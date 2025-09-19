from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, EmailStr
from app import db as db_mod
from app.auth import get_current_user, hash_password
from app.utils import hash_token, generate_token_pair
from bson.objectid import ObjectId
from bson.errors import InvalidId
import secrets
from pymongo.errors import PyMongoError, DuplicateKeyError
from fastapi import HTTPException as FastAPIHTTPException
import os
import datetime
from typing import Optional

router = APIRouter()


class AcceptPayload(BaseModel):
    name: Optional[str] = None
    password: Optional[str] = None


class CreateInvitation(BaseModel):
    registration_id: str
    invited_email: EmailStr
    expires_days: Optional[int] = 30


def _serialize_inv(inv: dict) -> dict:
    out = {k: (str(v) if isinstance(v, ObjectId) else v) for k, v in inv.items()}
    # ensure id field
    if out.get('_id'):
        out['id'] = out['_id']
    # do not expose token or token_hash in serialized output
    out.pop('token', None)
    out.pop('token_hash', None)
    return out


@router.post('/{token}/accept')
async def accept_invitation(token: str, payload: AcceptPayload, authorization: str | None = Header(None)):
    """Accept an invitation and create a registration linked to the accepting account.

    If the user is authenticated (current_user), the invitation is linked to that account.
    Otherwise the endpoint requires name+password to create a new user for the invited email.
    """
    # match invitation by token hash to avoid storing plaintext tokens in DB
    token_hash = hash_token(token)
    inv = await db_mod.db.invitations.find_one({"token_hash": token_hash})
    if not inv:
        raise HTTPException(status_code=404, detail='Invitation not found')

    # check expiry
    now = datetime.datetime.utcnow()
    expires_at = inv.get('expires_at')
    if expires_at and isinstance(expires_at, datetime.datetime) and expires_at < now:
        # mark expired
        await db_mod.db.invitations.update_one({"_id": inv['_id']}, {"$set": {"status": "expired", "expired_at": now}})
        raise HTTPException(status_code=400, detail='Invitation expired')

    if inv.get('status') != 'pending':
        raise HTTPException(status_code=400, detail='Invitation already used or invalid')

    invited_email = inv.get('invited_email')

    user_email = None
    if authorization:
        # try to resolve JWT -> user
        try:
            cu = await get_current_user(token=authorization.split(' ')[1])
            user_email = cu['email']
        except FastAPIHTTPException:
            user_email = None
    if not user_email:
        # create account flow: require password and name
        if not payload.password or not payload.name:
            raise HTTPException(status_code=400, detail='Provide name and password to create an account')
        existing = await db_mod.db.users.find_one({"email": invited_email})
        if existing:
            # user exists but not authenticated — instruct to login
            raise HTTPException(status_code=400, detail='Account already exists; please login and accept the invitation while authenticated')
        now = datetime.datetime.utcnow()
        user_doc = {
            "email": invited_email,
            "name": payload.name,
            "password_hash": hash_password(payload.password),
            # invited users are implicitly verified via invitation acceptance
            "email_verified": True,
            "is_verified": True,  # backward compatibility
            "roles": ['user'],
            "preferences": {},
            "failed_login_attempts": 0,
            "created_at": now,
            "updated_at": now,
        }
        await db_mod.db.users.insert_one(user_doc)
        user_email = invited_email

    # create registration for the invited user (link invitation_id)
    event_id = inv.get('event_id')
    now = datetime.datetime.utcnow()
    reg = {
        "event_id": event_id,
        "user_email_snapshot": user_email,
        "status": "invited",
        "invitation_id": inv.get('_id'),
        "user_id": None,
        "team_size": 1,
        "preferences": {},
        "created_at": now,
        "updated_at": now
    }
    reg_res = await db_mod.db.registrations.insert_one(reg)
    # retro-link invitation with registration if missing
    if not inv.get('registration_id'):
        try:
            await db_mod.db.invitations.update_one({"_id": inv['_id']}, {"$set": {"registration_id": reg_res.inserted_id}})
        except PyMongoError:
            # best-effort: do not fail the accept flow if retro-link fails
            pass

    # mark invitation accepted
    await db_mod.db.invitations.update_one({"_id": inv['_id']}, {"$set": {"status": "accepted", "accepted_by": user_email, "accepted_at": now}})

    return {"status": "accepted", "user_email": user_email, "registration_id": str(reg_res.inserted_id)}


@router.post('/')
async def create_invitation(payload: CreateInvitation, current_user=Depends(get_current_user)):
    """Create an invitation for a registration. Only the registration owner or admin may create invitations."""
    try:
        reg_oid = ObjectId(payload.registration_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail='invalid registration_id') from exc

    reg = await db_mod.db.registrations.find_one({"_id": reg_oid})
    if not reg:
        raise HTTPException(status_code=404, detail='Registration not found')

    roles = current_user.get('roles') or []
    is_admin = 'admin' in roles
    # ensure requester owns the registration or is admin
    if not is_admin and reg.get('user_id') != current_user.get('_id'):
        raise HTTPException(status_code=403, detail='Forbidden')

    now = datetime.datetime.utcnow()
    expires_days = payload.expires_days or 30
    expires_at = now + datetime.timedelta(days=expires_days)

    # generate unique token (retry a few times if collision)
    for _ in range(3):
        token, token_hash_val = generate_token_pair(18)
        inv = {
            "registration_id": reg_oid,
            "token_hash": token_hash_val,
            "invited_email": payload.invited_email.lower(),
            "status": "pending",
            "created_at": now,
            "expires_at": expires_at,
            "created_by": current_user.get('email'),
            "created_by_user_id": current_user.get('_id')
        }
        try:
            res = await db_mod.db.invitations.insert_one(inv)
            base = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
            return {"id": str(res.inserted_id), "token": token, "link": f"{base}/invitations/{token}"}
        except DuplicateKeyError:
            # token collision, generate another
            continue
        except PyMongoError:
            # transient DB error
            continue

    raise HTTPException(status_code=500, detail='Could not create invitation')


@router.get('/')
async def list_invitations(registration_id: Optional[str] = None, current_user=Depends(get_current_user)):
    """List invitations. Owners see invitations for their registrations; admins can list by registration or all if no filter provided."""
    roles = current_user.get('roles') or []
    is_admin = 'admin' in roles

    query = {}
    if registration_id:
        try:
            query['registration_id'] = ObjectId(registration_id)
        except (InvalidId, TypeError) as exc:
            raise HTTPException(status_code=400, detail='invalid registration_id') from exc
    else:
        if not is_admin:
            # default: show invitations created for or by the current user
            query = {"$or": [{"invited_email": current_user.get('email')}, {"created_by_user_id": current_user.get('_id')}]}

    out = []
    async for inv in db_mod.db.invitations.find(query).sort([('created_at', -1)]):
        out.append(_serialize_inv(inv))
    return out


@router.post('/{inv_id}/revoke')
async def revoke_invitation(inv_id: str, current_user=Depends(get_current_user)):
    """Revoke a pending invitation. Only the registration owner or admin can revoke."""
    try:
        oid = ObjectId(inv_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail='invalid invitation id') from exc

    inv = await db_mod.db.invitations.find_one({"_id": oid})
    if not inv:
        raise HTTPException(status_code=404, detail='Invitation not found')

    roles = current_user.get('roles') or []
    is_admin = 'admin' in roles
    # check ownership
    reg = None
    if inv.get('registration_id'):
        reg = await db_mod.db.registrations.find_one({"_id": inv.get('registration_id')})

    if not is_admin and reg and reg.get('user_id') != current_user.get('_id'):
        # if registration exists and user is not owner -> forbidden
        raise HTTPException(status_code=403, detail='Forbidden')

    if inv.get('status') != 'pending':
        raise HTTPException(status_code=400, detail='Only pending invitations can be revoked')

    now = datetime.datetime.utcnow()
    await db_mod.db.invitations.update_one({"_id": oid}, {"$set": {"status": "revoked", "revoked_at": now, "revoked_by": current_user.get('email')}})
    return {"status": "revoked"}
