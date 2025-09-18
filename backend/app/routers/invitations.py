from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, EmailStr
from .. import db as db_mod
from ..auth import get_current_user, hash_password
import secrets

router = APIRouter()


class AcceptPayload(BaseModel):
    name: str | None = None
    password: str | None = None


@router.post('/{token}/accept')
async def accept_invitation(token: str, payload: AcceptPayload, authorization: str | None = Header(None)):
    """Accept an invitation and create a registration linked to the accepting account.

    If the user is authenticated (current_user), the invitation is linked to that account.
    Otherwise the endpoint requires name+password to create a new user for the invited email.
    """
    inv = await db_mod.db.invitations.find_one({"token": token})
    if not inv:
        raise HTTPException(status_code=404, detail='Invitation not found')
    if inv.get('status') != 'pending':
        raise HTTPException(status_code=400, detail='Invitation already used')

    invited_email = inv.get('invited_email')

    user_email = None
    if authorization:
        # try to resolve JWT -> user
        try:
            cu = await get_current_user(token=authorization.split(' ')[1])
            user_email = cu['email']
        except Exception:
            user_email = None
    if not user_email:
        # create account flow: require password and name
        if not payload.password or not payload.name:
            raise HTTPException(status_code=400, detail='Provide name and password to create an account')
        existing = await db_mod.db.users.find_one({"email": invited_email})
        if existing:
            # user exists but not authenticated — instruct to login
            raise HTTPException(status_code=400, detail='Account already exists; please login and accept the invitation while authenticated')
        user_doc = {"email": invited_email, "name": payload.name, "password": hash_password(payload.password), "is_verified": True}
        await db_mod.db.users.insert_one(user_doc)
        user_email = invited_email

    # create registration for the invited user
    event_id = inv.get('event_id')
    now = __import__('datetime').datetime.utcnow()
    reg = {
        "event_id": event_id,
        "user_email_snapshot": user_email,
        "status": "invited",
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
        except Exception:
            pass

    # mark invitation accepted
    await db_mod.db.invitations.update_one({"_id": inv['_id']}, {"$set": {"status": "accepted", "accepted_by": user_email, "accepted_at": now}})

    return {"status": "accepted", "user_email": user_email}
