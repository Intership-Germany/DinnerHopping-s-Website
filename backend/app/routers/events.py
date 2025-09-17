from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse, Response
from bson import json_util
from pydantic import BaseModel, Field
from .. import db as db_mod
from ..auth import get_current_user
from ..utils import anonymize_address
from bson.objectid import ObjectId
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

class EventCreate(BaseModel):
    name: str
    date: str
    fee_cents: int = 0
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    organizer: str | None = None
    published: bool = False

class EventOut(BaseModel):
    id: str
    name: str
    date: str
    fee_cents: int

@router.get("/", response_model=list[EventOut])
async def list_events(date: str | None = None, status: str | None = None, lat: float | None = None, lon: float | None = None, radius_m: int | None = None):
    """List events with optional filters:
    - date: exact match
    - status: 'published' or 'draft'
    - lat/lon + radius_m: simple bounding box approx using degrees (approx)
    """
    query = {}
    if date:
        query['date'] = date
    if status:
        if status == 'published':
            query['published'] = True
        elif status == 'draft':
            query['published'] = False
    # simple radius -> degree bounding box
    if lat is not None and lon is not None and radius_m is not None:
        # approx degrees per meter: 1 deg ~ 111_000 m
        delta_deg = radius_m / 111000.0
        query['lat'] = {"$gte": lat - delta_deg, "$lte": lat + delta_deg}
        query['lon'] = {"$gte": lon - delta_deg, "$lte": lon + delta_deg}

    events = []
    async for e in db_mod.db.events.find(query):
        # be defensive: imported or partial documents may miss fields
        ev_name = e.get('name') or 'Unnamed event'
        ev_date = e.get('date') or ''
        ev_fee = e.get('fee_cents', 0)
        events.append(EventOut(id=str(e.get('_id')), name=ev_name, date=ev_date, fee_cents=ev_fee))
    return events


@router.post('/', response_model=EventOut)
async def create_event(payload: EventCreate, x_admin_token: str | None = Header(None)):
    ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'admin-token-change-me')
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail='Forbidden')
    doc = payload.dict()
    # ensure event_id exists to satisfy possible unique index created by imports
    # always set a unique event_id for newly created events
    doc['event_id'] = str(ObjectId())
    res = await db_mod.db.events.insert_one(doc)
    return EventOut(id=str(res.inserted_id), name=doc.get('name'), date=doc.get('date'), fee_cents=doc.get('fee_cents', 0))


@router.get('/{event_id}')
async def get_event(event_id: str, anonymise: bool = True):
    e = await db_mod.db.events.find_one({"_id": ObjectId(event_id)})
    if not e:
        raise HTTPException(status_code=404, detail='Event not found')
    # serialize the whole document, converting ObjectId
    serialized = _serialize(e)
    # ensure id is present as string
    serialized['id'] = str(e.get('_id'))
    # anonymise location by default
    if anonymise:
        lat = e.get('lat')
        lon = e.get('lon')
        if lat is not None and lon is not None:
            serialized['location'] = anonymize_address(lat, lon)
            # remove precise coords if present
            serialized.pop('lat', None)
            serialized.pop('lon', None)
            serialized.pop('address', None)
    return serialized


@router.put('/{event_id}', response_model=EventOut)
async def update_event(event_id: str, payload: EventCreate, x_admin_token: str | None = Header(None)):
    ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'admin-token-change-me')
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail='Forbidden')
    doc = payload.dict()
    await db_mod.db.events.update_one({"_id": ObjectId(event_id)}, {"$set": doc})
    e = await db_mod.db.events.find_one({"_id": ObjectId(event_id)})
    return EventOut(id=str(e['_id']), name=e.get('name'), date=e.get('date'), fee_cents=e.get('fee_cents', 0))


@router.post('/{event_id}/publish')
async def publish_event(event_id: str, x_admin_token: str | None = Header(None)):
    ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'admin-token-change-me')
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail='Forbidden')
    await db_mod.db.events.update_one({"_id": ObjectId(event_id)}, {"$set": {"published": True}})
    return {"status": "published"}

@router.post("/{event_id}/register")
async def register_for_event(event_id: str, payload: dict, current_user=Depends(get_current_user)):
    # payload may include team info, invited_emails and preferences override
    # invited_emails: list of emails to invite (they will receive an invitation token)
    event = await db_mod.db.events.find_one({"_id": ObjectId(event_id)})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # avoid duplicate registrations for same user/event
    existing = await db_mod.db.registrations.find_one({"event_id": ObjectId(event_id), "user_email": current_user['email']})
    if existing:
        return {"status": "already_registered"}

    team_size = int(payload.get('team_size', 1))
    preferences = payload.get('preferences', current_user.get('preferences', {}))

    reg = {
        "event_id": ObjectId(event_id),
        "user_email": current_user['email'],
        "team_size": team_size,
        "preferences": preferences,
        "created_at": __import__('datetime').datetime.utcnow(),
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
            "token": token,
            "invited_email": em,
            "event_id": ObjectId(event_id),
            "inviter_email": current_user['email'],
            "status": "pending",
            "created_at": __import__('datetime').datetime.utcnow(),
        }
        try:
            await db_mod.db.invitations.insert_one(inv)
            # In dev: print invitation link
            base = __import__('os').getenv('BACKEND_BASE_URL', 'http://localhost:8000')
            print(f"[invitation] To {em}: {base}/invitations/{token}")
            sent_invitations.append(em)
        except Exception:
            # ignore duplicates or insertion errors for now
            pass

    # Optionally create a payment link if event has a fee
    payment_link = None
    if event.get('fee_cents', 0) > 0:
        # create a simple payment record and a fake link (replace with real provider integration later)
        pay = {"registration_id": res.inserted_id, "amount_cents": event.get('fee_cents', 0), "status": "pending", "created_at": __import__('datetime').datetime.utcnow()}
        p = await db_mod.db.payments.insert_one(pay)
        payment_link = f"/payments/{str(p.inserted_id)}/pay"

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
