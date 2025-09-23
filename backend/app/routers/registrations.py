from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from typing import Optional, Literal
from app import db as db_mod
from app.auth import get_current_user
from app.utils import require_event_published, require_event_registration_open, compute_team_diet, send_email
from bson.objectid import ObjectId
from bson.errors import InvalidId
import datetime
import os

router = APIRouter()


class SoloRegistrationIn(BaseModel):
    event_id: str
    dietary_preference: Optional[Literal['vegan','vegetarian','omnivore']] = None
    kitchen_available: Optional[bool] = None
    main_course_possible: Optional[bool] = None
    course_preference: Optional[Literal['appetizer','main','dessert']] = None


class TeamExistingUser(BaseModel):
    email: EmailStr


class TeamExternalPartner(BaseModel):
    name: str
    email: EmailStr
    gender: Optional[Literal['female','male','diverse','prefer_not_to_say']] = None
    dietary_preference: Optional[Literal['vegan','vegetarian','omnivore']] = None
    field_of_study: Optional[str] = None
    kitchen_available: Optional[bool] = None
    main_course_possible: Optional[bool] = None


class TeamRegistrationIn(BaseModel):
    event_id: str
    partner_existing: Optional[TeamExistingUser] = None
    partner_external: Optional[TeamExternalPartner] = None
    # Which address hosts cooking ("creator" or "partner")
    cooking_location: Literal['creator','partner']
    # Creator overrides for this event
    dietary_preference: Optional[Literal['vegan','vegetarian','omnivore']] = None
    kitchen_available: Optional[bool] = None
    main_course_possible: Optional[bool] = None
    # Team course preference
    course_preference: Optional[Literal['appetizer','main','dessert']] = None


def _now():
    return datetime.datetime.utcnow()


async def _get_event_or_404(event_id: str) -> dict:
    try:
        oid = ObjectId(event_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail='invalid event_id') from exc
    ev = await db_mod.db.events.find_one({'_id': oid})
    if not ev:
        raise HTTPException(status_code=404, detail='Event not found')
    await require_event_published(oid)
    require_event_registration_open(ev)
    return ev


def _validate_course_choice(course: Optional[str], main_possible: Optional[bool]):
    if course == 'main' and not main_possible:
        raise HTTPException(status_code=400, detail='Main Course requires main_course_possible at chosen location')


async def _ensure_user(email: str) -> Optional[dict]:
    return await db_mod.db.users.find_one({'email': email.lower()})


@router.post('/solo')
async def register_solo(payload: SoloRegistrationIn, current_user=Depends(get_current_user)):
    ev = await _get_event_or_404(payload.event_id)

    # Pre-fill from profile, allow overrides per event
    creator = await _ensure_user(current_user['email'])
    diet = (payload.dietary_preference or creator.get('default_dietary_preference') or 'omnivore').lower()
    kitchen_available = payload.kitchen_available if payload.kitchen_available is not None else bool(creator.get('kitchen_available'))
    main_possible = payload.main_course_possible if payload.main_course_possible is not None else bool(creator.get('main_course_possible'))
    course = (payload.course_preference or '').lower() or None
    _validate_course_choice(course, main_possible)

    # Upsert registration per (event,user)
    now = _now()
    reg_doc = {
        'event_id': ev['_id'],
        'user_id': creator.get('_id'),
        'user_email_snapshot': creator.get('email'),
        'team_id': None,
        'team_size': 1,
        'preferences': {
            'course_preference': course,
            'kitchen_available': kitchen_available,
            'main_course_possible': main_possible,
        },
        'diet': diet,
        'status': 'pending',
        'created_at': now,
        'updated_at': now,
    }
    existing = await db_mod.db.registrations.find_one({'event_id': ev['_id'], 'user_email_snapshot': creator.get('email')})
    if existing:
        # update overrides if re-registering
        await db_mod.db.registrations.update_one({'_id': existing['_id']}, {'$set': {'preferences': reg_doc['preferences'], 'diet': diet, 'updated_at': now}})
        reg_id = existing['_id']
    else:
        res = await db_mod.db.registrations.insert_one(reg_doc)
        reg_id = res.inserted_id

    # Create payment (one person fee)
    # Payment amount per person comes from event.fee_cents; keep payments router logic for provider integration
    # Just return a pointer for client to call /payments/create
    return {
        'registration_id': str(reg_id),
        'team_size': 1,
        'amount_cents': int(ev.get('fee_cents') or 0),
        'payment_create_endpoint': '/payments/create',
    }


@router.post('/team')
async def register_team(payload: TeamRegistrationIn, current_user=Depends(get_current_user)):
    if not payload.partner_existing and not payload.partner_external:
        raise HTTPException(status_code=400, detail='Provide partner_existing or partner_external')
    if payload.partner_existing and payload.partner_external:
        raise HTTPException(status_code=400, detail='Provide only one partner type')

    ev = await _get_event_or_404(payload.event_id)
    creator = await _ensure_user(current_user['email'])

    # Resolve partner
    partner_user = None
    partner_external_info = None
    if payload.partner_existing:
        partner_user = await _ensure_user(payload.partner_existing.email)
        if not partner_user:
            raise HTTPException(status_code=404, detail='Invited user not found')
        # Auto-register invited user and notify via email; allow decline via separate endpoint
        # We'll link them into the same team
    else:
        # External partner: store minimal snapshot in team doc
        partner_external_info = payload.partner_external.model_dump()

    # Compute per-person overrides for creator and partner snapshot
    creator_kitchen = payload.kitchen_available if payload.kitchen_available is not None else bool(creator.get('kitchen_available'))
    creator_main = payload.main_course_possible if payload.main_course_possible is not None else bool(creator.get('main_course_possible'))
    chosen_location_main = None
    if payload.cooking_location == 'creator':
        chosen_location_main = creator_main
    else:
        # partner side: external may specify, existing user pulled from profile
        if partner_user:
            chosen_location_main = bool(partner_user.get('main_course_possible'))
        else:
            chosen_location_main = bool(partner_external_info.get('main_course_possible')) if partner_external_info else False
    _validate_course_choice(payload.course_preference, chosen_location_main)

    # Team dietary: precedence Vegan > Vegetarian > Omnivore
    creator_diet = (payload.dietary_preference or creator.get('default_dietary_preference') or 'omnivore').lower()
    partner_diet = None
    if partner_user:
        partner_diet = (partner_user.get('default_dietary_preference') or 'omnivore').lower()
    elif partner_external_info:
        partner_diet = (partner_external_info.get('dietary_preference') or 'omnivore').lower()
    team_diet = compute_team_diet(creator_diet, partner_diet)

    # Create team document
    now = _now()
    team_doc = {
        'event_id': ev['_id'],
        'created_by_user_id': creator.get('_id'),
        'members': [
            {
                'type': 'user',
                'user_id': creator.get('_id'),
                'email': creator.get('email'),
                'kitchen_available': creator_kitchen,
                'main_course_possible': creator_main,
                'diet': creator_diet,
            }
        ],
        'cooking_location': payload.cooking_location,  # 'creator' | 'partner'
        'course_preference': payload.course_preference,
        'team_diet': team_diet,
        'created_at': now,
        'updated_at': now,
        'status': 'pending',
    }
    if partner_user:
        team_doc['members'].append({
            'type': 'user',
            'user_id': partner_user.get('_id'),
            'email': partner_user.get('email'),
            'kitchen_available': bool(partner_user.get('kitchen_available')),
            'main_course_possible': bool(partner_user.get('main_course_possible')),
            'diet': (partner_user.get('default_dietary_preference') or 'omnivore').lower(),
        })
    else:
        team_doc['members'].append({
            'type': 'external',
            'name': partner_external_info.get('name'),
            'email': partner_external_info.get('email').lower(),
            'gender': partner_external_info.get('gender'),
            'diet': (partner_external_info.get('dietary_preference') or 'omnivore').lower(),
            'field_of_study': partner_external_info.get('field_of_study'),
            'kitchen_available': bool(partner_external_info.get('kitchen_available')),
            'main_course_possible': bool(partner_external_info.get('main_course_possible')),
        })

    # Validate at least one kitchen available
    if not any(bool(m.get('kitchen_available')) for m in team_doc['members']):
        raise HTTPException(status_code=400, detail='At least one kitchen must be available in the team')

    # Ensure main course rule on chosen location
    if team_doc.get('course_preference') == 'main':
        if team_doc.get('cooking_location') == 'creator' and not creator_main:
            raise HTTPException(status_code=400, detail='Main Course requires creator main_course_possible')
        if team_doc.get('cooking_location') == 'partner':
            partner_main = bool(team_doc['members'][1].get('main_course_possible'))
            if not partner_main:
                raise HTTPException(status_code=400, detail='Main Course requires partner main_course_possible')

    # Insert team
    team_res = await db_mod.db.teams.insert_one(team_doc)
    team_id = team_res.inserted_id

    # Create registrations for creator and partner (auto-register partner if existing user)
    reg_common = {
        'event_id': ev['_id'],
        'team_id': team_id,
        'team_size': 2,
        'preferences': {
            'course_preference': payload.course_preference,
            'cooking_location': payload.cooking_location,
        },
        'diet': team_diet,
        'status': 'pending',
        'created_at': now,
        'updated_at': now,
    }
    # creator registration (owner)
    reg_creator = reg_common | {'user_id': creator.get('_id'), 'user_email_snapshot': creator.get('email')}
    reg_creator_res = await db_mod.db.registrations.insert_one(reg_creator)
    reg_creator_id = reg_creator_res.inserted_id

    if partner_user:
        reg_partner = reg_common | {'user_id': partner_user.get('_id'), 'user_email_snapshot': partner_user.get('email'), 'status': 'invited'}
        reg_partner_res = await db_mod.db.registrations.insert_one(reg_partner)
        reg_partner_id = reg_partner_res.inserted_id
        # Notify partner via email with decline link
        base = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
        decline_link = f"{base}/registrations/teams/{team_id}/decline"
        subject = 'You have been added to a DinnerHopping team'
        body = (
            f"Hi,\n\nYou were added to a team for event '{ev.get('title')}'. If you cannot participate, you can decline here:\n{decline_link}\n\nThanks,\nDinnerHopping Team"
        )
        # best-effort notification
        _ = await send_email(to=partner_user.get('email'), subject=subject, body=body, category='team_invitation')
    else:
        # External partner: no user account, no auto-registration. Store snapshot only.
        reg_partner_id = None

    # Return team and payment info (single payment for €10 i.e., 2x fee)
    team_amount = int(ev.get('fee_cents') or 0) * 2
    return {
        'team_id': str(team_id),
        'registration_id': str(reg_creator_id),
        'partner_registration_id': str(reg_partner_id) if reg_partner_id else None,
        'team_size': 2,
        'amount_cents': team_amount,
        'payment_create_endpoint': '/payments/create',
    }


@router.post('/teams/{team_id}/decline')
async def decline_team(team_id: str, current_user=Depends(get_current_user)):
    try:
        tid = ObjectId(team_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail='invalid team id') from exc
    team = await db_mod.db.teams.find_one({'_id': tid})
    if not team:
        raise HTTPException(status_code=404, detail='Team not found')
    # Only the partner (non-creator) can decline
    members = team.get('members') or []
    # Find member matching current_user
    match = next((m for m in members if m.get('email') == current_user.get('email') and m.get('user_id') != team.get('created_by_user_id')), None)
    if not match:
        raise HTTPException(status_code=403, detail='Only invited partner can decline')
    # mark team cancelled and cancel related partner registration if exists
    now = _now()
    await db_mod.db.teams.update_one({'_id': tid}, {'$set': {'status': 'cancelled', 'cancelled_by': current_user.get('email'), 'cancelled_at': now}})
    # set partner registration cancelled_by_user
    await db_mod.db.registrations.update_many({'team_id': tid, 'user_email_snapshot': current_user.get('email')}, {'$set': {'status': 'cancelled_by_user', 'updated_at': now}})
    return {'status': 'declined'}


@router.get('/teams/{team_id}/decline')
async def decline_team_get(team_id: str, current_user=Depends(get_current_user)):
    # Delegate to POST handler for logic
    return await decline_team(team_id, current_user)


@router.get('/events/active')
async def list_active_events(current_user=Depends(get_current_user)):
    # Convenience endpoint for overview of active events
    # touch current_user to avoid unused warnings (authorization could be added later)
    _ = current_user
    query = {'status': 'published'}
    out = []
    async for e in db_mod.db.events.find(query).sort([('start_at', 1)]):
        out.append({'id': str(e.get('_id')), 'title': e.get('title'), 'date': e.get('date'), 'start_at': e.get('start_at'), 'fee_cents': e.get('fee_cents', 0)})
    return out
