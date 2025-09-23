from fastapi import APIRouter, HTTPException, status, Depends, Request, Response, Form
import os
from pydantic import BaseModel, EmailStr
from typing import Optional, Literal
from contextlib import suppress
from app.auth import hash_password, create_access_token, authenticate_user, get_current_user, get_user_by_email, validate_password
from app.utils import generate_and_send_verification, encrypt_address, anonymize_public_address, hash_token, generate_token_pair, send_email
from app import db as db_mod

######### Router / Endpoints #########

router = APIRouter()

class UserCreate(BaseModel):
    # Required at registration
    email: EmailStr
    password: str
    password_confirm: str
    first_name: str
    last_name: str
    # Full address components
    street: str
    street_no: str
    postal_code: str
    city: str
    gender: Literal['female','male','diverse','prefer_not_to_say']
    # Optional extras
    lat: float | None = None
    lon: float | None = None
    preferences: dict | None = {}

class UserOut(BaseModel):
    id: str
    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: EmailStr
    address: str | None = None
    preferences: dict | None = {}
    roles: list[str] | None = []

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

@router.post('/register', status_code=status.HTTP_201_CREATED, responses={400: {"description": "Bad Request - e.g. Email already registered or password validation failed"}})
async def register(u: UserCreate):
    # normalize email to lowercase
    u.email = u.email.lower()
    existing = await db_mod.db.users.find_one({"email": u.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    # validate password under policy
    if u.password != u.password_confirm:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    validate_password(u.password)
    # Build user document explicitly (do not trust arbitrary fields)
    user_doc = {
        'email': u.email.lower(),
        'first_name': u.first_name.strip(),
        'last_name': u.last_name.strip(),
        'name': f"{u.first_name.strip()} {u.last_name.strip()}",
        'gender': (u.gender or 'prefer_not_to_say').lower(),
        'address_struct': {
            'street': u.street,
            'street_no': u.street_no,
            'postal_code': u.postal_code,
            'city': u.city,
        },
        'lat': u.lat,
        'lon': u.lon,
        'preferences': u.preferences or {},
    }
    # store hashed password under password_hash (new schema)
    user_doc['password_hash'] = hash_password(u.password)
    # initialize failed login counters/lockout
    user_doc['failed_login_attempts'] = 0
    user_doc['lockout_until'] = None
    # newly created users are not verified until they confirm their email
    # preferred schema field name: email_verified
    user_doc['email_verified'] = False
    # first-login prompt flags for optional profile
    user_doc['profile_prompt_pending'] = True
    user_doc['optional_profile_completed'] = False
    # roles handling: DO NOT trust client input for roles.
    # Always assign the minimal 'user' role on self-service registration.
    # Any privileged role (e.g. 'admin') must be provisioned separately by
    # an existing administrator via a protected admin interface or migration.
    user_doc['roles'] = ['user']
    # store encrypted address and public anonymised address
    # Create a combined line for encryption/anonymization
    addr_line = f"{u.street} {u.street_no}, {u.postal_code} {u.city}"
    user_doc['address_encrypted'] = encrypt_address(addr_line)
    user_doc['address_public'] = anonymize_public_address(addr_line)
    now = __import__('datetime').datetime.utcnow()
    user_doc['created_at'] = now
    user_doc['updated_at'] = now
    user_doc['deleted_at'] = None  # soft delete marker
    res = await db_mod.db.users.insert_one(user_doc)
    user_doc['id'] = str(res.inserted_id)
    # send verification email (prints link in dev)
    email_sent = False
    with suppress(Exception):
        _token, email_sent = await generate_and_send_verification(u.email)
    # Respond to client that the user was created successfully, include email_sent flag & message if failed
    resp = {"message": "Utilisateur créé avec succès", "id": user_doc['id'], "email_sent": email_sent}
    if not email_sent:
        resp["email_warning"] = "Compte créé mais l'email de vérification n'a pas pu être envoyé. Réessayez plus tard ou utilisez la route /resend-verification."
    return resp

class LoginIn(BaseModel):
    username: EmailStr
    password: str

@router.post('/login', response_model=TokenOut, responses={401: {"description": "Unauthorized - invalid credentials or email not verified"}, 422: {"description": "Validation error"}})
async def login(request: Request, username: EmailStr | None = Form(None), password: str | None = Form(None), payload_form: str | None = Form(None)):
    """Login accepting either JSON body {username,password} or form data.

    This maintains compatibility with OAuth2PasswordRequestForm clients and
    test clients that send JSON. Email must be verified.
    """
    # Accept credentials from multiple client types: form-data (OAuth2 clients),
    # optional `payload_form` (a JSON string or empty string), or a raw JSON body.
    import json
    payload = None
    # 1) payload_form (form field) — some clients send payload="" or payload="{...}"
    if payload_form is not None:
        try:
            payload = json.loads(payload_form) if payload_form else {}
        except (ValueError, TypeError):
            payload = {}

    # 2) if no payload yet, attempt to read JSON body (best-effort; will raise if body is form-encoded)
    if payload is None:
        with suppress(Exception):
            data = await request.json()
            if isinstance(data, dict):
                payload = data

    # If we were given a payload (dict), extract username/password from it
    if isinstance(payload, dict):
        username = username or payload.get('username') or payload.get('email')
        password = password or payload.get('password')

    # 3) finally fallback to explicit form fields
    if not username or not password:
        with suppress(Exception):
            form = await request.form()
            username = username or form.get('username')
            password = password or form.get('password')

    if not username or not password:
        raise HTTPException(status_code=422, detail='username and password required')
    username = username.lower()
    # require verified email
    user_obj = await get_user_by_email(username)
    if not user_obj:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not (user_obj.get('email_verified') or user_obj.get('is_verified')):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email not verified")
    user = await authenticate_user(username, password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    try:
        token = create_access_token({"sub": user['email']})
    except Exception as exc:
        raise HTTPException(status_code=500, detail='Failed to create access token') from exc
    # first login timestamp and profile prompt
    now = __import__('datetime').datetime.utcnow()
    if not user.get('first_login_at'):
        with suppress(Exception):
            await db_mod.db.users.update_one({'email': user['email']}, {'$set': {'first_login_at': now}})
            user['first_login_at'] = now
    # Issue refresh token (HttpOnly cookie) and store hashed refresh token server-side
    # use configured refresh token size
    try:
        refresh_bytes = int(os.getenv('REFRESH_TOKEN_BYTES', os.getenv('REFRESH_TOKEN_SIZE', '32')))
    except (TypeError, ValueError):
        refresh_bytes = 32
    refresh_plain, refresh_hash = generate_token_pair(refresh_bytes)
    refresh_doc = {
        'user_email': user['email'],
        'token_hash': refresh_hash,
        'created_at': now,
        'expires_at': now + __import__('datetime').timedelta(days=int(os.getenv('REFRESH_TOKEN_DAYS', '30'))),
    }
    with suppress(Exception):
        await db_mod.db.refresh_tokens.insert_one(refresh_doc)

    # create CSRF token to return to client for double-submit protection
    try:
        csrf_bytes = int(os.getenv('CSRF_TOKEN_BYTES', '16'))
    except (TypeError, ValueError):
        csrf_bytes = 16
    csrf_token = generate_token_pair(csrf_bytes)[0]

    # Build JSON response body and attach cookies using response.set_cookie
    from fastapi.responses import JSONResponse

    secure_flag = True if os.getenv('ALLOW_INSECURE_COOKIES', 'false').lower() not in ('1', 'true') else False
    use_host_prefix = secure_flag and (os.getenv('USE_HOST_PREFIX_COOKIES', 'true').lower() in ('1','true','yes'))
    max_refresh = 60 * 60 * 24 * int(os.getenv('REFRESH_TOKEN_DAYS', '30'))

    needs_profile_completion = bool((user.get('profile_prompt_pending', True)) and (not user.get('optional_profile_completed')))
    resp = JSONResponse(content={"access_token": token, "csrf_token": csrf_token, "token_type": "bearer", "needs_profile_completion": needs_profile_completion})
    # Cookie names and attributes
    if use_host_prefix:
        # __Host- cookies require Secure and Path=/ and no Domain
        resp.set_cookie('__Host-refresh_token', refresh_plain, httponly=True, secure=True, samesite='lax', max_age=max_refresh, path='/')
        resp.set_cookie('__Host-access_token', token, httponly=True, secure=True, samesite='lax', max_age=60*60*24, path='/')
        resp.set_cookie('__Host-csrf_token', csrf_token, httponly=False, secure=True, samesite='lax', max_age=max_refresh, path='/')
    else:
        # Dev fallback over http
        resp.set_cookie('refresh_token', refresh_plain, httponly=True, secure=False, samesite='lax', max_age=max_refresh, path='/')
        resp.set_cookie('access_token', token, httponly=True, secure=False, samesite='lax', max_age=60*60*24, path='/')
        resp.set_cookie('csrf_token', csrf_token, httponly=False, secure=False, samesite='lax', max_age=max_refresh, path='/')

    return resp


@router.post('/logout')
async def logout(response: Response, current_user=Depends(get_current_user)):
    # Clear cookies and remove refresh token(s) associated with user
    with suppress(Exception):
        await db_mod.db.refresh_tokens.delete_many({'user_email': current_user['email']})
    # delete both dev and __Host- variants
    for name in ('access_token','refresh_token','csrf_token','__Host-access_token','__Host-refresh_token','__Host-csrf_token'):
        with suppress(Exception):
            response.delete_cookie(name, path='/')
    return {"status": "logged_out"}



@router.post('/refresh')
async def refresh(request: Request, response: Response):
    """Exchange a refresh cookie for a new access token. Rotates refresh token by default."""
    # validate CSRF: require X-CSRF-Token header match cookie value
    header_csrf = request.headers.get('x-csrf-token')
    cookie_csrf = request.cookies.get('__Host-csrf_token') or request.cookies.get('csrf_token')
    if not header_csrf or not cookie_csrf or header_csrf != cookie_csrf:
        raise HTTPException(status_code=403, detail='Missing or invalid CSRF token')

    refresh_cookie = request.cookies.get('__Host-refresh_token') or request.cookies.get('refresh_token')
    if not refresh_cookie:
        raise HTTPException(status_code=401, detail='Missing refresh token')
    # lookup hashed refresh token
    th = hash_token(refresh_cookie)
    rec = await db_mod.db.refresh_tokens.find_one({'token_hash': th})
    if not rec:
        raise HTTPException(status_code=401, detail='Invalid refresh token')
    # check expiry
    if rec.get('expires_at') and rec.get('expires_at') < __import__('datetime').datetime.utcnow():
        # revoke
        await db_mod.db.refresh_tokens.delete_one({'_id': rec['_id']})
        raise HTTPException(status_code=401, detail='Refresh token expired')

    # issue new access token and rotate refresh token
    new_access = create_access_token({"sub": rec['user_email']})
    # rotate refresh
    try:
        refresh_bytes = int(os.getenv('REFRESH_TOKEN_BYTES', os.getenv('REFRESH_TOKEN_SIZE', '32')))
    except (TypeError, ValueError):
        refresh_bytes = 32
    new_plain, new_hash = generate_token_pair(refresh_bytes)
    now = __import__('datetime').datetime.utcnow()
    with suppress(Exception):
        await db_mod.db.refresh_tokens.update_one({'_id': rec['_id']}, {'$set': {'token_hash': new_hash, 'created_at': now, 'expires_at': now + __import__('datetime').timedelta(days=int(os.getenv('REFRESH_TOKEN_DAYS', '30')))}})

    secure_flag = True if os.getenv('ALLOW_INSECURE_COOKIES', 'false').lower() not in ('1', 'true') else False
    use_host_prefix = secure_flag and (os.getenv('USE_HOST_PREFIX_COOKIES', 'true').lower() in ('1','true','yes'))
    if use_host_prefix:
        response.set_cookie('__Host-refresh_token', new_plain, httponly=True, secure=True, samesite='lax', max_age=60*60*24*int(os.getenv('REFRESH_TOKEN_DAYS', '30')), path='/')
        response.set_cookie('__Host-access_token', new_access, httponly=True, secure=True, samesite='lax', max_age=60*60*24, path='/')
    else:
        response.set_cookie('refresh_token', new_plain, httponly=True, secure=False, samesite='lax', max_age=60*60*24*int(os.getenv('REFRESH_TOKEN_DAYS', '30')), path='/')
        response.set_cookie('access_token', new_access, httponly=True, secure=False, samesite='lax', max_age=60*60*24, path='/')
    return {"access_token": new_access}


class ProfileUpdate(BaseModel):
    # legacy combined name (admin-only)
    name: str | None = None
    # explicit fields
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    gender: Optional[Literal['female','male','diverse','prefer_not_to_say']] = None
    email: EmailStr | None = None
    # either provide a single-line address or structured parts
    address: str | None = None
    street: Optional[str] = None
    street_no: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    lat: float | None = None
    lon: float | None = None
    preferences: dict | None = None

@router.get('/profile', response_model=UserOut)
async def get_profile_alias(current_user=Depends(get_current_user)):
    u = await db_mod.db.users.find_one({"email": current_user['email']})
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    return UserOut(id=str(u.get('_id', '')), name=u.get('name'), email=u.get('email'), address=u.get('address_public'), preferences=u.get('preferences', {}), roles=u.get('roles', []))

@router.put('/profile', response_model=UserOut)
async def update_profile_alias(payload: ProfileUpdate, current_user=Depends(get_current_user)):
    update_data = {k: v for k, v in payload.dict().items() if v is not None}
    # Only admins may change the 'name' field. Prevent non-admins from updating it.
    if 'name' in update_data:
        roles = current_user.get('roles') or []
        if 'admin' not in roles:
            raise HTTPException(status_code=403, detail='Only admins may change the name')
    if 'password' in update_data:
        # migrating update: accept 'password' input, store as password_hash
        update_data['password_hash'] = hash_password(update_data['password'])
        update_data.pop('password', None)
    # handle email change: require uniqueness and mark unverified
    if 'email' in update_data:
        new_email = update_data.get('email')
        if new_email:
            new_email = new_email.lower()
            existing = await db_mod.db.users.find_one({'email': new_email})
            if existing and existing.get('_id') != current_user.get('_id'):
                raise HTTPException(status_code=400, detail='Email already in use')
            # mark unverified and send verification email
            update_data['email'] = new_email
            update_data['email_verified'] = False
            with suppress(Exception):
                _token, _ = await generate_and_send_verification(new_email)
        else:
            # empty/invalid email not allowed
            raise HTTPException(status_code=400, detail='Invalid email')
    # handle first/last name synchronization with legacy 'name'
    if 'first_name' in update_data or 'last_name' in update_data:
        first_name = update_data.get('first_name', current_user.get('first_name'))
        last_name = update_data.get('last_name', current_user.get('last_name'))
        if first_name and last_name:
            update_data['name'] = f"{first_name.strip()} {last_name.strip()}"
    # normalize gender
    if 'gender' in update_data and isinstance(update_data.get('gender'), str):
        update_data['gender'] = update_data['gender'].lower()
    # handle structured address updates
    if any(k in update_data for k in ('street','street_no','postal_code','city')):
        existing = current_user.get('address_struct') or {}
        struct = {
            'street': update_data.get('street', existing.get('street')),
            'street_no': update_data.get('street_no', existing.get('street_no')),
            'postal_code': update_data.get('postal_code', existing.get('postal_code')),
            'city': update_data.get('city', existing.get('city')),
        }
        update_data['address_struct'] = struct
        left = " ".join([p for p in [struct.get('street'), struct.get('street_no')] if p])
        right = " ".join([p for p in [struct.get('postal_code'), struct.get('city')] if p])
        full_line = f"{left}, {right}" if left or right else None
        if full_line:
            update_data['address_encrypted'] = encrypt_address(full_line)
            update_data['address_public'] = anonymize_public_address(full_line)
    # handle single-line address
    if 'address' in update_data:
        update_data['address_encrypted'] = encrypt_address(update_data.get('address'))
        update_data['address_public'] = anonymize_public_address(update_data.get('address'))
        update_data.pop('address', None)
    update_data['updated_at'] = __import__('datetime').datetime.utcnow()
    await db_mod.db.users.update_one({"email": current_user['email']}, {"$set": update_data})
    u = await db_mod.db.users.find_one({"email": current_user['email']})
    # If email changed, lookup by new email
    if update_data.get('email'):
        u = await db_mod.db.users.find_one({"email": update_data.get('email')})
    return UserOut(
        id=str(u.get('_id', '')),
        name=u.get('name'),
        first_name=u.get('first_name'),
        last_name=u.get('last_name'),
        email=u.get('email'),
        address=u.get('address_public'),
        preferences=u.get('preferences', {}),
        roles=u.get('roles', []),
    )

@router.get('/verify-email')
async def verify_email(token: str | None = None):
    if not token:
        raise HTTPException(status_code=400, detail='Missing token')
    # match by token_hash, not plaintext token
    th = hash_token(token)
    rec = await db_mod.db.email_verifications.find_one({"token_hash": th})
    if not rec:
        raise HTTPException(status_code=404, detail='Verification token not found')
    # check expiry
    expires_at = rec.get('expires_at')
    now = __import__('datetime').datetime.utcnow()
    # rec.expires_at may be timezone-aware; compare safely
    try:
        if expires_at and isinstance(expires_at, __import__('datetime').datetime) and expires_at < now:
            # mark expired and inform caller
            await db_mod.db.email_verifications.update_one({"_id": rec['_id']}, {"$set": {"expired_at": now, "status": "expired"}})
            raise HTTPException(status_code=400, detail='Verification token expired')
    except TypeError:
        # if comparison fails, continue conservatively
        pass

    # mark user as verified
    await db_mod.db.users.update_one({"email": rec['email']}, {"$set": {"email_verified": True, "updated_at": __import__('datetime').datetime.utcnow()}})
    # delete the token for one-time use
    await db_mod.db.email_verifications.delete_one({"_id": rec['_id']})
    return {"status": "verified"}

class ResendVerificationIn(BaseModel):
    email: EmailStr


@router.post('/resend-verification')
async def resend_verification(payload: ResendVerificationIn):
    """Allow an unverified user to request a new verification email.
    Response is always generic to avoid disclosing account existence.
    """
    user = await db_mod.db.users.find_one({"email": payload.email})
    email_sent = False
    if user and not (user.get('email_verified') or user.get('is_verified')):
        with suppress(Exception):
            await db_mod.db.email_verifications.delete_many({"email": payload.email})
        with suppress(Exception):
            _token, email_sent = await generate_and_send_verification(payload.email)
    return {"message": "If the account exists and is not verified, a new verification email has been sent.", "email_sent": email_sent}


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


@router.put('/password')
async def change_password(payload: ChangePasswordIn, current_user=Depends(get_current_user)):
    """Authenticated password change. Requires old_password and validates new_password under policy."""
    # verify old password
    user = await get_user_by_email(current_user['email'])
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    auth = await authenticate_user(current_user['email'], payload.old_password)
    if not auth:
        raise HTTPException(status_code=403, detail='Old password is incorrect')
    # validate new password
    validate_password(payload.new_password)
    # set new password hash
    await db_mod.db.users.update_one({'email': current_user['email']}, {'$set': {'password_hash': hash_password(payload.new_password), 'updated_at': __import__('datetime').datetime.utcnow()}})
    return {"status": "password_changed"}


class ForgotPasswordIn(BaseModel):
    email: EmailStr


@router.post('/forgot-password')
async def forgot_password(payload: ForgotPasswordIn):
    """Initiate password reset: create a single-use reset token and email the reset link.

    Response is intentionally generic to avoid revealing account existence.
    """
    email = payload.email.lower()
    user = await db_mod.db.users.find_one({'email': email})
    email_sent = False
    # Always respond success; if user exists, create reset token and send email
    if user:
        # remove old tokens for this email
        with suppress(Exception):
            await db_mod.db.password_resets.delete_many({'email': email})
        # use configured token size for password reset tokens (falls back to utils default)
        try:
            reset_bytes = int(os.getenv('PASSWORD_RESET_TOKEN_BYTES', os.getenv('TOKEN_BYTES', '32')))
        except (TypeError, ValueError):
            reset_bytes = None
        token, token_hash = generate_token_pair(reset_bytes)
        now = __import__('datetime').datetime.utcnow()
        try:
            ttl_hours = int(os.getenv('PASSWORD_RESET_EXPIRES_HOURS', '4'))
        except (TypeError, ValueError):
            ttl_hours = 4
        expires_at = now + __import__('datetime').timedelta(hours=ttl_hours)
        doc = {'email': email, 'token_hash': token_hash, 'created_at': now, 'expires_at': expires_at}
        with suppress(Exception):
            await db_mod.db.password_resets.insert_one(doc)
        # send email with reset link
        with suppress(Exception):
            base = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
            reset_link = f"{base}/reset-password?token={token}"
            subject = 'Reset your DinnerHopping password'
            body = f"Hi,\n\nTo reset your password, click the link below:\n{reset_link}\n\nIf you didn't request this, ignore this message.\n\nThanks,\nDinnerHopping Team"
            email_sent = await send_email(to=email, subject=subject, body=body, category='password_reset')

    return {"message": "If an account exists for this email, a password reset link has been sent.", "email_sent": email_sent}


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str


@router.post('/reset-password')
async def reset_password(request: Request):
    """Complete password reset using token from email. Accepts either JSON {token,new_password}
    or form-encoded submissions from the HTML form.
    """
    token = None
    new_password = None
    # Try JSON body first
    try:
        data = await request.json()
        if isinstance(data, dict):
            token = data.get('token')
            new_password = data.get('new_password')
    except (ValueError, TypeError):
        # not JSON, try form-encoded
        try:
            form = await request.form()
            token = token or form.get('token')
            new_password = new_password or form.get('new_password')
        except Exception:
            # give up and validate below
            pass

    if not token or not new_password:
        raise HTTPException(status_code=400, detail='token and new_password required')
    th = hash_token(token)
    rec = await db_mod.db.password_resets.find_one({'token_hash': th})
    if not rec:
        raise HTTPException(status_code=404, detail='Reset token not found')
    # check expiry
    expires_at = rec.get('expires_at')
    now = __import__('datetime').datetime.utcnow()
    try:
        if expires_at and isinstance(expires_at, __import__('datetime').datetime) and expires_at < now:
            await db_mod.db.password_resets.update_one({'_id': rec['_id']}, {'$set': {'status': 'expired', 'expired_at': now}})
            raise HTTPException(status_code=400, detail='Reset token expired')
    except TypeError:
        pass
    # validate password policy
    validate_password(new_password)
    # update user's password
    await db_mod.db.users.update_one({'email': rec['email']}, {'$set': {'password_hash': hash_password(new_password), 'updated_at': __import__('datetime').datetime.utcnow()}})
    # delete/reset token
    with suppress(Exception):
        await db_mod.db.password_resets.delete_one({'_id': rec['_id']})
    return {"status": "password_reset"}


@router.get('/reset-password')
async def reset_password_form(token: str | None = None):
        """Return a minimal HTML form to allow browsers or curl to open the reset page.

        The form posts to POST /reset-password with fields `token` and `new_password`.
        This GET endpoint is CSRF-exempt by design (safe, idempotent GET) and simply helps
        with manual debugging and UI integration.
        """
        if not token:
                raise HTTPException(status_code=400, detail='Missing token')
        html = f"""
        <!doctype html>
        <html>
            <head><meta charset="utf-8"><title>Reset password</title></head>
            <body>
                <h1>Reset your password</h1>
                <form method="post" action="/reset-password">
                    <input type="hidden" name="token" value="{token}" />
                    <label>New password: <input type="password" name="new_password"/></label>
                    <button type="submit">Reset password</button>
                </form>
            </body>
        </html>
        """
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html, status_code=200)


# -------- Optional profile (first-login prompt) --------

class OptionalProfileOut(BaseModel):
    kitchen_available: Optional[bool] = None
    main_course_possible: Optional[bool] = None
    default_dietary_preference: Optional[Literal['vegan','vegetarian','omnivore']] = None
    field_of_study: Optional[str] = None
    optional_profile_completed: bool = False
    profile_prompt_pending: bool = False


class OptionalProfileUpdate(BaseModel):
    kitchen_available: Optional[bool] = None
    main_course_possible: Optional[bool] = None
    default_dietary_preference: Optional[Literal['vegan','vegetarian','omnivore']] = None
    field_of_study: Optional[str] = None
    skip: Optional[bool] = False


@router.get('/profile/optional', response_model=OptionalProfileOut)
async def get_optional_profile(current_user=Depends(get_current_user)):
    u = await db_mod.db.users.find_one({"email": current_user['email']})
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    return OptionalProfileOut(
        kitchen_available=u.get('kitchen_available'),
        main_course_possible=u.get('main_course_possible'),
        default_dietary_preference=u.get('default_dietary_preference'),
        field_of_study=u.get('field_of_study'),
        optional_profile_completed=bool(u.get('optional_profile_completed')),
        profile_prompt_pending=bool(u.get('profile_prompt_pending')),
    )


@router.patch('/profile/optional')
async def update_optional_profile(payload: OptionalProfileUpdate, current_user=Depends(get_current_user)):
    u = await db_mod.db.users.find_one({"email": current_user['email']})
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    set_fields: dict = {"updated_at": __import__('datetime').datetime.utcnow()}
    if payload.skip:
        set_fields['profile_prompt_pending'] = False
        await db_mod.db.users.update_one({"email": current_user['email']}, {"$set": set_fields})
        return {"status": "skipped"}

    provided = False
    if payload.kitchen_available is not None:
        set_fields['kitchen_available'] = payload.kitchen_available
        provided = True
        if payload.kitchen_available is False:
            set_fields['main_course_possible'] = False
    if payload.main_course_possible is not None:
        set_fields['main_course_possible'] = payload.main_course_possible
        provided = True
    if payload.default_dietary_preference is not None:
        set_fields['default_dietary_preference'] = payload.default_dietary_preference
        provided = True
    if payload.field_of_study is not None:
        set_fields['field_of_study'] = payload.field_of_study
        provided = True

    if provided:
        set_fields['optional_profile_completed'] = True
        set_fields['profile_prompt_pending'] = False

    await db_mod.db.users.update_one({"email": current_user['email']}, {"$set": set_fields})
    return {"status": "updated", "optional_profile_completed": bool(set_fields.get('optional_profile_completed', u.get('optional_profile_completed')))}
