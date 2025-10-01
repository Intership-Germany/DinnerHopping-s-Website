from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, EmailStr
from app import db as db_mod
from app.auth import get_current_user, hash_password
from app.utils import hash_token, generate_token_pair, require_event_published, require_registration_owner_or_admin, send_email
from bson.objectid import ObjectId
from bson.errors import InvalidId
import secrets
from pymongo.errors import PyMongoError, DuplicateKeyError
import os
import datetime
from typing import Optional
from fastapi import Request
from fastapi.responses import RedirectResponse
import urllib.parse

######### Router / Endpoints #########

router = APIRouter()


class AcceptPayload(BaseModel):
    # Collect first/last name separately (legacy 'name' removed)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
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
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = inv.get('expires_at')
    if expires_at and isinstance(expires_at, datetime.datetime) and expires_at < now:
        # mark expired
        await db_mod.db.invitations.update_one({"_id": inv['_id']}, {"$set": {"status": "expired", "expired_at": now}})
        raise HTTPException(status_code=400, detail='Invitation expired')

    if inv.get('status') != 'pending':
        raise HTTPException(status_code=400, detail='Invitation already used or invalid')

    invited_email = inv.get('invited_email')

    # ensure event still accepts registrations
    if inv.get('event_id'):
        await require_event_published(inv.get('event_id'))

    user_email = None
    if authorization:
        # try to resolve JWT -> user
        try:
            # get_current_user expects (request, token). We don't have a Request here,
            # but the implementation will accept None and use the provided token.
            cu = await get_current_user(None, authorization.split(' ', 1)[1])
            user_email = cu.get('email')
        except HTTPException:
            user_email = None
    if not user_email:
        # create account flow: require password and both first & last name
        if not payload.password or not payload.first_name or not payload.last_name:
            raise HTTPException(status_code=400, detail='Provide first_name, last_name and password to create an account')
        existing = await db_mod.db.users.find_one({"email": invited_email})
        if existing:
            # user exists but not authenticated — instruct to login
            raise HTTPException(status_code=400, detail='Account already exists; please login and accept the invitation while authenticated')
        now = datetime.datetime.now(datetime.timezone.utc)
        user_doc = {
            "email": invited_email,
            "first_name": payload.first_name.strip() if payload.first_name else None,
            "last_name": payload.last_name.strip() if payload.last_name else None,
            "password_hash": hash_password(payload.password),
            # invited users created via accept require email verification for security
            "email_verified": False,  # require explicit verification
            "roles": ['user'],
            "preferences": {},
            "failed_login_attempts": 0,
            "lockout_until": None,
            "lat": None,
            "lon": None,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        await db_mod.db.users.insert_one(user_doc)
        user_email = invited_email
        
        # Send email verification for security (prevent account hijacking)
        try:
            from app.utils import generate_and_send_verification
            await generate_and_send_verification(invited_email)
        except Exception:
            # If email verification fails, still continue but log it
            pass

    # create registration for the invited user (link invitation_id), avoid duplicate registration
    event_id = inv.get('event_id')
    now = datetime.datetime.now(datetime.timezone.utc)
    existing = None
    try:
        existing = await db_mod.db.registrations.find_one({'event_id': event_id, 'user_email_snapshot': user_email})
    except PyMongoError:
        existing = None
    if existing:
        reg_res = existing
    else:
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

    # ensure a pending payment exists for the invitee's registration if event has a fee
    try:
        ev = await db_mod.db.events.find_one({"_id": event_id}) if event_id else None
        fee_cents = (ev or {}).get('fee_cents', 0)
        reg_oid = reg_res.inserted_id if hasattr(reg_res, 'inserted_id') else reg_res.get('_id')
        if fee_cents and fee_cents > 0 and reg_oid:
            existing_payment = await db_mod.db.payments.find_one({"registration_id": reg_oid})
            if not existing_payment:
                pay = {
                    "registration_id": reg_oid,
                    "amount": float(fee_cents) / 100.0,
                    "currency": 'EUR',
                    "status": "pending",
                    "provider": 'N/A',
                    "meta": {"reason": "invite_accepted"},
                    "created_at": datetime.datetime.now(datetime.timezone.utc)
                }
                p = await db_mod.db.payments.insert_one(pay)
                try:
                    await db_mod.db.registrations.update_one({"_id": reg_oid}, {"$set": {"payment_id": p.inserted_id}})
                except PyMongoError:
                    pass
    except PyMongoError:
        pass

    # mark invitation accepted but pending email verification if account was created
    user_created_account = not authorization  # if no auth token, we created a new account
    invitation_status = "pending_verification" if user_created_account else "accepted"
    await db_mod.db.invitations.update_one({"_id": inv['_id']}, {"$set": {"status": invitation_status, "accepted_by": user_email, "accepted_at": now}})

    reg_id = str(reg_res.inserted_id) if hasattr(reg_res, 'inserted_id') else str(reg_res.get('_id'))
    response = {"status": invitation_status, "user_email": user_email, "registration_id": reg_id}
    
    if user_created_account:
        response["message"] = "Account created. Please verify your email address to complete the invitation acceptance."
    
    return response


@router.post('/')
async def create_invitation(payload: CreateInvitation, current_user=Depends(get_current_user)):
    """Create an invitation for a registration. Only the registration owner or admin may create invitations.

        Additional behavior implemented:
        - If the invited email is not associated with an existing user, a provisional user account
            is created with a temporary password (best-effort). The provisional account is marked
            as email_verified to streamline invitation acceptance. This is a pragmatic choice and
            can be changed to require email verification via the usual flow.
        - An invitation email is sent to the invited address with the token link. Sending is
            best-effort and will not cause the endpoint to fail if SMTP is unavailable.
    """
    try:
        reg_oid = ObjectId(payload.registration_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail='invalid registration_id') from exc

    # ensure requester owns the registration or is admin
    reg = await require_registration_owner_or_admin(current_user, reg_oid)

    now = datetime.datetime.now(datetime.timezone.utc)
    expires_days = payload.expires_days or 30
    expires_at = now + datetime.timedelta(days=expires_days)

    # generate unique token (retry a few times if collision)
    # Prevent the same inviter from inviting the same email for the same event more than once
    event_id = reg.get('event_id')
    existing_inv = await db_mod.db.invitations.find_one({
        'invited_email': payload.invited_email.lower(),
        'event_id': event_id,
        'created_by_user_id': current_user.get('_id')
    })
    if existing_inv:
        raise HTTPException(status_code=400, detail='You have already invited this user for this event')
    # check if invited user already exists
    invited_email_lc = payload.invited_email.lower()
    existing_user = await db_mod.db.users.find_one({"email": invited_email_lc})
    user_created = False
    temp_password = None
    if not existing_user:
        # create a provisional user so the invited person has an account
        # use a randomly generated temporary password and mark email_verified=True
        temp_password = secrets.token_urlsafe(12)
        user_doc = {
            "email": invited_email_lc,
            "name": invited_email_lc.split('@', 1)[0],
            "password_hash": hash_password(temp_password),
            "email_verified": True,
            "roles": ['user'],
            "preferences": {},
            "failed_login_attempts": 0,
            "lockout_until": None,
            "lat": None,
            "lon": None,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        try:
            await db_mod.db.users.insert_one(user_doc)
            user_created = True
        except DuplicateKeyError:
            # race: user created concurrently
            existing_user = await db_mod.db.users.find_one({"email": invited_email_lc})
            user_created = False
        except PyMongoError:
            # best-effort: if user creation fails, continue without blocking invitation
            user_created = False

    # generate token and insert invitation (retry on token collision)
    try:
        invite_bytes = int(os.getenv('INVITE_TOKEN_BYTES', os.getenv('TOKEN_BYTES', '18')))
    except (TypeError, ValueError):
        invite_bytes = 18
    for _ in range(3):
        token, token_hash_val = generate_token_pair(invite_bytes)
        inv = {
            "registration_id": reg_oid,
            "event_id": event_id,
            "token_hash": token_hash_val,
            "invited_email": invited_email_lc,
            "status": "pending",
            "created_at": now,
            "expires_at": expires_at,
            "created_by": current_user.get('email'),
            "created_by_user_id": current_user.get('_id')
        }
        try:
            res = await db_mod.db.invitations.insert_one(inv)
            base = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
            link = f"{base}/invitations/{token}"

            # send invitation email (best-effort)
            if user_created:
                subject = "You've been invited to an event on DinnerHopping — account created"
                body = (
                    f"Hi,\n\nYou have been invited to join an event on DinnerHopping. An account has been created for you with the following temporary credentials:\n\n"
                    f"Email: {invited_email_lc}\nTemporary password: {temp_password}\n\nPlease log in and change your password after first sign-in. You can also accept the invitation directly by visiting:\n{link}\n\n"
                    "If you didn't expect this, please ignore this email.\n\nThanks,\nDinnerHopping Team"
                )
            else:
                subject = "You've been invited to an event on DinnerHopping"
                body = (
                    f"Hi,\n\nYou have been invited to join an event on DinnerHopping. To accept the invitation, please log in to your account and visit the invitations page, or click the link below to accept while logged in:\n{link}\n\n"
                    "If you don't have an account, you can register using the same email address.\n\nThanks,\nDinnerHopping Team"
                )
            # fire-and-forget but await to surface SMTP errors in logs (send_email handles its own failures)
            await send_email(to=invited_email_lc, subject=subject, body=body, category='invitation')

            return {"id": str(res.inserted_id), "token": token, "link": link}
        except DuplicateKeyError:
            # token collision, try again
            continue
        except PyMongoError:
            # transient DB error — retry
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

    # check ownership via helper
    if inv.get('registration_id'):
        await require_registration_owner_or_admin(current_user, inv.get('registration_id'))

    if inv.get('status') != 'pending':
        raise HTTPException(status_code=400, detail='Only pending invitations can be revoked')

    now = datetime.datetime.now(datetime.timezone.utc)
    await db_mod.db.invitations.update_one({"_id": oid}, {"$set": {"status": "revoked", "revoked_at": now, "revoked_by": current_user.get('email')}})
    return {"status": "revoked"}


@router.get('/{token}')
async def view_invitation(token: str, request: Request):
    """View an invitation by token. If the requester is not authenticated, redirect to login page.

    Auth is checked using Authorization header (Bearer) or access_token cookie. The redirect
    includes a `next` query parameter pointing back to the original invitation URL so the
    frontend can continue the flow after login.
    """
    # try resolve current user from Authorization header or cookie
    user = None
    authz = request.headers.get('authorization') or ''
    if authz.lower().startswith('bearer '):
        try:
            user = await get_current_user(request, authz.split(' ', 1)[1])
        except HTTPException:
            user = None
    else:
        cookie_token = request.cookies.get('__Host-access_token') or request.cookies.get('access_token')
        if cookie_token:
            try:
                user = await get_current_user(request, token=cookie_token)
            except HTTPException:
                user = None

    if not user:
        # Instead of including sensitive token in URL, create a temporary login state
        # and redirect with a safe identifier
        token_hash = hash_token(token)
        inv = await db_mod.db.invitations.find_one({"token_hash": token_hash})
        if not inv:
            raise HTTPException(status_code=404, detail='Invitation not found')
        
        # Create a temporary login state record
        import secrets
        temp_state = secrets.token_urlsafe(32)
        state_doc = {
            'state_id': temp_state,
            'invitation_id': inv['_id'],
            'created_at': datetime.datetime.utcnow(),
            'expires_at': datetime.datetime.utcnow() + datetime.timedelta(minutes=30),
            'used': False
        }
        await db_mod.db.invitation_login_states.insert_one(state_doc)
        
        # Redirect with safe state parameter instead of token
        frontend_base = (os.getenv('FRONTEND_BASE_URL') or '').rstrip('/')
        if frontend_base:
            login_path = f"{frontend_base}/login.html?invitation_state={temp_state}"
        else:
            login_path = f"/login?invitation_state={temp_state}"
        return RedirectResponse(login_path, status_code=303)

    # authenticated: return invitation metadata (do not expose token hash)
    token_hash = hash_token(token)
    inv = await db_mod.db.invitations.find_one({"token_hash": token_hash})
    if not inv:
        raise HTTPException(status_code=404, detail='Invitation not found')
    base = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
    out = _serialize_inv(inv)
    out['accept_link'] = f"{base}/invitations/{token}/accept"
    return out


@router.get('/by-state/{state_id}')
async def get_invitation_by_state(state_id: str, current_user=Depends(get_current_user)):
    """Get invitation details using temporary login state (safer than token in URL)."""
    # Find the temporary state record
    state_doc = await db_mod.db.invitation_login_states.find_one({'state_id': state_id, 'used': False})
    if not state_doc:
        raise HTTPException(status_code=404, detail='Invalid or expired invitation state')
    
    # Check if state has expired
    if state_doc.get('expires_at') and datetime.datetime.utcnow() > state_doc.get('expires_at'):
        raise HTTPException(status_code=404, detail='Invitation state expired')
    
    # Get the invitation
    inv = await db_mod.db.invitations.find_one({"_id": state_doc['invitation_id']})
    if not inv:
        raise HTTPException(status_code=404, detail='Invitation not found')
    
    # Mark state as used
    await db_mod.db.invitation_login_states.update_one(
        {'_id': state_doc['_id']}, 
        {'$set': {'used': True, 'used_at': datetime.datetime.utcnow(), 'used_by': current_user.get('email')}}
    )
    
    # Return invitation metadata (do not expose token hash)
    out = _serialize_inv(inv)
    # Don't include accept_link since this is for already authenticated users
    return out


@router.get('/{token}/accept')
async def accept_invitation_via_link(token: str, request: Request):
    """Accept invitation via email link without needing interactive login.

    This endpoint mirrors the POST accept behavior but does not require the caller to
    provide name/password or an Authorization header. It will create a provisional
    account if necessary and mark the invitation as accepted. After success it will
    redirect the user to a frontend success page (FRONTEND_BASE_URL) if available,
    otherwise return JSON.
    """
    token_hash = hash_token(token)
    inv = await db_mod.db.invitations.find_one({"token_hash": token_hash})
    if not inv:
        raise HTTPException(status_code=404, detail='Invitation not found')

    # check expiry and status
    now = datetime.datetime.utcnow()
    expires_at = inv.get('expires_at')
    if expires_at and isinstance(expires_at, datetime.datetime) and expires_at < now:
        # mark expired
        await db_mod.db.invitations.update_one({"_id": inv['_id']}, {"$set": {"status": "expired", "expired_at": now}})
        raise HTTPException(status_code=400, detail='Invitation expired')
    if inv.get('status') != 'pending':
        raise HTTPException(status_code=400, detail='Invitation already used or invalid')

    invited_email = inv.get('invited_email')
    # ensure event still accepts registrations
    if inv.get('event_id'):
        await require_event_published(inv.get('event_id'))

    # ensure or create user
    user_email = invited_email
    existing = await db_mod.db.users.find_one({"email": invited_email})
    provisional_created = False
    temp_password = None
    if not existing:
        temp_password = secrets.token_urlsafe(12)
        user_doc = {
            "email": invited_email,
            "name": invited_email.split('@', 1)[0],
            "password_hash": hash_password(temp_password),
            "email_verified": True,
            "roles": ['user'],
            "preferences": {},
            "failed_login_attempts": 0,
            "lockout_until": None,
            "lat": None,
            "lon": None,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        try:
            await db_mod.db.users.insert_one(user_doc)
            provisional_created = True
        except DuplicateKeyError:
            provisional_created = False
        except PyMongoError:
            provisional_created = False

    # create registration if missing
    event_id = inv.get('event_id')
    existing_reg = None
    try:
        existing_reg = await db_mod.db.registrations.find_one({'event_id': event_id, 'user_email_snapshot': user_email})
    except PyMongoError:
        existing_reg = None
    if existing_reg:
        reg_res = existing_reg
    else:
        # Get the user_id of the invited user
        user_doc = await db_mod.db.users.find_one({"email": user_email})
        user_id = user_doc.get('_id') if user_doc else None

        reg = {
            "event_id": event_id,
            "user_email_snapshot": user_email,
            "status": "invited",
            "invitation_id": inv.get('_id'),
            "user_id": user_id,
            "team_size": 1,
            "preferences": {},
            "created_at": now,
            "updated_at": now
        }
        reg_res = await db_mod.db.registrations.insert_one(reg)

    # Business rule: bump the inviter's team size to at least 2 (if inviter registration exists).
    try:
        inviter_reg_oid = inv.get('registration_id')
        if inviter_reg_oid:
            # inviter_reg_oid should be an ObjectId stored on the invitation; fetch the inviter registration
            inviter_reg = await db_mod.db.registrations.find_one({"_id": inviter_reg_oid})
            if inviter_reg:
                current_team = int(inviter_reg.get('team_size') or 1)
                if current_team < 2:
                    await db_mod.db.registrations.update_one({"_id": inviter_reg_oid}, {"$set": {"team_size": 2, "updated_at": datetime.datetime.now(datetime.timezone.utc)}})
                    # best-effort: increment attendee_count accordingly
                    try:
                        await db_mod.db.events.update_one({"_id": event_id}, {"$inc": {"attendee_count": 2 - current_team}})
                    except PyMongoError:
                        pass
    except PyMongoError:
        # non-blocking
        pass
    
    # retro-link invitation
    if not inv.get('registration_id'):
        try:
            await db_mod.db.invitations.update_one({"_id": inv['_id']}, {"$set": {"registration_id": reg_res.inserted_id}})
        except PyMongoError:
            pass

    # ensure a pending payment exists for the invitee's registration if event has a fee
    try:
        ev = await db_mod.db.events.find_one({"_id": event_id}) if event_id else None
        fee_cents = (ev or {}).get('fee_cents', 0)
        reg_oid = reg_res.inserted_id if hasattr(reg_res, 'inserted_id') else reg_res.get('_id')
        if fee_cents and fee_cents > 0 and reg_oid:
            existing_payment = await db_mod.db.payments.find_one({"registration_id": reg_oid})
            if not existing_payment:
                pay = {
                    "registration_id": reg_oid,
                    "amount": float(fee_cents) / 100.0,
                    "currency": 'EUR',
                    "status": "pending",
                    "provider": 'None',
                    "meta": {"reason": "invite_accepted"},
                    "created_at": datetime.datetime.now(datetime.timezone.utc)
                }
                p = await db_mod.db.payments.insert_one(pay)
                try:
                    await db_mod.db.registrations.update_one({"_id": reg_oid}, {"$set": {"payment_id": p.inserted_id}})
                except PyMongoError:
                    pass
    except PyMongoError:
        pass

    # mark invitation accepted
    await db_mod.db.invitations.update_one({"_id": inv['_id']}, {"$set": {"status": "accepted", "accepted_by": user_email, "accepted_at": now}})

    # notify invited user by email about acceptance and credentials if provisional
    if provisional_created and temp_password:
        subject = "Your DinnerHopping account was created and invitation accepted"
        body = (
            f"Hi,\n\nYour account was created and the invitation was accepted for you. You can log in with:\n\n"
            f"Email: {user_email}\nTemporary password: {temp_password}\n\nPlease change your password after signing in.\n\nThanks,\nDinnerHopping Team"
        )
        await send_email(to=user_email, subject=subject, body=body, category='invitation_accept')

    # redirect to frontend success page when requested by a browser
    accept_success_url = f"{(os.getenv('FRONTEND_BASE_URL') or os.getenv('BACKEND_BASE_URL') or 'http://localhost:8000').rstrip('/')}/invitations/accepted"
    # If the client expects JSON (Accept header includes application/json) return JSON
    accept_header = request.headers.get('accept', '')
    if 'application/json' in accept_header or request.headers.get('x-requested-with') == 'XMLHttpRequest':
        reg_id = str(reg_res.inserted_id) if hasattr(reg_res, 'inserted_id') else str(reg_res.get('_id'))
        return {"status": "accepted", "user_email": user_email, "registration_id": reg_id}

    return RedirectResponse(accept_success_url, status_code=303)
