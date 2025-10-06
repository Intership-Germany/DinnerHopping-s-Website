from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, Literal
from app import db as db_mod
from app.auth import get_current_user
from app.utils import require_event_published, require_event_registration_open, compute_team_diet, send_email, require_registration_owner_or_admin, get_registration_by_any_id, get_event
from bson.objectid import ObjectId
from bson.errors import InvalidId
import datetime
import os
from app.enums import Gender, DietaryPreference, CoursePreference, normalized_value

router = APIRouter()


def _require_exactly_one_partner(partner_existing, partner_external):
    if bool(partner_existing) == bool(partner_external):
        raise HTTPException(status_code=400, detail='exactly one of partner_existing or partner_external required')


def _enum_value(enum_cls, value, default=None):
    return normalized_value(enum_cls, value, default=default)


class SoloRegistrationIn(BaseModel):
    event_id: str
    dietary_preference: DietaryPreference | None = None
    kitchen_available: Optional[bool] = None
    main_course_possible: Optional[bool] = None
    course_preference: CoursePreference | None = None

    @field_validator('dietary_preference', mode='before')
    @classmethod
    def normalize_dietary(cls, value):
        return DietaryPreference.normalize(value)

    @field_validator('course_preference', mode='before')
    @classmethod
    def normalize_course(cls, value):
        return CoursePreference.normalize(value)


class TeamExistingUser(BaseModel):
    email: EmailStr


class TeamExternalPartner(BaseModel):
    name: str
    email: EmailStr
    gender: Gender | None = None
    dietary_preference: DietaryPreference | None = None
    field_of_study: Optional[str] = None
    kitchen_available: Optional[bool] = None
    main_course_possible: Optional[bool] = None

    @field_validator('gender', mode='before')
    @classmethod
    def normalize_gender(cls, value):
        if value is None:
            return None
        return Gender.normalize(value)

    @field_validator('dietary_preference', mode='before')
    @classmethod
    def normalize_dietary(cls, value):
        return DietaryPreference.normalize(value)


class TeamRegistrationIn(BaseModel):
    event_id: str
    partner_existing: Optional[TeamExistingUser] = None
    partner_external: Optional[TeamExternalPartner] = None
    # Which address hosts cooking ("creator" or "partner")
    cooking_location: Literal['creator','partner']
    # Creator overrides for this event
    dietary_preference: DietaryPreference | None = None
    kitchen_available: Optional[bool] = None
    main_course_possible: Optional[bool] = None
    # Team course preference
    course_preference: CoursePreference | None = None

    @field_validator('dietary_preference', mode='before')
    @classmethod
    def normalize_dietary(cls, value):
        return DietaryPreference.normalize(value)

    @field_validator('course_preference', mode='before')
    @classmethod
    def normalize_course(cls, value):
        return CoursePreference.normalize(value)


class ReplacePartnerExisting(BaseModel):
    email: EmailStr


class ReplacePartnerExternal(BaseModel):
    name: str
    email: EmailStr
    gender: Gender | None = None
    dietary_preference: DietaryPreference | None = None
    field_of_study: Optional[str] = None
    kitchen_available: Optional[bool] = None
    main_course_possible: Optional[bool] = None

    @field_validator('gender', mode='before')
    @classmethod
    def normalize_gender(cls, value):
        if value is None:
            return None
        return Gender.normalize(value)

    @field_validator('dietary_preference', mode='before')
    @classmethod
    def normalize_dietary(cls, value):
        return DietaryPreference.normalize(value)


class ReplacePartnerIn(BaseModel):
    partner_existing: Optional[ReplacePartnerExisting] = None
    partner_external: Optional[ReplacePartnerExternal] = None
    # Optional updated course preference / cooking location (cannot violate constraints)
    course_preference: CoursePreference | None = None
    cooking_location: Optional[Literal['creator','partner']] = None

    @field_validator('course_preference', mode='before')
    @classmethod
    def normalize_course(cls, value):
        return CoursePreference.normalize(value)


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


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


async def _reserve_capacity(ev: dict, team_size: int) -> None:
    """Increment event attendee_count while enforcing capacity limit."""
    if not ev or not isinstance(ev, dict):
        raise HTTPException(status_code=404, detail='Event not found')
    try:
        event_id = ev.get('_id')
        event_oid = event_id if isinstance(event_id, ObjectId) else ObjectId(event_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail='invalid event id') from exc

    inc = max(int(team_size or 0), 0)
    if inc == 0:
        return
    if ev.get('capacity') is not None:
        filter_query = {
            '_id': event_oid,
            '$expr': {
                '$lte': [
                    {'$add': [{'$ifNull': ['$attendee_count', 0]}, inc]},
                    '$capacity'
                ]
            }
        }
        result = await db_mod.db.events.update_one(filter_query, {'$inc': {'attendee_count': inc}})
        if result.modified_count == 0:
            raise HTTPException(status_code=400, detail='Event capacity full')
    else:
        await db_mod.db.events.update_one({'_id': event_oid}, {'$inc': {'attendee_count': inc}})


async def _release_capacity(event_id, team_size: int) -> None:
    try:
        event_oid = event_id if isinstance(event_id, ObjectId) else ObjectId(event_id)
    except (InvalidId, TypeError):
        return
    dec = max(int(team_size or 0), 0)
    if dec == 0:
        return
    await db_mod.db.events.update_one(
        {
            '_id': event_oid,
            '$expr': {
                '$gte': [{'$ifNull': ['$attendee_count', 0]}, dec]
            }
        },
        {'$inc': {'attendee_count': -dec}}
    )


@router.post('/solo')
async def register_solo(payload: SoloRegistrationIn, current_user=Depends(get_current_user)):
    ev = await _get_event_or_404(payload.event_id)

    # Pre-fill from profile, allow overrides per event
    creator = await _ensure_user(current_user['email'])
    if not creator:
        raise HTTPException(status_code=404, detail='User not found')
    
    # Check for existing active registrations (single-active-registration rule - Option A)
    # Active statuses: any status except cancelled
    cancelled_states = {'cancelled_by_user', 'cancelled_admin'}
    existing_active = await db_mod.db.registrations.find_one({
        'user_id': creator.get('_id'),
        'status': {'$nin': list(cancelled_states)},
    })
    
    # Allow re-registration for the same event, but block if active registration for a DIFFERENT event
    if existing_active and str(existing_active.get('event_id')) != str(ev['_id']):
        event_info = await db_mod.db.events.find_one({'_id': existing_active.get('event_id')})
        event_title = event_info.get('title') if event_info else 'another event'
        raise HTTPException(
            status_code=409,
            detail={
                'message': f'You already have an active registration for {event_title}. Please cancel that registration before registering for a new event.',
                'existing_registration': {
                    'registration_id': str(existing_active.get('_id')),
                    'event_id': str(existing_active.get('event_id')),
                    'event_title': event_title,
                    'status': existing_active.get('status'),
                },
            }
        )
    
    diet = (
        _enum_value(DietaryPreference, payload.dietary_preference)
        or _enum_value(DietaryPreference, creator.get('default_dietary_preference'))
        or 'omnivore'
    )
    kitchen_available = payload.kitchen_available if payload.kitchen_available is not None else bool(creator.get('kitchen_available'))
    main_possible = payload.main_course_possible if payload.main_course_possible is not None else bool(creator.get('main_course_possible'))
    course = _enum_value(CoursePreference, payload.course_preference)
    _validate_course_choice(course, main_possible)

    # Upsert registration per (event,user)
    now = _now()
    preferences = {
        'course_preference': course,
        'kitchen_available': kitchen_available,
        'main_course_possible': main_possible,
    }
    existing = await db_mod.db.registrations.find_one({'event_id': ev['_id'], 'user_email_snapshot': creator.get('email')})
    if existing and (existing.get('team_id') or existing.get('team_size', 1) != 1):
        raise HTTPException(status_code=400, detail='Already registered with a team for this event')

    needs_reserve = existing is None or (existing.get('status') in cancelled_states if existing else False)
    if needs_reserve:
        await _reserve_capacity(ev, 1)

    try:
        if existing:
            old_status = existing.get('status')
            update_fields = {
                'preferences': preferences,
                'diet': diet,
                'updated_at': now,
            }
            update_doc = {'$set': update_fields}
            if existing.get('status') in cancelled_states:
                update_fields['status'] = 'pending_payment'
                update_doc = {
                    '$set': update_fields,
                    '$unset': {'cancelled_at': ''}
                }
            await db_mod.db.registrations.update_one({'_id': existing['_id']}, update_doc)
            reg_id = existing['_id']
            
            # Audit log for status change
            if old_status in cancelled_states and update_fields.get('status') == 'pending_payment':
                from app.utils import create_audit_log
                await create_audit_log(
                    entity_type='registration',
                    entity_id=reg_id,
                    action='status_change',
                    actor=creator.get('email'),
                    old_state={'status': old_status},
                    new_state={'status': 'pending_payment'},
                    reason='Re-registration after cancellation'
                )
        else:
            reg_doc = {
                'event_id': ev['_id'],
                'user_id': creator.get('_id'),
                'user_email_snapshot': creator.get('email'),
                'team_id': None,
                'team_size': 1,
                'preferences': preferences,
                'diet': diet,
                'status': 'pending_payment',
                'created_at': now,
                'updated_at': now,
            }
            res = await db_mod.db.registrations.insert_one(reg_doc)
            reg_id = res.inserted_id
            
            # Audit log for creation
            from app.utils import create_audit_log, send_registration_notification
            await create_audit_log(
                entity_type='registration',
                entity_id=reg_id,
                action='created',
                actor=creator.get('email'),
                new_state={'status': 'pending_payment', 'team_size': 1},
                reason='Solo registration created'
            )
            
            # Send notification (best-effort, don't fail if it doesn't work)
            try:
                await send_registration_notification(reg_id, 'created')
            except Exception:
                pass  # Log but don't fail registration if notification fails
    except Exception:
        if needs_reserve:
            await _release_capacity(ev.get('_id'), 1)
        raise

    # Create payment (one person fee)
    # Payment amount per person comes from event.fee_cents; keep payments router logic for provider integration
    # Just return a pointer for client to call /payments
    return {
        'registration_id': str(reg_id),
        'team_size': 1,
        'amount_cents': int(ev.get('fee_cents') or 0),
        'payment_create_endpoint': '/payments/create',
        'registration_status': 'pending_payment',
    }


@router.post('/team')
async def register_team(payload: TeamRegistrationIn, current_user=Depends(get_current_user)):
    _require_exactly_one_partner(payload.partner_existing, payload.partner_external)

    ev = await _get_event_or_404(payload.event_id)
    creator = await _ensure_user(current_user['email'])
    if not creator:
        raise HTTPException(status_code=404, detail='User not found')

    # Check for existing active registrations (single-active-registration rule - Option A)
    cancelled_states = {'cancelled_by_user', 'cancelled_admin'}
    existing_creator_active = await db_mod.db.registrations.find_one({
        'user_id': creator.get('_id'),
        'status': {'$nin': list(cancelled_states)},
    })
    
    # Block if active registration for a DIFFERENT event
    if existing_creator_active and str(existing_creator_active.get('event_id')) != str(ev['_id']):
        event_info = await db_mod.db.events.find_one({'_id': existing_creator_active.get('event_id')})
        event_title = event_info.get('title') if event_info else 'another event'
        raise HTTPException(
            status_code=409,
            detail={
                'message': f'You already have an active registration for {event_title}. Please cancel that registration before registering for a new event.',
                'existing_registration': {
                    'registration_id': str(existing_creator_active.get('_id')),
                    'event_id': str(existing_creator_active.get('event_id')),
                    'event_title': event_title,
                    'status': existing_creator_active.get('status'),
                },
            }
        )

    active_filter = {'$nin': list(cancelled_states)}
    existing_creator_reg = await db_mod.db.registrations.find_one({
        'event_id': ev['_id'],
        'user_email_snapshot': creator.get('email'),
        'status': active_filter,
    })
    if existing_creator_reg:
        raise HTTPException(status_code=400, detail='Already registered for this event')

    # Resolve partner
    partner_user = None
    partner_external_info = None
    if payload.partner_existing:
        partner_user = await _ensure_user(payload.partner_existing.email)
        if not partner_user:
            raise HTTPException(status_code=404, detail='Invited user not found')
        if str(partner_user.get('_id')) == str(creator.get('_id')):
            raise HTTPException(status_code=400, detail='Cannot invite yourself as partner')
        
        # Check if partner has active registration for a different event
        existing_partner_active = await db_mod.db.registrations.find_one({
            'user_id': partner_user.get('_id'),
            'status': {'$nin': list(cancelled_states)},
        })
        if existing_partner_active and str(existing_partner_active.get('event_id')) != str(ev['_id']):
            partner_event_info = await db_mod.db.events.find_one({'_id': existing_partner_active.get('event_id')})
            partner_event_title = partner_event_info.get('title') if partner_event_info else 'another event'
            raise HTTPException(
                status_code=409,
                detail=f'Your partner already has an active registration for {partner_event_title}. They must cancel that registration first.'
            )
        
        existing_partner_reg = await db_mod.db.registrations.find_one({
            'event_id': ev['_id'],
            'user_email_snapshot': partner_user.get('email'),
            'status': active_filter,
        })
        if existing_partner_reg:
            raise HTTPException(status_code=400, detail='Partner already registered for this event')
        # Auto-register invited user and notify via email; allow decline via separate endpoint
        # We'll link them into the same team
    else:
        # External partner: store minimal snapshot in team doc
        partner_external_info = payload.partner_external.model_dump()
        partner_external_info['dietary_preference'] = _enum_value(DietaryPreference, partner_external_info.get('dietary_preference'))
        partner_external_info['gender'] = _enum_value(Gender, partner_external_info.get('gender'))

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
    normalized_course = _enum_value(CoursePreference, payload.course_preference)
    _validate_course_choice(normalized_course, chosen_location_main)

    # Team dietary: precedence Vegan > Vegetarian > Omnivore
    creator_diet = (
        _enum_value(DietaryPreference, payload.dietary_preference)
        or _enum_value(DietaryPreference, creator.get('default_dietary_preference'))
        or 'omnivore'
    )
    partner_diet = None
    if partner_user:
        partner_diet = _enum_value(DietaryPreference, partner_user.get('default_dietary_preference')) or 'omnivore'
    elif partner_external_info:
        partner_diet = partner_external_info.get('dietary_preference') or 'omnivore'
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
        'course_preference': normalized_course,
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
            'diet': _enum_value(DietaryPreference, partner_user.get('default_dietary_preference')) or 'omnivore',
        })
    else:
        team_doc['members'].append({
            'type': 'external',
            'name': partner_external_info.get('name'),
            'email': partner_external_info.get('email').lower(),
            'gender': _enum_value(Gender, partner_external_info.get('gender')),
            'diet': _enum_value(DietaryPreference, partner_external_info.get('dietary_preference')) or 'omnivore',
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

    await _reserve_capacity(ev, 2)

    try:
        # Insert team
        team_res = await db_mod.db.teams.insert_one(team_doc)
        team_id = team_res.inserted_id

        # Create registrations for creator and partner (auto-register partner if existing user)
        reg_common = {
            'event_id': ev['_id'],
            'team_id': team_id,
            'team_size': 2,
            'preferences': {
                'course_preference': normalized_course,
                'cooking_location': payload.cooking_location,
            },
            'diet': team_diet,
            'status': 'pending_payment',
            'created_at': now,
            'updated_at': now,
        }
        # creator registration (owner)
        reg_creator = reg_common | {'user_id': creator.get('_id'), 'user_email_snapshot': creator.get('email')}
        reg_creator_res = await db_mod.db.registrations.insert_one(reg_creator)
        reg_creator_id = reg_creator_res.inserted_id
        
        # Audit log for creator registration
        from app.utils import create_audit_log
        await create_audit_log(
            entity_type='registration',
            entity_id=reg_creator_id,
            action='created',
            actor=creator.get('email'),
            new_state={'status': 'pending_payment', 'team_size': 2, 'team_id': str(team_id)},
            reason='Team registration created (creator)'
        )

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
            _ = await send_email(
                to=partner_user.get('email'),
                subject=subject,
                body=body,
                category='team_invitation',
                template_vars={'event_title': ev.get('title'), 'decline_link': decline_link, 'email': partner_user.get('email')}
            )
        else:
            # External partner: no user account, no auto-registration. Store snapshot only.
            reg_partner_id = None
    except Exception:
        await _release_capacity(ev.get('_id'), 2)
        raise
    
    # increment attendee_count for the newly created registrations
    inc_count = 1 if partner_user else 1  # Always 1 for creator, partner was already counted in try block
    if partner_user and reg_partner_id:
        inc_count = 2
    
    try:
        await db_mod.db.events.update_one({'_id': ev['_id']}, {'$inc': {'attendee_count': inc_count}})
    except Exception:
        pass
    
    # Audit log for partner registration (if existing user)
    if partner_user and reg_partner_id:
        from app.utils import create_audit_log
        await create_audit_log(
            entity_type='registration',
            entity_id=reg_partner_id,
            action='created',
            actor=creator.get('email'),
            new_state={'status': 'invited', 'team_size': 2, 'team_id': str(team_id)},
            reason='Team registration created (invited partner)'
        )

    # Return team and payment info (single payment for €10 i.e., 2x fee)
    team_amount = int(ev.get('fee_cents') or 0) * 2
    return {
        'team_id': str(team_id),
        'registration_id': str(reg_creator_id),
        'partner_registration_id': str(reg_partner_id) if reg_partner_id else None,
        'team_size': 2,
        'amount_cents': team_amount,
        'payment_create_endpoint': '/payments/create',
        'registration_status': 'pending_payment',
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
    ev = await db_mod.db.events.find_one({'_id': team.get('event_id')}) if team.get('event_id') else None
    if not ev:
        raise HTTPException(status_code=404, detail='Event not found')
    if _cancellation_deadline_passed(ev):
        raise HTTPException(status_code=400, detail='Cancellation deadline passed')
    # Only the partner (non-creator) can decline
    members = team.get('members') or []
    # Find member matching current_user
    match = next((m for m in members if m.get('email') == current_user.get('email') and m.get('user_id') != team.get('created_by_user_id')), None)
    if not match:
        raise HTTPException(status_code=403, detail='Only invited partner can decline')
    # mark team cancelled and cancel related partner registration if exists
    now = _now()
    await db_mod.db.teams.update_one({'_id': tid}, {'$set': {'status': 'cancelled', 'cancelled_by': current_user.get('email'), 'cancelled_at': now}})
    # set partner registration cancelled_by_user and decrement attendee_count accordingly
    res = await db_mod.db.registrations.update_many({'team_id': tid, 'user_email_snapshot': current_user.get('email')}, {'$set': {'status': 'cancelled_by_user', 'updated_at': now}})
    try:
        if getattr(res, 'modified_count', 0) > 0:
            await db_mod.db.events.update_one({'_id': team.get('event_id'), 'attendee_count': {'$gte': res.modified_count}}, {'$inc': {'attendee_count': -res.modified_count}})
    except Exception:
        pass
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


# ------------- CANCELLATIONS & REPLACEMENTS (Phase 2.5) -------------

async def _load_registration_or_404(registration_id: str) -> dict:
    if registration_id is None or (isinstance(registration_id, str) and not registration_id.strip()):
        raise HTTPException(status_code=400, detail='invalid registration id')
    reg = await get_registration_by_any_id(registration_id)
    if not reg:
        raise HTTPException(status_code=404, detail='Registration not found')
    return reg


async def _load_event_for_registration(reg: dict) -> dict:
    ev = await db_mod.db.events.find_one({'_id': reg.get('event_id')}) if reg.get('event_id') else None
    if not ev:
        raise HTTPException(status_code=404, detail='Event not found')
    return ev


def _cancellation_deadline_passed(ev: dict) -> bool:
    ddl = ev.get('registration_deadline') or ev.get('payment_deadline')
    if not ddl:
        return False
    deadline_dt = None
    # If it's already a datetime, ensure it's timezone-aware (assume UTC when naive)
    if isinstance(ddl, datetime.datetime):
        deadline_dt = ddl if ddl.tzinfo is not None else ddl.replace(tzinfo=datetime.timezone.utc)
    elif isinstance(ddl, str):
        try:
            from app import datetime_utils
            deadline_dt = datetime_utils.parse_iso(ddl)
        except Exception:
            # If parsing fails, be conservative and treat as no deadline
            return False

    if not deadline_dt:
        return False

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    # both should be timezone-aware now; compare safely
    if isinstance(deadline_dt, datetime.datetime) and deadline_dt.tzinfo is None:
        deadline_dt = deadline_dt.replace(tzinfo=datetime.timezone.utc)
    return now_utc > deadline_dt


async def _mark_refund_if_applicable(reg: dict, ev: dict):
    if not ev.get('refund_enabled'):
        return
    # mark payment doc for later refund processing if exists and succeeded
    pay_id = reg.get('payment_id')
    if not pay_id:
        return
    try:
        pay_oid = pay_id if isinstance(pay_id, ObjectId) else ObjectId(pay_id)
    except (InvalidId, TypeError):
        return
    pay = await db_mod.db.payments.find_one({'_id': pay_oid})
    if not pay or pay.get('status') not in ('succeeded', 'paid'):
        return
    await db_mod.db.payments.update_one({'_id': pay_oid}, {'$set': {'refund_requested': True, 'refund_requested_at': datetime.datetime.now(datetime.timezone.utc)}})


@router.delete('/{registration_id}')
async def cancel_solo_registration(registration_id: str, current_user=Depends(get_current_user)):
    """Cancel a solo registration (team_size == 1) before the deadline.

    If refund is enabled and payment succeeded, mark payment for refund processing.
    Idempotent: repeated calls after cancellation return current status.
    """
    reg = await require_registration_owner_or_admin(current_user, registration_id)
    if reg.get('team_size') != 1:
        raise HTTPException(status_code=400, detail='Use team cancellation endpoints for team registrations')
    ev = await _load_event_for_registration(reg)
    if _cancellation_deadline_passed(ev):
        raise HTTPException(status_code=400, detail='Cancellation deadline passed')
    # If already cancelled, return current state
    if reg.get('status') in ('cancelled_by_user', 'cancelled_admin'):
        return {'status': reg.get('status')}
    
    old_status = reg.get('status')
    now = datetime.datetime.now(datetime.timezone.utc)
    await db_mod.db.registrations.update_one({'_id': reg['_id']}, {'$set': {'status': 'cancelled_by_user', 'updated_at': now, 'cancelled_at': now}})
    await _release_capacity(reg.get('event_id'), 1)
    await _mark_refund_if_applicable(reg, ev)
    
    # Audit log for cancellation
    from app.utils import create_audit_log
    await create_audit_log(
        entity_type='registration',
        entity_id=registration_id,
        action='cancelled',
        actor=current_user.get('email'),
        old_state={'status': old_status},
        new_state={'status': 'cancelled_by_user'},
        reason='User-initiated cancellation'
    )
    
    # email best-effort
    if reg.get('user_email_snapshot'):
        _ = await send_email(
            to=reg['user_email_snapshot'],
            subject=f'Cancellation confirmed for {ev.get("title")}',
            body='Your registration has been cancelled. If eligible, a refund will be processed later.',
            category='cancellation',
            template_vars={'event_title': ev.get('title'), 'refund': False, 'email': reg.get('user_email_snapshot')}
        )
    return {'status': 'cancelled_by_user'}


@router.post('/teams/{team_id}/members/{registration_id}/cancel')
async def cancel_team_member(team_id: str, registration_id: str, current_user=Depends(get_current_user)):
    """A team member (non-creator) cancels themselves. Team becomes incomplete.

    Remaining creator will be notified and can replace partner or cancel team.
    """
    try:
        tid = ObjectId(team_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail='invalid team id') from exc
    team = await db_mod.db.teams.find_one({'_id': tid})
    if not team:
        raise HTTPException(status_code=404, detail='Team not found')
    reg = await _load_registration_or_404(registration_id)
    if str(reg.get('team_id')) != str(tid):
        raise HTTPException(status_code=400, detail='Registration not in team')
    # Ensure current user matches the registration being cancelled and is not the creator
    if str(reg.get('user_id')) != str(current_user.get('_id')):
        raise HTTPException(status_code=403, detail='Forbidden')
    if team.get('created_by_user_id') == reg.get('user_id'):
        raise HTTPException(status_code=400, detail='Creator cannot cancel with this endpoint')
    ev = await _load_event_for_registration(reg)
    if _cancellation_deadline_passed(ev):
        raise HTTPException(status_code=400, detail='Cancellation deadline passed')
    # Already cancelled? idempotent
    if reg.get('status') in ('cancelled_by_user', 'cancelled_admin'):
        return {'status': reg.get('status')}
    now = datetime.datetime.now(datetime.timezone.utc)
    await db_mod.db.registrations.update_one({'_id': reg['_id']}, {'$set': {'status': 'cancelled_by_user', 'updated_at': now, 'cancelled_at': now}})
    # Mark team incomplete (custom status) without affecting payment (single payment stays)
    await db_mod.db.teams.update_one({'_id': tid}, {'$set': {'status': 'incomplete', 'updated_at': now}})
    # decrement attendee_count for this cancelled member
    try:
        await db_mod.db.events.update_one({'_id': ev.get('_id'), 'attendee_count': {'$gt': 0}}, {'$inc': {'attendee_count': -1}})
    except Exception:
        pass
    # Notify creator
    creator_reg = await db_mod.db.registrations.find_one({'team_id': tid, 'user_id': team.get('created_by_user_id')})
    if creator_reg and creator_reg.get('user_email_snapshot'):
        base = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
        replace_url = f"{base}/registrations/teams/{team_id}/replace"
        body = (
            f"Your partner has cancelled for event '{ev.get('title')}'.\n"
            f"You may invite a replacement via: {replace_url}\n"
            "If you do nothing, the team may be excluded during matching if incomplete."
        )
        _ = await send_email(
            to=creator_reg['user_email_snapshot'],
            subject='Team partner cancelled',
            body=body,
            category='team_cancellation',
            template_vars={'event_title': ev.get('title'), 'email': creator_reg['user_email_snapshot']}
        )
    return {'status': 'cancelled_by_user', 'team_status': 'incomplete'}


@router.post('/teams/{team_id}/replace')
async def replace_team_partner(team_id: str, payload: ReplacePartnerIn, current_user=Depends(get_current_user)):
    """Replace a cancelled partner before the deadline with an existing user or external partner.

    Constraints: Only team creator may call; team must be in status 'incomplete'.
    """
    try:
        tid = ObjectId(team_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail='invalid team id') from exc
    team = await db_mod.db.teams.find_one({'_id': tid})
    if not team:
        raise HTTPException(status_code=404, detail='Team not found')
    if team.get('created_by_user_id') != current_user.get('_id'):
        raise HTTPException(status_code=403, detail='Only creator can replace partner')
    if team.get('status') != 'incomplete':
        raise HTTPException(status_code=400, detail='Team not incomplete')
    # Ensure there is exactly one active member (creator) and one cancelled registration
    active_regs = []
    cancelled_regs = []
    async for r in db_mod.db.registrations.find({'team_id': tid}):
        if r.get('status') in ('cancelled_by_user', 'cancelled_admin'):
            cancelled_regs.append(r)
        else:
            active_regs.append(r)
    if len(active_regs) != 1 or len(cancelled_regs) != 1:
        raise HTTPException(status_code=400, detail='Invalid team state for replacement')
    creator_reg = active_regs[0]
    ev = await _load_event_for_registration(creator_reg)
    if _cancellation_deadline_passed(ev):
        raise HTTPException(status_code=400, detail='Replacement deadline passed')
    # Validate payload
    _require_exactly_one_partner(payload.partner_existing, payload.partner_external)
    # Build new member snapshot
    now = datetime.datetime.now(datetime.timezone.utc)
    partner_user = None
    partner_external_info = None
    if payload.partner_existing:
        partner_user = await _ensure_user(payload.partner_existing.email)
        if not partner_user:
            raise HTTPException(status_code=404, detail='Invited user not found')
        member_snapshot = {
            'type': 'user',
            'user_id': partner_user.get('_id'),
            'email': partner_user.get('email'),
            'kitchen_available': bool(partner_user.get('kitchen_available')),
            'main_course_possible': bool(partner_user.get('main_course_possible')),
            'diet': _enum_value(DietaryPreference, partner_user.get('default_dietary_preference')) or 'omnivore',
        }
    else:
        partner_external_info = payload.partner_external.model_dump()
        member_snapshot = {
            'type': 'external',
            'name': partner_external_info.get('name'),
            'email': partner_external_info.get('email').lower(),
            'gender': _enum_value(Gender, partner_external_info.get('gender')),
            'diet': _enum_value(DietaryPreference, partner_external_info.get('dietary_preference')) or 'omnivore',
            'field_of_study': partner_external_info.get('field_of_study'),
            'kitchen_available': bool(partner_external_info.get('kitchen_available')),
            'main_course_possible': bool(partner_external_info.get('main_course_possible')),
        }
    # Update team members replacing cancelled one
    members = team.get('members') or []
    # Replace the non-creator entry
    new_members = []
    for m in members:
        if m.get('user_id') == team.get('created_by_user_id'):
            new_members.append(m)
        else:
            # assume this was cancelled partner; replace
            new_members.append(member_snapshot)
    team_course_pref = _enum_value(CoursePreference, team.get('course_preference'))
    if payload.course_preference is not None:
        team_course_pref = _enum_value(CoursePreference, payload.course_preference) or team_course_pref
    cooking_location = team.get('cooking_location')
    if payload.cooking_location:
        cooking_location = payload.cooking_location
    # Validate main course constraint if changed
    if team_course_pref == 'main':
        if cooking_location == 'creator':
            # ensure creator still main_course_possible
            if not any(m.get('user_id') == team.get('created_by_user_id') and m.get('main_course_possible') for m in new_members):
                raise HTTPException(status_code=400, detail='Creator cannot host main course')
        else:
            # partner side
            partner_entry = next((m for m in new_members if m is not None and m is not members[0]), None)
            if not partner_entry or not partner_entry.get('main_course_possible'):
                raise HTTPException(status_code=400, detail='Replacement partner cannot host main course')
    # Compute new team diet
    creator_diet_raw = next((m.get('diet') for m in new_members if m.get('user_id') == team.get('created_by_user_id')), 'omnivore')
    partner_diet_raw = next((m.get('diet') for m in new_members if m.get('user_id') != team.get('created_by_user_id')), 'omnivore')
    creator_diet = _enum_value(DietaryPreference, creator_diet_raw) or 'omnivore'
    partner_diet = _enum_value(DietaryPreference, partner_diet_raw) or 'omnivore'
    team_diet = compute_team_diet(creator_diet, partner_diet)
    await db_mod.db.teams.update_one({'_id': tid}, {'$set': {
        'members': new_members,
        'status': 'pending',  # back to normal pending status
        'updated_at': now,
    'course_preference': team_course_pref,
        'cooking_location': cooking_location,
        'team_diet': team_diet,
    }})
    # Create registration for replacement partner if internal user
    if partner_user:
        reg_common = {
            'event_id': ev['_id'],
            'team_id': tid,
            'team_size': 2,
            'preferences': {
                'course_preference': team_course_pref,
                'cooking_location': cooking_location,
            },
            'diet': team_diet,
            'status': 'pending',
            'created_at': now,
            'updated_at': now,
        }
        await db_mod.db.registrations.insert_one(reg_common | {'user_id': partner_user.get('_id'), 'user_email_snapshot': partner_user.get('email')})
        # increment attendee_count for the new partner
        try:
            await db_mod.db.events.update_one({'_id': ev['_id']}, {'$inc': {'attendee_count': 1}})
        except Exception:
            pass
        # Notify partner
        base = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
        subject = 'You have been added to a DinnerHopping team (replacement)'
        body = f"Hi,\n\nYou were added as a replacement partner for event '{ev.get('title')}'.\nIf you cannot participate, you can cancel from your dashboard.\n\nThanks,\nDinnerHopping Team"
        _ = await send_email(to=partner_user.get('email'), subject=subject, body=body, category='team_replacement')
    return {'status': 'replaced', 'team_status': 'pending'}

@router.get('/registration-status')
async def registration_status(registration_id: str | None = None, current_user=Depends(get_current_user)):
    """Return the current user's registration(s) and linked payment status.

    If `registration_id` is provided the endpoint returns the single registration
    (authorization enforced). Otherwise returns all registrations for the user (users are not supposed to have multiple registrations).
    """
    regs = []
    if registration_id:
        # Lookup and enforce owner/admin rights
        reg = await get_registration_by_any_id(registration_id)
        if not reg:
            raise HTTPException(status_code=404, detail='Registration not found')
        # require owner or admin
        reg = await require_registration_owner_or_admin(current_user, reg.get('_id'))
        regs = [reg]
    else:
        # fetch all registrations where the user is owner (by id) or snapshot email
        query = {'$or': [{'user_id': current_user.get('_id')}, {'user_email_snapshot': (current_user.get('email') or '').lower()}]}
        async for r in db_mod.db.registrations.find(query):
            regs.append(r)

    out = []
    for r in regs:
        event_title = None
        ev = None
        if r.get('event_id'):
            try:
                ev = await get_event(r.get('event_id'))
            except Exception:
                ev = None
        if ev:
            event_title = ev.get('title')

        payment_summary = None
        pay_id = r.get('payment_id')
        if pay_id:
            # try to resolve payment by ObjectId or raw id
            pay = None
            try:
                pay_oid = pay_id if isinstance(pay_id, ObjectId) else ObjectId(pay_id)
            except (InvalidId, TypeError):
                pay = await db_mod.db.payments.find_one({'_id': pay_id})
            else:
                pay = await db_mod.db.payments.find_one({'_id': pay_oid})

            if pay:
                amount_cents = None
                if pay.get('amount') is not None:
                    try:
                        amount_cents = int(round((pay.get('amount') or 0) * 100))
                    except Exception:
                        amount_cents = None
                payment_summary = {
                    'payment_id': str(pay.get('_id')),
                    'status': pay.get('status'),
                    'provider': pay.get('provider'),
                    'amount_cents': amount_cents,
                    'payment_link': pay.get('payment_link'),
                }

        # Compute canonical amount from event if available
        amount_due_cents = None
        try:
            if ev:
                fee = int(ev.get('fee_cents') or 0)
                ts = int(r.get('team_size') or 1)
                amount_due_cents = fee * ts
        except Exception:
            amount_due_cents = None

        out.append({
            'registration_id': str(r.get('_id')),
            'event_id': str(r.get('event_id')) if r.get('event_id') else None,
            'event_title': event_title,
            'status': r.get('status'),
            'team_id': str(r.get('team_id')) if r.get('team_id') else None,
            'team_size': int(r.get('team_size') or 1),
            'amount_due_cents': amount_due_cents,
            'payment': payment_summary,
            'created_at': r.get('created_at'),
            'paid_at': r.get('paid_at'),
        })

    return {'registrations': out}
