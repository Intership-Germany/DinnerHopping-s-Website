from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Literal
from app import db as db_mod
from app.auth import get_current_user
from app.utils import anonymize_address, encrypt_address, anonymize_public_address
from bson.objectid import ObjectId
from bson.errors import InvalidId
from pymongo.errors import PyMongoError
from fastapi import Header
import os

def _serialize(obj):
    from bson import ObjectId as _OID
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, _OID):
        return str(obj)
    return obj

router = APIRouter()

class LocationIn(BaseModel):
    address: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


class EventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    date: str  # ISO date string
    capacity: Optional[int] = None
    location: Optional[LocationIn] = None
    organizer_id: Optional[str] = None  # user id (ObjectId as str)
    status: Optional[Literal['draft', 'published', 'closed', 'cancelled']] = 'draft'

class LocationOut(BaseModel):
    address_public: Optional[str] = None
    point: Optional[dict] = None


class EventOut(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    date: str
    location: Optional[LocationOut] = None
    capacity: Optional[int] = None
    attendee_count: int = 0
    status: Optional[Literal['draft', 'published', 'closed', 'cancelled']] = 'draft'
    organizer_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

@router.get("/", response_model=list[EventOut])
async def list_events(date: Optional[str] = None, status: Optional[Literal['draft', 'published', 'closed', 'cancelled']] = None, lat: Optional[float] = None, lon: Optional[float] = None, radius_m: Optional[int] = None):
    """List events with optional filters:
    - date: exact match
    - status: 'published' or 'draft'
    - lat/lon + radius_m: simple bounding box approx using degrees (approx)
    """
    query = {}
    if date:
        query['date'] = date
    if status:
        query['status'] = status
    # simple radius -> degree bounding box
    if lat is not None and lon is not None and radius_m is not None:
        # approx degrees per meter: 1 deg ~ 111_000 m
        delta_deg = radius_m / 111000.0
        query['lat'] = {"$gte": lat - delta_deg, "$lte": lat + delta_deg}
        query['lon'] = {"$gte": lon - delta_deg, "$lte": lon + delta_deg}

    events_resp = []
    async for e in db_mod.db.events.find(query):
        events_resp.append(EventOut(
            id=str(e.get('_id')),
            title=e.get('title') or e.get('name') or 'Untitled',
            description=e.get('description'),
            date=e.get('date') or '',
            location=e.get('location'),
            capacity=e.get('capacity'),
            attendee_count=e.get('attendee_count', 0),
            status=e.get('status'),
            organizer_id=str(e.get('organizer_id')) if e.get('organizer_id') is not None else None,
            created_by=str(e.get('created_by')) if e.get('created_by') is not None else None,
            created_at=e.get('created_at'),
            updated_at=e.get('updated_at')
        ))
    return events_resp


@router.post('/', response_model=EventOut)
async def create_event(payload: EventCreate, x_admin_token: str | None = Header(None)):
    ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'admin-token-change-me')
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail='Forbidden')
    now = __import__('datetime').datetime.utcnow()
    doc = payload.dict()
    # build location subdocument if provided
    loc_in = doc.pop('location', None)
    location = None
    if isinstance(loc_in, dict):
        address = loc_in.get('address')
        lat = loc_in.get('lat')
        lon = loc_in.get('lon')
        if address:
            location = {
                'address_encrypted': encrypt_address(address),
                'address_public': anonymize_public_address(address),
                'point': {'type': 'Point', 'coordinates': [lon, lat]} if lat is not None and lon is not None else None
            }
        elif lat is not None and lon is not None:
            location = {
                'address_encrypted': None,
                'address_public': None,
                'point': {'type': 'Point', 'coordinates': [lon, lat]}
            }
    if location is not None:
        doc['location'] = location

    # convert organizer_id to ObjectId if present
    if doc.get('organizer_id'):
        try:
            doc['organizer_id'] = ObjectId(doc['organizer_id'])
        except (InvalidId, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail='invalid organizer_id') from exc

    # default fields per schema
    doc['attendee_count'] = 0
    doc['created_at'] = now
    doc['updated_at'] = now
    # set created_by to organizer if available; otherwise leave None
    doc['created_by'] = doc.get('organizer_id') if doc.get('organizer_id') is not None else None

    # enforce status default (pydantic already defaults but ensure correct)
    if not doc.get('status'):
        doc['status'] = 'draft'

    res = await db_mod.db.events.insert_one(doc)
    return EventOut(id=str(res.inserted_id), title=doc.get('title'), description=doc.get('description'), date=doc.get('date'), location=doc.get('location'), capacity=doc.get('capacity'), attendee_count=0, status=doc.get('status'), organizer_id=str(doc['organizer_id']) if doc.get('organizer_id') is not None else None, created_by=str(doc['created_by']) if doc.get('created_by') is not None else None, created_at=doc.get('created_at'), updated_at=doc.get('updated_at'))


@router.get('/{event_id}')
async def get_event(event_id: str, anonymise: bool = True):
    e = await db_mod.db.events.find_one({"_id": ObjectId(event_id)})
    if not e:
        raise HTTPException(status_code=404, detail='Event not found')
    # serialize the whole document, converting ObjectId
    serialized = _serialize(e)
    # ensure id is present as string
    serialized['id'] = str(e.get('_id'))
    # anonymise location info
    loc = e.get('location') if isinstance(e.get('location'), dict) else None
    if anonymise and loc:
        # if address_public present keep only that
        pub = loc.get('address_public')
        if pub:
            serialized['location'] = {'address_public': pub}
        else:
            # derive anonymised from point coordinates if present
            pt = loc.get('point') if isinstance(loc.get('point'), dict) else None
            if pt and isinstance(pt.get('coordinates'), list) and len(pt['coordinates']) == 2:
                lon, lat = pt['coordinates']
                if lat is not None and lon is not None:
                    serialized['location'] = anonymize_address(lat, lon)
    else:
        serialized['location'] = loc
    # include organizer_id/created_by as strings
    if e.get('organizer_id') is not None:
        serialized['organizer_id'] = str(e.get('organizer_id'))
    if e.get('created_by') is not None:
        serialized['created_by'] = str(e.get('created_by'))
    return serialized


@router.put('/{event_id}', response_model=EventOut)
async def update_event(event_id: str, payload: EventCreate, x_admin_token: str | None = Header(None)):
    ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'admin-token-change-me')
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail='Forbidden')
    update = payload.dict()
    # handle location update
    loc_in = update.pop('location', None)
    location = None
    if isinstance(loc_in, dict):
        address = loc_in.get('address')
        lat = loc_in.get('lat')
        lon = loc_in.get('lon')
        if address:
            location = {
                'address_encrypted': encrypt_address(address),
                'address_public': anonymize_public_address(address),
                'point': {'type': 'Point', 'coordinates': [lon, lat]} if lat is not None and lon is not None else None
            }
        elif lat is not None and lon is not None:
            location = {
                'address_encrypted': None,
                'address_public': None,
                'point': {'type': 'Point', 'coordinates': [lon, lat]}
            }
    if location is not None:
        update['location'] = location

    # convert organizer_id if provided
    if update.get('organizer_id'):
        try:
            update['organizer_id'] = ObjectId(update['organizer_id'])
        except (InvalidId, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail='invalid organizer_id') from exc

    update['updated_at'] = __import__('datetime').datetime.utcnow()
    await db_mod.db.events.update_one({"_id": ObjectId(event_id)}, {"$set": update})
    e = await db_mod.db.events.find_one({"_id": ObjectId(event_id)})
    return EventOut(id=str(e['_id']), title=e.get('title') or e.get('name'), description=e.get('description'), date=e.get('date'), location=e.get('location'), capacity=e.get('capacity'), attendee_count=e.get('attendee_count', 0), status=e.get('status'), organizer_id=str(e.get('organizer_id')) if e.get('organizer_id') is not None else None, created_by=str(e.get('created_by')) if e.get('created_by') is not None else None, created_at=e.get('created_at'), updated_at=e.get('updated_at'))


@router.post('/{event_id}/publish')
async def publish_event(event_id: str, x_admin_token: str | None = Header(None)):
    ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'admin-token-change-me')
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail='Forbidden')
    await db_mod.db.events.update_one({"_id": ObjectId(event_id)}, {"$set": {"status": "published", "updated_at": __import__('datetime').datetime.utcnow()}})
    return {"status": "published"}

@router.post("/{event_id}/register")
async def register_for_event(event_id: str, payload: dict, current_user=Depends(get_current_user)):
    # payload may include team info, invited_emails and preferences override
    # invited_emails: list of emails to invite (they will receive an invitation token)
    event = await db_mod.db.events.find_one({"_id": ObjectId(event_id)})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

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

    now = __import__('datetime').datetime.utcnow()
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
    import secrets
    for em in invited:
        token = secrets.token_urlsafe(24)
        inv = {
            "registration_id": res.inserted_id,
            "token": token,
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
    if event.get('fee_cents', 0) > 0:
        # create a simple payment record and a fake link (replace with real provider integration later)
        pay = {
            "registration_id": res.inserted_id,
            "amount": event.get('fee_cents', 0) / 100.0,
            "currency": 'EUR',
            "status": "pending",
            "provider": 'dev-local',
            "meta": {},
            "created_at": __import__('datetime').datetime.utcnow()
        }
        p = await db_mod.db.payments.insert_one(pay)
        payment_link = f"/payments/{str(p.inserted_id)}/pay"
        try:
            await db_mod.db.registrations.update_one({"_id": res.inserted_id}, {"$set": {"payment_id": p.inserted_id}})
        except PyMongoError as exc:
            print(f"[payment][error] failed to attach payment id to registration {res.inserted_id}: {exc}")

    return {"status": "registered", "registration_ids": created_regs, "invitations_sent": sent_invitations, "payment_link": payment_link}

async def get_my_plan(current_user):
    # Fetch plan document and return a clean JSON-serializable representation
    plan = await db_mod.db.plans.find_one({"user_email": current_user['email']})
    if not plan:
        return {"message": "No plan yet (matching not run)"}

    out = {
        "id": str(plan.get('_id')) if plan.get('_id') is not None else None,
        "event_id": str(plan.get('event_id')) if plan.get('event_id') is not None else None,
        "user_email": plan.get('user_email'),
        "sections": []
    }

    for section in plan.get('sections', []):
        sec = {
            'meal': section.get('meal'),
            'time': section.get('time'),
            'host_email': None,
            'host_location': None,
            'guests': []
        }
        host = section.get('host') or {}
        if isinstance(host, dict):
            sec['host_email'] = host.get('email')
            lat = host.get('lat')
            lon = host.get('lon')
            if lat is not None and lon is not None:
                sec['host_location'] = anonymize_address(lat, lon)

        guests = section.get('guests') or []
        # In the simple stub guests are emails; ensure strings
        sec['guests'] = [g for g in guests]

        out['sections'].append(sec)

    return JSONResponse(content=out)
