from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, Literal
from app import db as db_mod
from app.auth import get_current_user
from app.utils import require_event_published, require_event_registration_open, compute_team_diet, send_email, require_registration_owner_or_admin, get_registration_by_any_id, get_event, create_chat_group
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

    # Allow re-registration for the same event. If the user has an active registration
    # for a DIFFERENT event try to auto-cancel it (if cancellation deadline not passed).
    # If auto-cancel is not possible, keep previous behaviour and reject with 409.
    if existing_active and str(existing_active.get('event_id')) != str(ev['_id']):
        # load previous event to check cancellation deadline
        prev_ev = await db_mod.db.events.find_one({'_id': existing_active.get('event_id')}) if existing_active.get('event_id') else None
        # If we can cancel previous registration (deadline not passed), do so automatically
        if prev_ev and not _cancellation_deadline_passed(prev_ev):
            now = _now()
            try:
                # If previous registration belongs to a team, cancel the whole team
                if existing_active.get('team_id'):
                    tid = existing_active.get('team_id')
                    try:
                        await db_mod.db.teams.update_one({'_id': tid}, {'$set': {'status': 'cancelled', 'cancelled_by': creator.get('email'), 'cancelled_at': now, 'updated_at': now}})
                    except Exception:
                        pass
                    res = await db_mod.db.registrations.update_many({'team_id': tid, 'status': {'$nin': list(cancelled_states)}}, {'$set': {'status': 'cancelled_by_user', 'updated_at': now, 'cancelled_at': now}})
                    try:
                        if getattr(res, 'modified_count', 0) > 0:
                            await db_mod.db.events.update_one({'_id': existing_active.get('event_id')}, {'$inc': {'attendee_count': -int(res.modified_count)}})
                    except Exception:
                        pass
                else:
                    # Solo registration: cancel it
                    await db_mod.db.registrations.update_one({'_id': existing_active.get('_id')}, {'$set': {'status': 'cancelled_by_user', 'updated_at': now, 'cancelled_at': now}})
                    try:
                        await db_mod.db.events.update_one({'_id': existing_active.get('event_id'), 'attendee_count': {'$gt': 0}}, {'$inc': {'attendee_count': -1}})
                    except Exception:
                        pass
                # Best-effort: mark for refunds if applicable
                try:
                    await _mark_refund_if_applicable(existing_active, prev_ev)
                except Exception:
                    pass
                # Audit the automatic cancellation
                from app.utils import create_audit_log
                try:
                    await create_audit_log(
                        entity_type='registration',
                        entity_id=existing_active.get('_id'),
                        action='auto_cancel_for_new_registration',
                        actor=creator.get('email'),
                        old_state={'status': existing_active.get('status')},
                        new_state={'status': 'cancelled_by_user'},
                        reason='Auto-cancel previous registration when registering for a new event'
                    )
                except Exception:
                    pass
            except Exception:
                # If cancellation attempt failed for any reason, fall back to blocking behaviour
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
        else:
            # Cannot auto-cancel (deadline passed or no event info) -> block
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

    # If existing registration is cancelled, create a new one instead of updating
    if existing and existing.get('status') in cancelled_states:
        # Mark any pending payments for the old registration as failed (but leave succeeded/paid payments alone for refund tracking)
        try:
            await db_mod.db.payments.update_many(
                {'registration_id': existing.get('_id'), 'status': {'$nin': ['succeeded', 'paid', 'refunded']}}, 
                {'$set': {'status': 'failed', 'updated_at': now}}
            )
        except Exception:
            pass
        # Clear refund_flag from old cancelled registration to prevent duplicate refund listing
        try:
            await db_mod.db.registrations.update_one(
                {'_id': existing.get('_id')}, 
                {'$unset': {'refund_flag': ''}}
            )
        except Exception:
            pass
        existing = None

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
                # Reactivation: move to pending_payment and clear cancelled timestamp and any previous payment pointer
                update_fields['status'] = 'pending_payment'
                update_doc = {
                    '$set': update_fields,
                    '$unset': {'cancelled_at': '', 'payment_id': ''}
                }
                # Also best-effort: mark any existing payment records for this registration as failed
                try:
                    await db_mod.db.payments.update_many({'registration_id': existing.get('_id'), 'status': {'$nin': ['succeeded', 'paid']}}, {'$set': {'status': 'failed', 'updated_at': now}})
                except Exception:
                    pass
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
            # If event has chat enabled, create a default chat group for this solo registrant
            try:
                if ev.get('chat_enabled'):
                    from app.utils import add_participants_to_general_chat
                    await add_participants_to_general_chat(ev.get('_id'), [creator.get('email')], created_by=creator.get('email'))
            except Exception:
                pass
    except Exception:
        if needs_reserve:
            await _release_capacity(ev.get('_id'), 1)
        raise

    # Create payment (one person fee)
    # Payment amount per person comes from event.fee_cents; keep payments router logic for provider integration
    # Return a pointer (full URL) for the client to call the payments create endpoint.
    base = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
    return {
        'registration_id': str(reg_id),
        'team_size': 1,
        'amount_cents': int(ev.get('fee_cents') or 0),
        'payment_create_endpoint': f"{base}/payments/create",
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
    
    # If creator has active registration for a different event, attempt to auto-cancel
    if existing_creator_active and str(existing_creator_active.get('event_id')) != str(ev['_id']):
        prev_ev = await db_mod.db.events.find_one({'_id': existing_creator_active.get('event_id')}) if existing_creator_active.get('event_id') else None
        if prev_ev and not _cancellation_deadline_passed(prev_ev):
            now = _now()
            try:
                if existing_creator_active.get('team_id'):
                    tid = existing_creator_active.get('team_id')
                    try:
                        await db_mod.db.teams.update_one({'_id': tid}, {'$set': {'status': 'cancelled', 'cancelled_by': creator.get('email'), 'cancelled_at': now, 'updated_at': now}})
                    except Exception:
                        pass
                    res = await db_mod.db.registrations.update_many({'team_id': tid, 'status': {'$nin': list(cancelled_states)}}, {'$set': {'status': 'cancelled_by_user', 'updated_at': now, 'cancelled_at': now}})
                    try:
                        if getattr(res, 'modified_count', 0) > 0:
                            await db_mod.db.events.update_one({'_id': existing_creator_active.get('event_id')}, {'$inc': {'attendee_count': -int(res.modified_count)}})
                    except Exception:
                        pass
                else:
                    await db_mod.db.registrations.update_one({'_id': existing_creator_active.get('_id')}, {'$set': {'status': 'cancelled_by_user', 'updated_at': now, 'cancelled_at': now}})
                    try:
                        await db_mod.db.events.update_one({'_id': existing_creator_active.get('event_id'), 'attendee_count': {'$gt': 0}}, {'$inc': {'attendee_count': -1}})
                    except Exception:
                        pass
                try:
                    await _mark_refund_if_applicable(existing_creator_active, prev_ev)
                except Exception:
                    pass
            except Exception:
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
        else:
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
            partner_prev_ev = await db_mod.db.events.find_one({'_id': existing_partner_active.get('event_id')}) if existing_partner_active.get('event_id') else None
            if partner_prev_ev and not _cancellation_deadline_passed(partner_prev_ev):
                now = _now()
                try:
                    if existing_partner_active.get('team_id'):
                        tid = existing_partner_active.get('team_id')
                        try:
                            await db_mod.db.teams.update_one({'_id': tid}, {'$set': {'status': 'cancelled', 'cancelled_by': partner_user.get('email'), 'cancelled_at': now, 'updated_at': now}})
                        except Exception:
                            pass
                        res = await db_mod.db.registrations.update_many({'team_id': tid, 'status': {'$nin': list(cancelled_states)}}, {'$set': {'status': 'cancelled_by_user', 'updated_at': now, 'cancelled_at': now}})
                        try:
                            if getattr(res, 'modified_count', 0) > 0:
                                await db_mod.db.events.update_one({'_id': existing_partner_active.get('event_id')}, {'$inc': {'attendee_count': -int(res.modified_count)}})
                        except Exception:
                            pass
                    else:
                        await db_mod.db.registrations.update_one({'_id': existing_partner_active.get('_id')}, {'$set': {'status': 'cancelled_by_user', 'updated_at': now, 'cancelled_at': now}})
                        try:
                            await db_mod.db.events.update_one({'_id': existing_partner_active.get('event_id'), 'attendee_count': {'$gt': 0}}, {'$inc': {'attendee_count': -1}})
                        except Exception:
                            pass
                    try:
                        await _mark_refund_if_applicable(existing_partner_active, partner_prev_ev)
                    except Exception:
                        pass
                except Exception:
                    partner_event_info = await db_mod.db.events.find_one({'_id': existing_partner_active.get('event_id')})
                    partner_event_title = partner_event_info.get('title') if partner_event_info else 'another event'
                    raise HTTPException(
                        status_code=409,
                        detail=f'Your partner already has an active registration for {partner_event_title}. They must cancel that registration first.'
                    )
            else:
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
                'allergies': creator.get('allergies', []),
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
            'allergies': partner_user.get('allergies', []),
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
            'allergies': partner_external_info.get('allergies', []),
        })

    # Validate at least one kitchen available
    if not any(bool(m.get('kitchen_available')) for m in team_doc['members']):
        raise HTTPException(status_code=400, detail='At least one kitchen must be available in the team')

    # Validate that cooking location has a kitchen
    cooking_location_idx = 0 if payload.cooking_location == 'creator' else 1
    if not bool(team_doc['members'][cooking_location_idx].get('kitchen_available')):
        location_name = 'creator' if cooking_location_idx == 0 else 'partner'
        raise HTTPException(status_code=400, detail=f'Cooking location ({location_name}) must have a kitchen available')

    # Ensure main course rule on chosen location
    if team_doc.get('course_preference') == 'main':
        if team_doc.get('cooking_location') == 'creator' and not creator_main:
            raise HTTPException(status_code=400, detail='Main Course requires creator main_course_possible')
        if team_doc.get('cooking_location') == 'partner':
            partner_main = bool(team_doc['members'][1].get('main_course_possible'))
            if not partner_main:
                raise HTTPException(status_code=400, detail='Main Course requires partner main_course_possible')

    await _reserve_capacity(ev, 2)

    # Attempt to run team + registrations in a DB transaction when available
    try:
        client = getattr(db_mod, 'mongo_db', None)
        session = None
        use_tx = False
        if client and getattr(client, 'client', None):
            try:
                # start a session; if server doesn't support transactions this may raise
                session = client.client.start_session()
                session.start_transaction()
                use_tx = True
            except Exception:
                session = None
                use_tx = False

        # Helper to run collection operations with or without session
        async def coll_insert_one(coll, doc):
            if session:
                return await getattr(db_mod.db, coll).insert_one(doc, session=session)
            return await getattr(db_mod.db, coll).insert_one(doc)

        # Insert team
        team_res = await coll_insert_one('teams', team_doc)
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
        reg_creator_res = await coll_insert_one('registrations', reg_creator)
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
            reg_partner_res = await coll_insert_one('registrations', reg_partner)
            reg_partner_id = reg_partner_res.inserted_id
            # Notify partner via email with decline link
            base = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
            frontend_base = os.getenv('FRONTEND_BASE_URL', base)
            decline_link = f"{frontend_base}/team-invitation.html?team_id={team_id}&action=decline"
            
            # Get event date for email
            event_date = ev.get('date') or (ev.get('start_at').strftime('%Y-%m-%d') if ev.get('start_at') else 'TBD')
            
            # Use the new notification function
            try:
                from app import notifications
                _ = await notifications.send_team_invitation(
                    partner_email=partner_user.get('email'),
                    creator_email=creator.get('email'),
                    event_title=ev.get('title', 'Upcoming Event'),
                    event_date=event_date,
                    decline_url=decline_link,
                    team_id=str(team_id)
                )
            except Exception:
                # Fallback to basic email if notification function fails
                subject = 'You have been added to a DinnerHopping team'
                body = (
                    f"Hi,\n\nYou were added to a team for event '{ev.get('title')}' by {creator.get('email')}.\n\n"
                    f"You have been automatically registered. If you cannot participate, you can decline here:\n{decline_link}\n\n"
                    f"Thanks,\nDinnerHopping Team"
                )
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

        # commit transaction if used
        if session and use_tx:
            try:
                await session.commit_transaction()
            except Exception:
                try:
                    await session.abort_transaction()
                except Exception:
                    pass
                raise
    except Exception:
        # rollback and cleanup
        try:
            if session and use_tx:
                await session.abort_transaction()
        except Exception:
            pass
        await _release_capacity(ev.get('_id'), 2)
        raise
    finally:
        try:
            if session:
                session.end_session()
        except Exception:
            pass

    # best-effort: create chat group for teams if event has chat enabled
    try:
        if ev.get('chat_enabled'):
            participants = [creator.get('email')]
            if partner_user:
                participants.append(partner_user.get('email'))
            else:
                participants.append(team_doc['members'][1].get('email'))
            await create_chat_group(ev.get('_id'), participants, creator.get('email'), section_ref='team')
            # also add both to the general chat for the event
            try:
                from app.utils import add_participants_to_general_chat
                await add_participants_to_general_chat(ev.get('_id'), participants, created_by=creator.get('email'))
            except Exception:
                pass
    except Exception:
        # ignore chat creation errors
        pass


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
    base = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
    return {
        'team_id': str(team_id),
        'registration_id': str(reg_creator_id),
        'partner_registration_id': str(reg_partner_id) if reg_partner_id else None,
        'team_size': 2,
        'amount_cents': team_amount,
        'payment_create_endpoint': f"{base}/payments/create",
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
    
    # Notify creator that partner declined
    try:
        from app import notifications
        creator_reg = await db_mod.db.registrations.find_one({'team_id': tid, 'user_id': team.get('created_by_user_id')})
        if creator_reg and creator_reg.get('user_email_snapshot'):
            await notifications.send_team_partner_cancelled(
                creator_email=creator_reg.get('user_email_snapshot'),
                event_title=ev.get('title', 'Event')
            )
    except Exception:
        pass
    
    return {'status': 'declined'}


@router.get('/teams/{team_id}')
async def get_team_details(team_id: str, current_user=Depends(get_current_user)):
    """Get team details for invitation page.
    
    Returns basic team information including event details and creator info.
    Only accessible by team members.
    """
    try:
        tid = ObjectId(team_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail='invalid team id') from exc
    
    team = await db_mod.db.teams.find_one({'_id': tid})
    if not team:
        raise HTTPException(status_code=404, detail='Team not found')
    
    # Check if current user is a member of this team
    members = team.get('members') or []
    is_member = any(
        m.get('email', '').lower() == current_user.get('email', '').lower() or
        m.get('user_id') == current_user.get('_id')
        for m in members
    )
    
    if not is_member:
        raise HTTPException(status_code=403, detail='You are not a member of this team')
    
    # Get event details
    ev = await db_mod.db.events.find_one({'_id': team.get('event_id')}) if team.get('event_id') else None
    if not ev:
        raise HTTPException(status_code=404, detail='Event not found')
    
    # Get creator info
    creator = await db_mod.db.users.find_one({'_id': team.get('created_by_user_id')})
    
    event_date = ev.get('date') or (ev.get('start_at').strftime('%Y-%m-%d') if ev.get('start_at') else 'TBD')
    
    return {
        'team_id': str(team.get('_id')),
        'event_id': str(ev.get('_id')),
        'event_title': ev.get('title'),
        'event_date': event_date,
        'created_by_email': creator.get('email') if creator else 'Unknown',
        'status': team.get('status'),
        'team_diet': team.get('team_diet'),
        'cooking_location': team.get('cooking_location'),
        'course_preference': team.get('course_preference'),
    }


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


@router.get('/search-user')
async def search_user_by_email(email: str, current_user=Depends(get_current_user)):
    """Search for a user by email address for team invitation.
    
    Returns basic user info if found, used by frontend to validate partner invitations.
    Only returns public info (email, name) - no sensitive data.
    """
    if not email or not email.strip():
        raise HTTPException(status_code=400, detail='Email parameter required')
    
    email_lower = email.strip().lower()
    
    # Don't allow searching for yourself
    if email_lower == current_user.get('email', '').lower():
        raise HTTPException(status_code=400, detail='Cannot invite yourself as partner')
    
    user = await db_mod.db.users.find_one({'email': email_lower})
    
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    
    # Return only public information
    return {
        'email': user.get('email'),
        'full_name': user.get('full_name'),
        'kitchen_available': bool(user.get('kitchen_available')),
        'main_course_possible': bool(user.get('main_course_possible')),
        'dietary_preference': _enum_value(DietaryPreference, user.get('default_dietary_preference')) or 'omnivore',
    }


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
    # Prevent double refund: only mark if not already requested/processed
    if pay.get('refund_requested') or pay.get('status') == 'refunded':
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
    # If event supports refunds, mark registration as refundable for admin listing
    try:
        if ev.get('refund_on_cancellation'):
            await db_mod.db.registrations.update_one({'_id': reg['_id']}, {'$set': {'refund_flag': True}})
    except Exception:
        pass
    # If event supports refunds, mark registration as refundable for admin listing
    try:
        if ev.get('refund_on_cancellation'):
            await db_mod.db.registrations.update_one({'_id': reg['_id']}, {'$set': {'refund_flag': True}})
    except Exception:
        pass
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
    
    # email best-effort (use notification helper which will render template)
    if reg.get('user_email_snapshot'):
        try:
            from app import notifications
            refund_flag = bool(ev.get('refund_on_cancellation'))
            _ = await notifications.send_cancellation_confirmation(reg.get('user_email_snapshot'), ev.get('title'), refund_flag)
        except Exception:
            # best-effort: don't fail cancellation on email errors
            pass
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
    # Attempt to mark payment for refund if applicable (best-effort)
    try:
        await _mark_refund_if_applicable(reg, ev)
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
    # Send cancellation confirmation to the member who cancelled
    if reg.get('user_email_snapshot'):
        try:
            from app import notifications
            refund_flag = bool(ev.get('refund_on_cancellation'))
            _ = await notifications.send_cancellation_confirmation(reg.get('user_email_snapshot'), ev.get('title'), refund_flag)
        except Exception:
            pass
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
            'allergies': partner_user.get('allergies', []),
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
            'allergies': partner_external_info.get('allergies', []),
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
            if r.get('event_id'):
                try:
                    ev = await get_event(r.get('event_id'))
                    if ev:
                        r['event_title'] = ev.get('title')
                        r['event_fee_cents'] = ev.get('fee_cents')
                except Exception:
                    r['event_title'] = 'Unknown Event (loading error)'
                    r['event_fee_cents'] = 'Unknown'
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
