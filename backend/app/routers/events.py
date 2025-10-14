"""Events router

Handles event management including:
- Event creation, reading, updating, and deletion (CRUD)
- Event status lifecycle management
- Event listing with filters (date, status, location, participant)
- Registration to events
- Attendee counting
- User event plan retrieval
"""

######### Imports #########

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from datetime import datetime, timezone, timedelta
from typing import Optional, Literal, List
from datetime import timezone, timedelta
from app import db as db_mod
from app.auth import get_current_user, require_admin
from app.utils import anonymize_address, encrypt_address, anonymize_public_address, require_event_registration_open, create_chat_group
from bson.objectid import ObjectId
from bson.errors import InvalidId
from pymongo.errors import PyMongoError
import datetime
import os

######### Constants and Status Management #########

_ALLOWED_STATUSES = {'draft','coming_soon','open','closed','matched','released','cancelled'}
_LEGACY_MAP = { 'published': 'open' }

def _normalize_status(v: Optional[str]) -> str:
    if not v:
        return 'draft'
    s = str(v).strip().lower()
    s = _LEGACY_MAP.get(s, s)
    return s if s in _ALLOWED_STATUSES else 'draft'
router = APIRouter()

######### Date/Datetime Helpers #########

# --- Date/Datetime helpers (added to fix InvalidDocument for datetime.date) ---

def _parse_incoming_date(name: str, value):
    """Best-effort parse of incoming date/time strings.

    Returns one of:
    - None if value is falsy
    - datetime.datetime for datetime-like fields (start_at, registration_deadline, payment_deadline)
    - str (ISO date) for the 'date' field (kept as string for legacy compatibility)
    - original value on failure
    """
    if value in (None, ''):
        return None
    # Already acceptable types
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):  # promote to datetime for non-'date' fields
        if name == 'date':
            return value.isoformat()
        return datetime.datetime.combine(value, datetime.time(0, 0))
    if isinstance(value, str):
        txt = value.strip()
        if not txt:
            return None
        # Handle trailing Z (UTC) which fromisoformat doesn't parse directly
        if txt.endswith('Z'):
            txt_stripped = txt[:-1]
        else:
            txt_stripped = txt
        try:
            if name == 'date' and len(txt_stripped) == 10:
                # YYYY-MM-DD
                return datetime.date.fromisoformat(txt_stripped).isoformat()
            # Try full datetime
            dt = datetime.datetime.fromisoformat(txt_stripped)
            # For pure date (no time) fromisoformat returns datetime with 00:00 time
            if name == 'date':
                return dt.date().isoformat()
            return dt
        except ValueError:
            # Fallback: if looks like YYYY-MM-DD for non-'date' -> promote to datetime midnight
            if len(txt) == 10 and txt.count('-') == 2:
                try:
                    d = datetime.date.fromisoformat(txt)
                    if name == 'date':
                        return d.isoformat()
                    return datetime.datetime.combine(d, datetime.time(0, 0))
                except ValueError:
                    return value
            return value
    return value
    
def _normalize_status(v: Optional[str]) -> str:
    if not v:
        return 'draft'
    s = str(v).strip().lower()
    s = _LEGACY_MAP.get(s, s)
    return s if s in _ALLOWED_STATUSES else 'draft'
    
def _sanitize_event_doc(doc: dict) -> dict:
    """Mutate & return event doc ensuring Mongo encodable values for date/time fields.

    - 'date' stored as ISO date string (YYYY-MM-DD)
    - datetime.date objects for other fields are promoted to datetime.datetime midnight
    - Leaves other values untouched.
    """
    if not isinstance(doc, dict):
        return doc
    date_fields = ['date', 'start_at', 'registration_deadline', 'payment_deadline']
    for f in date_fields:
        if f in doc:
            parsed = _parse_incoming_date(f, doc.get(f))
            # Promote stray datetime.date (non 'date') to datetime
            if isinstance(parsed, datetime.date) and not isinstance(parsed, datetime.datetime):
                if f == 'date':
                    parsed = parsed.isoformat()
                else:
                    parsed = datetime.datetime.combine(parsed, datetime.time(0, 0))
            # Ensure 'date' is plain string
            if f == 'date' and isinstance(parsed, datetime.datetime):
                parsed = parsed.date().isoformat()
            doc[f] = parsed
    return doc

######### Serialization Helpers #########

def _fmt_date(v):
    """Format stored date/datetime value to API string.

    Returns:
    - ISO date (YYYY-MM-DD) for date/datetime representing date-only
    - ISO 8601 datetime without microseconds for datetimes
    - Original string if already a string
    - None if value falsy
    """
    if not v:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, datetime.datetime):
        # If time is midnight and no tz info, treat as date-only
        if v.hour == 0 and v.minute == 0 and v.second == 0 and v.microsecond == 0:
            return v.date().isoformat()
        return v.replace(microsecond=0).isoformat()
    if isinstance(v, datetime.date):
        return v.isoformat()
    return str(v)

# Generic serializer (recursive) to convert ObjectId & datetime for JSON responses
# (events.py referenced _serialize without defining it previously)
def _serialize(obj):
    if obj is None:
        return None
    if isinstance(obj, list):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k] = _serialize(v)
        return out
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime.datetime):
        return _fmt_date(obj)
    if isinstance(obj, datetime.date):
        return obj.isoformat()
    return obj

class LocationIn(BaseModel):
    address: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None

class EventCreate(BaseModel):
    """Admin event creation/update payload (simplified pricing).

    Pricing: a single field `fee_cents` (price per participant). The total to pay
    for a team of size N = fee_cents * N. Legacy variants fee_solo_cents / fee_team_cents removed.
    """
    title: str
    description: Optional[str] = None
    extra_info: Optional[str] = None
    date: Optional[datetime.date] = None
    start_at: Optional[datetime.datetime] = None
    capacity: Optional[int] = None
    fee_cents: Optional[int] = 0
    city: Optional[str] = None
    registration_deadline: Optional[datetime.datetime] = None
    payment_deadline: Optional[datetime.datetime] = None
    valid_zip_codes: Optional[List[str]] = Field(default_factory=list, description="Whitelisted postal codes allowed to register")
    after_party_location: Optional[LocationIn] = None
    organizer_id: Optional[str] = None
    status: Optional[Literal['draft','coming_soon','open','closed','matched','released','cancelled']] = 'draft'
    refund_on_cancellation: Optional[bool] = None
    chat_enabled: Optional[bool] = None

class LocationOut(BaseModel):
    address_public: Optional[str] = None
    point: Optional[dict] = None

class EventOut(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    extra_info: Optional[str] = None
    date: Optional[datetime.date] = None
    start_at: Optional[datetime.datetime] = None
    capacity: Optional[int] = None
    fee_cents: Optional[int] = 0
    city: Optional[str] = None
    registration_deadline: Optional[datetime.datetime] = None
    payment_deadline: Optional[datetime.datetime] = None
    valid_zip_codes: List[str] = []
    after_party_location: Optional[LocationOut] = None
    attendee_count: int = 0
    status: Optional[Literal['draft','coming_soon','open','closed','matched','released','cancelled']] = 'draft'
    matching_status: Optional[Literal['not_started','in_progress','proposed','finalized','archived']] = 'not_started'
    organizer_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime.datetime] = None
    updated_at: Optional[datetime.datetime] = None
    refund_on_cancellation: Optional[bool] = None
    chat_enabled: Optional[bool] = None
    event_plan: Optional[str] = None  # New field to include event plan details

def _safe_location(loc: Optional[dict]) -> Optional[dict]:
    """Coerce location-like dict to API-friendly shape.

    Ensures 'point' is a dict or None; drops invalid types to prevent Pydantic errors.
    Accepts both after_party_location and legacy 'location' shapes.
    """
    if not isinstance(loc, dict):
        return None
    out: dict = {}
    ap = loc.get('address_public')
    if ap is not None:
        out['address_public'] = ap if isinstance(ap, str) else str(ap)
    pt = loc.get('point')
    if not isinstance(pt, dict):
        pt = loc.get('zip') if isinstance(loc.get('zip'), dict) else None
    out['point'] = pt if isinstance(pt, dict) else None
    return out

@router.get("/", response_model=list[EventOut])
async def list_events(date: Optional[str] = None, status: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None, radius_m: Optional[int] = None, participant: Optional[str] = None, current_user=Depends(get_current_user)):
    """List events with optional filters:
    - date: exact match
    - status: 'published' or 'draft'
    - lat/lon + radius_m: simple bounding box approx using degrees (approx)
    """
    query = {}
    if date:
        query['date'] = date
    if status:
        # unify requested 'published' to 'open'
        if status == 'published':
            status = 'open'
        query['status'] = status
    if lat is not None and lon is not None and radius_m is not None:
        delta_deg = radius_m / 111000.0
        query['lat'] = {"$gte": lat - delta_deg, "$lte": lat + delta_deg}
        query['lon'] = {"$gte": lon - delta_deg, "$lte": lon + delta_deg}

    if participant:
        target_email = None
        target_user_id = None
        if participant == 'me':
            target_user_id = current_user.get('_id')
        else:
            if '@' in participant:
                target_email = participant.lower()
            else:
                try:
                    target_user_id = ObjectId(participant)
                except (InvalidId, TypeError, ValueError):
                    target_email = participant.lower()
        reg_query = {}
        if target_user_id is not None:
            reg_query['user_id'] = target_user_id
        if target_email is not None:
            reg_query['user_email_snapshot'] = target_email
        event_ids = set()
        async for r in db_mod.db.registrations.find(reg_query, {'event_id': 1}):
            if r.get('event_id') is not None:
                event_ids.add(r['event_id'])
        if not event_ids:
            return []
        query['_id'] = {'$in': list(event_ids)}

    roles = current_user.get('roles') or []
    is_admin = 'admin' in roles
    if not is_admin and not status:
        query['status'] = {'$in': ['coming_soon','open','matched','released']}

    events_resp = []
    async for e in db_mod.db.events.find(query):
        if not is_admin:
            valid_zips = e.get('valid_zip_codes') or []
            user_zip = (current_user.get('postal_code') or '').strip()
            if valid_zips and user_zip and user_zip not in valid_zips:
                continue
        date_val = _fmt_date(e.get('date')) or ''
        start_val = _fmt_date(e.get('start_at'))
        registration_deadline_val = _fmt_date(e.get('registration_deadline'))
        payment_deadline_val = _fmt_date(e.get('payment_deadline'))

        # Normalize after_party_location using the safe helper when building the response
        # (previous call to `_normalize_location_for_output` was undefined and unused)
        # raw_loc = e.get('after_party_location') or e.get('location')

        events_resp.append(EventOut(
            id=str(e.get('_id')),
            title=e.get('title') or e.get('name') or 'Untitled',
            description=e.get('description'),
            extra_info=e.get('extra_info'),
            date=date_val,
            registration_deadline=registration_deadline_val,
            start_at=start_val,
            payment_deadline=payment_deadline_val,
            capacity=e.get('capacity'),
            fee_cents=e.get('fee_cents', 0),
            city=e.get('city'),
            attendee_count=e.get('attendee_count', 0),
            status=_normalize_status(e.get('status')),
            organizer_id=str(e.get('organizer_id')) if e.get('organizer_id') is not None else None,
            created_by=str(e.get('created_by')) if e.get('created_by') is not None else None,
            after_party_location=_safe_location(e.get('after_party_location') or e.get('location')),
            created_at=e.get('created_at'),
            updated_at=e.get('updated_at'),
            refund_on_cancellation=e.get('refund_on_cancellation'),
            chat_enabled=e.get('chat_enabled'),
            valid_zip_codes=e.get('valid_zip_codes', []),
            event_plan=e.get('event_plan')  # Include event plan in the response
        ))
    return events_resp


@router.post('/', response_model=EventOut)
async def create_event(payload: EventCreate, current_user=Depends(require_admin)):
    # admin-only: only admins can create events
    now = datetime.datetime.now(datetime.timezone.utc)
    doc = payload.model_dump()
    # build after_party_location subdocument if provided (accept legacy 'location')
    loc_in = doc.pop('after_party_location', None)
    if loc_in is None:
        # support legacy payload key
        loc_in = doc.pop('location', None)
    after_party_location = None
    if isinstance(loc_in, dict):
        address = loc_in.get('address')
        lat = loc_in.get('lat')
        lon = loc_in.get('lon')
        # Attempt geocoding if address present but no coordinates
        if address and (lat is None or lon is None):
            try:
                from app.services.geocoding import geocode_address  # local import to avoid circular imports at module load
                latlon = await geocode_address(address)
                if latlon:
                    lat, lon = latlon
            except Exception:
                pass
        if address:
            after_party_location = {
                'address_encrypted': encrypt_address(address),
                'address_public': anonymize_public_address(address),
                'zip': {'type': 'Point', 'coordinates': [lon, lat]} if lat is not None and lon is not None else None
            }
        elif lat is not None and lon is not None:
            after_party_location = {
                'address_encrypted': None,
                'address_public': None,
                'zip': {'type': 'Point', 'coordinates': [lon, lat]}
            }
    if after_party_location is not None:
        doc['after_party_location'] = after_party_location

    # If organizer_id not provided, set to current admin user by default
    if not doc.get('organizer_id'):
        doc['organizer_id'] = current_user.get('_id')
    else:
        try:
            doc['organizer_id'] = ObjectId(doc['organizer_id'])
        except (InvalidId, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail='invalid organizer_id') from exc

    # default fields per schema & fee compatibility
    doc['attendee_count'] = 0
    doc['fee_cents'] = int(doc.get('fee_cents', 0)) if doc.get('fee_cents') is not None else 0
    doc['registration_deadline'] = doc.get('registration_deadline')
    doc['payment_deadline'] = doc.get('payment_deadline')
    doc['matching_status'] = doc.get('matching_status', 'not_started')
    doc['created_at'] = now
    doc['updated_at'] = now
    # pass-through of valid_zip_codes if provided
    if payload.valid_zip_codes is not None:
        doc['valid_zip_codes'] = payload.valid_zip_codes
    # set created_by to current user
    doc['created_by'] = current_user.get('_id')

    # enforce status default (pydantic already defaults but ensure correct)
    if not doc.get('status'):
        doc['status'] = 'draft'

    # Sanitize date/time fields for Mongo
    _sanitize_event_doc(doc)

    res = await db_mod.db.events.insert_one(doc)
    return EventOut(
        id=str(res.inserted_id),
        title=doc.get('title'),
        description=doc.get('description'),
        extra_info=doc.get('extra_info'),
        date=_fmt_date(doc.get('date')) or '',
        start_at=_fmt_date(doc.get('start_at')),
        capacity=doc.get('capacity'),
        fee_cents=doc.get('fee_cents', 0),
        city=doc.get('city'),
        registration_deadline=_fmt_date(doc.get('registration_deadline')),
        payment_deadline=_fmt_date(doc.get('payment_deadline')),
        after_party_location=_safe_location(doc.get('after_party_location')),
        attendee_count=0,
        status=_normalize_status(doc.get('status')),
        matching_status=doc.get('matching_status'),
        organizer_id=str(doc['organizer_id']) if doc.get('organizer_id') is not None else None,
        created_by=str(doc['created_by']) if doc.get('created_by') is not None else None,
        created_at=doc.get('created_at'),
        updated_at=doc.get('updated_at'),
        refund_on_cancellation=doc.get('refund_on_cancellation'),
        chat_enabled=doc.get('chat_enabled'),
        valid_zip_codes=doc.get('valid_zip_codes', []),
    )

# Alias without trailing slash for clients/tests calling '/events' exactly when redirect_slashes=False
@router.post('', response_model=EventOut)
async def create_event_no_trailing(payload: EventCreate, current_user=Depends(require_admin)):
    return await create_event(payload, current_user)


@router.get('/{event_id}')
async def get_event(event_id: str, anonymise: bool = True, current_user=Depends(get_current_user)):
    try:
        event_oid = ObjectId(event_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail='Invalid event ID')

    e = await db_mod.db.events.find_one({"_id": event_oid})
    if not e:
        raise HTTPException(status_code=404, detail='Event not found')
    # enforce that drafts are not visible to non-admins/non-organizers
    roles = current_user.get('roles') or []
    is_admin = 'admin' in roles
    # organizer_id may be stored as ObjectId
    org = e.get('organizer_id')
    if e.get('status') == 'draft' and not is_admin:
        if org is None or str(org) != str(current_user.get('_id')):
            raise HTTPException(status_code=404, detail='Event not found')
    # serialize the whole document, converting ObjectId
    serialized = _serialize(e)
    # ensure id is present as string
    serialized['id'] = str(e.get('_id'))
    # ensure fee_cents is always present (default 0)
    serialized['fee_cents'] = e.get('fee_cents', 0)
    # anonymise after_party_location info (fallback to legacy 'location')
    loc = None
    if isinstance(e.get('after_party_location'), dict):
        loc = e.get('after_party_location')
    elif isinstance(e.get('location'), dict):
        loc = e.get('location')
    if anonymise and loc:
        # if address_public present keep only that
        pub = loc.get('address_public')
        if pub:
            serialized['after_party_location'] = {'address_public': pub}
        else:
            # derive anonymised from point/zip coordinates if present
            pt_raw = loc.get('point') if isinstance(loc.get('point'), dict) else None
            if not pt_raw:
                pt_raw = loc.get('zip') if isinstance(loc.get('zip'), dict) else None
            if pt_raw and isinstance(pt_raw.get('coordinates'), list) and len(pt_raw['coordinates']) == 2:
                lon, lat = pt_raw['coordinates']
                if lat is not None and lon is not None:
                    serialized['after_party_location'] = anonymize_address(lat, lon)
    else:
        serialized['after_party_location'] = loc
    # include organizer_id/created_by as strings
    if e.get('organizer_id') is not None:
        serialized['organizer_id'] = str(e.get('organizer_id'))
    if e.get('created_by') is not None:
        serialized['created_by'] = str(e.get('created_by'))
    # include optional admin fields if present
    serialized['extra_info'] = e.get('extra_info')
    serialized['city'] = e.get('city')
    serialized['refund_on_cancellation'] = e.get('refund_on_cancellation')
    serialized['chat_enabled'] = e.get('chat_enabled')
    # legacy fields removed: fee_solo_cents, fee_team_cents
    serialized['valid_zip_codes'] = e.get('valid_zip_codes', [])
    # normalize legacy status mapping
    if serialized.get('status') == 'published':
        serialized['status'] = 'open'
    return serialized


@router.put('/{event_id}', response_model=EventOut)
async def update_event(event_id: str, payload: EventCreate, _=Depends(require_admin)):
    update = payload.model_dump(exclude_unset=True)
    # load existing event to detect flips (e.g., chat_enabled toggles)
    try:
        existing_event = await db_mod.db.events.find_one({'_id': ObjectId(event_id)})
    except Exception:
        existing_event = None
    # handle after_party_location update (accept legacy 'location')
    loc_in = update.pop('after_party_location', None)
    if loc_in is None:
        loc_in = update.pop('location', None)
    after_party_location = None
    if isinstance(loc_in, dict):
        address = loc_in.get('address')
        lat = loc_in.get('lat')
        lon = loc_in.get('lon')
        # Attempt geocoding if address present but no coordinates
        if address and (lat is None or lon is None):
            try:
                from app.services.geocoding import geocode_address
                latlon = await geocode_address(address)
                if latlon:
                    lat, lon = latlon
            except Exception:
                pass
        if address:
            after_party_location = {
                'address_encrypted': encrypt_address(address),
                'address_public': anonymize_public_address(address),
                'zip': {'type': 'Point', 'coordinates': [lon, lat]} if lat is not None and lon is not None else None
            }
        elif lat is not None and lon is not None:
            after_party_location = {
                'address_encrypted': None,
                'address_public': None,
                'zip': {'type': 'Point', 'coordinates': [lon, lat]}
            }
    if after_party_location is not None:
        update['after_party_location'] = after_party_location

    # convert organizer_id if provided
    if 'organizer_id' in update and update.get('organizer_id') is not None:
        try:
            update['organizer_id'] = ObjectId(update['organizer_id'])
        except (InvalidId, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail='invalid organizer_id') from exc

    # set updated_at
    update['updated_at'] = datetime.datetime.now()

    # Sanitize date/time fields for Mongo
    _sanitize_event_doc(update)

    # Persist changes (model_dump with exclude_unset ensures we only touch provided fields)
    await db_mod.db.events.update_one({"_id": ObjectId(event_id)}, {"$set": update})
    e = await db_mod.db.events.find_one({"_id": ObjectId(event_id)})

    # If admin just enabled chat (flipped from falsy to True), create best-effort groups
    try:
        if 'chat_enabled' in update and update.get('chat_enabled') and not (existing_event and existing_event.get('chat_enabled')):
            # create team groups
            try:
                async for t in db_mod.db.teams.find({'event_id': ObjectId(event_id)}):
                    members = t.get('members') or []
                    emails = [m.get('email') for m in members if m.get('email')]
                    if emails:
                        await create_chat_group(event_id, emails, str(e.get('created_by') or 'admin'), section_ref='team')
            except Exception:
                pass
            # create a general group for solo regs
            try:
                solo_emails = []
                async for r in db_mod.db.registrations.find({'event_id': ObjectId(event_id), 'team_id': None, 'status': {'$nin': ['cancelled_by_user','cancelled_admin']}}):
                    em = r.get('user_email_snapshot')
                    if em:
                        solo_emails.append(em)
                if solo_emails:
                    await create_chat_group(event_id, list(dict.fromkeys(solo_emails)), str(e.get('created_by') or 'admin'), section_ref='general')
            except Exception:
                pass
    except Exception:
        pass
    return EventOut(
        id=str(e['_id']),
        title=e.get('title') or e.get('name'),
        description=e.get('description'),
        extra_info=e.get('extra_info'),
        date=_fmt_date(e.get('date')),
        start_at=_fmt_date(e.get('start_at')),
        capacity=e.get('capacity'),
        fee_cents=e.get('fee_cents', 0),
        city=e.get('city'),
        registration_deadline=_fmt_date(e.get('registration_deadline')),
        payment_deadline=_fmt_date(e.get('payment_deadline')),
        after_party_location=_safe_location(e.get('after_party_location') or e.get('location')),
        attendee_count=e.get('attendee_count', 0),
        status=_normalize_status(e.get('status')),
        matching_status=e.get('matching_status', 'not_started'),
        organizer_id=str(e.get('organizer_id')) if e.get('organizer_id') is not None else None,
        created_by=str(e.get('created_by')) if e.get('created_by') is not None else None,
        created_at=e.get('created_at'),
        updated_at=e.get('updated_at'),
        refund_on_cancellation=e.get('refund_on_cancellation'),
        chat_enabled=e.get('chat_enabled'),
        valid_zip_codes=e.get('valid_zip_codes', []),
    )


@router.post('/{event_id}/status/{new_status}')
async def change_event_status(event_id: str, new_status: str, _=Depends(require_admin)):
    """Generic status transition endpoint (admin).

    Accepts lifecycle statuses: draft, coming_soon, open, closed, matched, released, cancelled.
    Legacy 'published' will be rewritten to 'open'.
    """
    allowed = {'draft','coming_soon','open','closed','matched','released','cancelled','published'}
    if new_status not in allowed:
        raise HTTPException(status_code=400, detail='invalid status')
    if new_status == 'published':
        new_status = 'open'
    e = await db_mod.db.events.find_one({'_id': ObjectId(event_id)})
    if not e:
        raise HTTPException(status_code=404, detail='Event not found')
    await db_mod.db.events.update_one({'_id': ObjectId(event_id)}, {'$set': {'status': new_status, 'updated_at': datetime.datetime.now(datetime.timezone.utc)}})
    return {'status': new_status}

@router.delete('/{event_id}')
async def delete_event(event_id: str, cascade: bool = True, _=Depends(require_admin)):
    """Delete an event. If cascade is True (default), remove related records:
    - registrations (+ invitations & payments by registration)
    - matches (by string event_id)
    - plans (by ObjectId event_id)
    - teams (by ObjectId event_id)
    Returns counts of deleted documents.
    """
    try:
      oid = ObjectId(event_id)
    except (InvalidId, TypeError, ValueError):
      raise HTTPException(status_code=400, detail='invalid event_id')

    e = await db_mod.db.events.find_one({'_id': oid})
    if not e:
      raise HTTPException(status_code=404, detail='Event not found')

    deleted = {
      'event': 0,
      'registrations': 0,
      'invitations': 0,
      'payments': 0,
      'matches': 0,
      'plans': 0,
      'teams': 0,
    }

    if cascade:
      # registrations and dependent docs
      reg_ids = [r['_id'] async for r in db_mod.db.registrations.find({'event_id': oid}, {'_id': 1})]
      if reg_ids:
        # invitations by registration_id
        inv_res = await db_mod.db.invitations.delete_many({'registration_id': {'$in': reg_ids}})
        deleted['invitations'] = getattr(inv_res, 'deleted_count', 0)
        # payments by registration_id
        pay_res = await db_mod.db.payments.delete_many({'registration_id': {'$in': reg_ids}})
        deleted['payments'] = getattr(pay_res, 'deleted_count', 0)
        # registrations themselves
        reg_res = await db_mod.db.registrations.delete_many({'_id': {'$in': reg_ids}})
        deleted['registrations'] = getattr(reg_res, 'deleted_count', 0)
      # matches store event_id as string
      m_res = await db_mod.db.matches.delete_many({'event_id': event_id})
      deleted['matches'] = getattr(m_res, 'deleted_count', 0)
      # plans store event_id as ObjectId
      pl_res = await db_mod.db.plans.delete_many({'event_id': oid})
      deleted['plans'] = getattr(pl_res, 'deleted_count', 0)
      # teams store event_id as ObjectId (if teams collection used)
      try:
        t_res = await db_mod.db.teams.delete_many({'event_id': oid})
        deleted['teams'] = getattr(t_res, 'deleted_count', 0)
      except Exception:
        pass

    ev_res = await db_mod.db.events.delete_one({'_id': oid})
    deleted['event'] = getattr(ev_res, 'deleted_count', 0)

    return {'status': 'deleted', 'deleted': deleted}

@router.post("/{event_id}/register")
async def register_for_event(event_id: str, payload: dict, current_user=Depends(get_current_user)):
    # payload may include team info, invited_emails and preferences override
    # invited_emails: list of emails to invite (they will receive an invitation token)
    # ensure event exists and is open (published legacy)
    event = await db_mod.db.events.find_one({'_id': ObjectId(event_id)})
    if not event:
        raise HTTPException(status_code=404, detail='Event not found')
    status = event.get('status')
    if status in ('draft','coming_soon'):
        raise HTTPException(status_code=400, detail='Registration not open')
    if status not in ('open','released','matched'):
        raise HTTPException(status_code=400, detail='Event not accepting registrations')
    # ensure registration window is still open
    require_event_registration_open(event)

    # avoid duplicate registrations for same user/event
    existing = await db_mod.db.registrations.find_one({"event_id": ObjectId(event_id), "user_id": current_user.get('_id')})
    if existing:
        return {"status": "already_registered"}

    team_size = int(payload.get('team_size', 1))
    preferences = payload.get('preferences', current_user.get('preferences', {}))

    # Try to reserve capacity atomically. If capacity exists, ensure team_size fits.
    if event.get('capacity') is not None:
        # filter ensures attendee_count + team_size <= capacity
        filter_q = {
            '_id': ObjectId(event_id),
            '$expr': { '$lte': [ { '$add': [ '$attendee_count', team_size ] }, '$capacity' ] }
        }
        upd = { '$inc': { 'attendee_count': team_size } }
        res_upd = await db_mod.db.events.update_one(filter_q, upd)
        if res_upd.modified_count == 0:
            return { 'status': 'full' }
    else:
        # no capacity limit, just increment
        await db_mod.db.events.update_one({'_id': ObjectId(event_id)}, {'$inc': {'attendee_count': team_size}})

    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
    # allowed statuses pipeline: pending|invited|confirmed|paid|cancelled|refunded
    initial_status = "pending"
    reg = {
        "event_id": ObjectId(event_id),
        "user_id": current_user.get('_id'),
        "user_email_snapshot": current_user['email'],
        "status": initial_status,
        # optional relationships (may be filled later): invitation_id, payment_id
        "invitation_id": None,
        "payment_id": None,
        # import meta placeholder if created via import tool later
        "import_meta": None,
        # additional contextual info retained from previous version
        "team_size": team_size,
        "preferences": preferences,
        "created_at": now,
        "updated_at": now,
    }

    res = await db_mod.db.registrations.insert_one(reg)
    created_regs = [str(res.inserted_id)]

    # handle invited_emails: create invitation records per email
    invited = payload.get('invited_emails') or []
    sent_invitations = []
    from ..utils import generate_token_pair
    for em in invited:
        try:
            invite_bytes = int(os.getenv('INVITE_TOKEN_BYTES', os.getenv('TOKEN_BYTES', '18')))
        except (TypeError, ValueError):
            invite_bytes = 18
        token, token_hash_val = generate_token_pair(invite_bytes)
        inv = {
            "registration_id": res.inserted_id,
            "token_hash": token_hash_val,
            "invited_email": em,
            "status": "pending",
            "created_at": now,
            "expires_at": now + __import__('datetime').timedelta(days=30)
        }
        try:
            await db_mod.db.invitations.insert_one(inv)
            base = __import__('os').getenv('BACKEND_BASE_URL', 'http://localhost:8000')
            print(f"[invitation] To {em}: {base}/invitations/{token}")
            sent_invitations.append(em)
        except PyMongoError as exc:
            # log and continue - invitation failure shouldn't block registration
            print(f"[invitation][error] failed to create invitation for {em}: {exc}")

    # Optionally create a payment link if event has a fee
    payment_link = None
    base_fee = event.get('fee_cents', 0) or 0
    chosen_fee_cents = base_fee * team_size
    if chosen_fee_cents and chosen_fee_cents > 0:
        pay = {
            "registration_id": res.inserted_id,
            "amount": chosen_fee_cents / 100.0,
            "currency": 'EUR',
            "status": "pending",
            "provider": 'N/A',
            "meta": {"team_size": team_size},
            "created_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
        }
        p = await db_mod.db.payments.insert_one(pay)
        payment_link = f"/payments/{str(p.inserted_id)}/pay"
        try:
            await db_mod.db.registrations.update_one({"_id": res.inserted_id}, {"$set": {"payment_id": p.inserted_id}})
        except PyMongoError as exc:
            print(f"[payment][error] failed to attach payment id to registration {res.inserted_id}: {exc}")

    return {"status": "registered", "registration_ids": created_regs, "invitations_sent": sent_invitations, "payment_link": payment_link}

@router.post('/{event_id}/recount_attendees')
async def recount_attendees(event_id: str, _=Depends(require_admin)):
    """Recompute attendee_count from registrations that are not cancelled/refunded/expired and update the event."""
    try:
        oid = ObjectId(event_id)
    except (InvalidId, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail='invalid event_id') from exc
    e = await db_mod.db.events.find_one({'_id': oid})
    if not e:
        raise HTTPException(status_code=404, detail='Event not found')
    active_status = {'pending','invited','confirmed','paid'}
    count = 0
    async for r in db_mod.db.registrations.find({'event_id': oid, 'status': {'$in': list(active_status)}}):
        count += 1
    await db_mod.db.events.update_one({'_id': oid}, {'$set': {'attendee_count': count, 'updated_at': datetime.datetime.now(datetime.timezone.utc)}})
    return {'attendee_count': count}

@router.get('/{event_id}/my_plan')
async def get_my_plan(event_id: str, current_user=Depends(get_current_user)):
    """Get the current user's plan."""


    # 1. Fetch the user's plan
    plan = await db_mod.db.plans.find_one({"user_email": current_user['email']})
    if not plan:
        return {"message": "No plan yet (matching not run)"}

    # 2. Fetch the event details
    event_id = plan.get('event_id')
    event = await db_mod.db.events.find_one({"_id": event_id}) if event_id else None
    event_date_str = event.get('date') if event else None

    unlock_minutes = 120  # Unlock window in minutes
    now = datetime.datetime.now(timezone.utc)

    # 3. Parse event date
    event_date = None
    if event_date_str:
        try:
            event_date = (
                datetime.datetime.fromisoformat(event_date_str).date()
                if isinstance(event_date_str, str)
                else event_date_str
            )
        except Exception as e:
            print(f"[DEBUG] Failed to parse event date: {e}")

    # 4. Pre-fetch all user data for hosts
    host_emails = [
        section.get('host_email')
        for section in plan.get('sections', [])
        if section.get('host_email')
    ]
    
    users = {
        u['email']: u
        for u in await db_mod.db.users.find({"email": {"$in": host_emails}}).to_list(None)
    }

    # 5. Build the response
    out = {
        "id": str(plan.get('_id')) if plan.get('_id') is not None else None,
        "event_id": str(event_id) if event_id is not None else None,
        "user_email": plan.get('user_email'),
        "sections": [],
    }

    for section in plan.get('sections', []):

        # 6. Initialize section output
        sec = {
            'meal': section.get('meal'),
            'time': section.get('time'),
            'host_first_name': None,
            'host_location': section.get('host_location'),
            'guests': section.get('guests') or [],
            'chat_room_id': section.get('chat_room_id'),
        }

        # 7. Set host first name
        host_email = section.get('host_email')
        if host_email and host_email in users:
            sec['host_first_name'] = users[host_email].get('first_name')

        # 8. Parse section time
        section_time = section.get('time')
        unlock_time = None
        if section_time and event_date:
            try:
                if isinstance(section_time, str):
                    dt_str = f"{event_date}T{section_time}"
                    unlock_time = datetime.datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)
                elif isinstance(section_time, datetime.datetime):
                    unlock_time = (
                        section_time
                        if section_time.tzinfo
                        else section_time.replace(tzinfo=timezone.utc)
                    )
            except Exception as e:
                print(f"[DEBUG] Failed to parse section time: {e}")

        # 9. Determine address visibility
        show_full_address = False
        if unlock_time:
            unlock_start = unlock_time - timedelta(minutes=unlock_minutes)
            unlock_end = unlock_time + timedelta(days=1)

            if unlock_start <= now <= unlock_end:
                show_full_address = True
            elif now > unlock_end:
                sec['host_location'] = None
                out['sections'].append(sec)
                continue


        # 10. Adjust host_location based on unlock logic
        if sec['host_location']:
            if show_full_address and host_email in users:
                # Fetch full address from user data
                user = users[host_email].get("address_struct", {})
                full_address = {
                    "street": user.get("street"),
                    "street_no": user.get("street_no"),
                    "postal_code": user.get("postal_code"),
                    "city": user.get("city"),
                    "center": sec['host_location'].get("center"),  # Keep coordinates
                    "approx_radius_m": 0,  # Full address
                }
                sec['host_location'] = full_address
            else:
                # Keep only approximate coordinates
                sec['host_location']['approx_radius_m'] = 500

        # 11. Add guests_info if current user is the host
        if host_email and current_user['email'] == host_email and section.get('guests_info'):
            sec['guests_info'] = section.get('guests_info')

        out['sections'].append(sec)
    
    return JSONResponse(content=out)


@router.get('/{event_id}/my_plan')
async def get_my_plan(event_id: str, current_user=Depends(get_current_user)):
    """Get the current user's plan."""


    # 1. Fetch the user's plan
    plan = await db_mod.db.plans.find_one({"user_email": current_user['email']})
    if not plan:
        return {"message": "No plan yet (matching not run)"}

    # 2. Fetch the event details
    event_id = plan.get('event_id')
    event = await db_mod.db.events.find_one({"_id": event_id}) if event_id else None
    event_date_str = event.get('date') if event else None

    unlock_minutes = 120  # Unlock window in minutes
    now = datetime.datetime.now(timezone.utc)

    # 3. Parse event date
    event_date = None
    if event_date_str:
        try:
            event_date = (
                datetime.datetime.fromisoformat(event_date_str).date()
                if isinstance(event_date_str, str)
                else event_date_str
            )
        except Exception as e:
            print(f"[DEBUG] Failed to parse event date: {e}")

    # 4. Pre-fetch all user data for hosts
    host_emails = [
        section.get('host_email')
        for section in plan.get('sections', [])
        if section.get('host_email')
    ]
    
    users = {
        u['email']: u
        for u in await db_mod.db.users.find({"email": {"$in": host_emails}}).to_list(None)
    }

    # 5. Build the response
    out = {
        "id": str(plan.get('_id')) if plan.get('_id') is not None else None,
        "event_id": str(event_id) if event_id is not None else None,
        "user_email": plan.get('user_email'),
        "sections": [],
    }

    for section in plan.get('sections', []):

        # 6. Initialize section output
        sec = {
            'meal': section.get('meal'),
            'time': section.get('time'),
            'host_first_name': None,
            'host_location': section.get('host_location'),
            'guests': section.get('guests') or [],
            'chat_room_id': section.get('chat_room_id'),
        }

        # 7. Set host first name
        host_email = section.get('host_email')
        if host_email and host_email in users:
            sec['host_first_name'] = users[host_email].get('first_name')

        # 8. Parse section time
        section_time = section.get('time')
        unlock_time = None
        if section_time and event_date:
            try:
                if isinstance(section_time, str):
                    dt_str = f"{event_date}T{section_time}"
                    unlock_time = datetime.datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)
                elif isinstance(section_time, datetime.datetime):
                    unlock_time = (
                        section_time
                        if section_time.tzinfo
                        else section_time.replace(tzinfo=timezone.utc)
                    )
            except Exception as e:
                print(f"[DEBUG] Failed to parse section time: {e}")

        # 9. Determine address visibility
        show_full_address = False
        if unlock_time:
            unlock_start = unlock_time - timedelta(minutes=unlock_minutes)
            unlock_end = unlock_time + timedelta(days=1)

            if unlock_start <= now <= unlock_end:
                show_full_address = True
            elif now > unlock_end:
                sec['host_location'] = None
                out['sections'].append(sec)
                continue


        # 10. Adjust host_location based on unlock logic
        if sec['host_location']:
            if show_full_address and host_email in users:
                # Fetch full address from user data
                user = users[host_email].get("address_struct", {})
                full_address = {
                    "street": user.get("street"),
                    "street_no": user.get("street_no"),
                    "postal_code": user.get("postal_code"),
                    "city": user.get("city"),
                    "center": sec['host_location'].get("center"),  # Keep coordinates
                    "approx_radius_m": 0,  # Full address
                }
                sec['host_location'] = full_address
            else:
                # Keep only approximate coordinates
                sec['host_location']['approx_radius_m'] = 500

        # 11. Add guests_info if current user is the host
        if host_email and current_user['email'] == host_email and section.get('guests_info'):
            sec['guests_info'] = section.get('guests_info')

        out['sections'].append(sec)
    
    return JSONResponse(content=out)
