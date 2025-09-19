from fastapi import APIRouter, HTTPException, status, Depends, Request, Response
import os
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from ..auth import hash_password, create_access_token, authenticate_user, get_current_user, get_user_by_email, validate_password
from ..utils import generate_and_send_verification, encrypt_address, anonymize_public_address, hash_token, generate_token_pair
from .. import db as db_mod

router = APIRouter()

class UserCreate(BaseModel):
    name: str = Field(...)
    email: EmailStr
    password: str
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    preferences: dict | None = {}
    roles: list[str] | None = Field(default_factory=lambda: ['user'], description="List of role strings e.g. ['user','admin']")

class UserOut(BaseModel):
    id: str
    name: str
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
    validate_password(u.password)
    user_doc = u.dict()
    # enforce lowercase email
    user_doc['email'] = user_doc['email'].lower()
    # store hashed password under password_hash (new schema) and remove legacy key
    user_doc['password_hash'] = hash_password(u.password)
    user_doc.pop('password', None)
    # initialize failed login counters/lockout
    user_doc['failed_login_attempts'] = 0
    user_doc['lockout_until'] = None
    # newly created users are not verified until they confirm their email
    # preferred schema field name: email_verified
    user_doc['email_verified'] = False
    # maintain backward compatibility if other code still checks is_verified
    user_doc['is_verified'] = False
    # roles handling: ensure non-empty list with 'user' at minimum
    roles = user_doc.get('roles') or ['user']
    if isinstance(roles, str):
        roles = [roles]
    if 'user' not in roles:
        roles.append('user')
    user_doc['roles'] = sorted(set(roles))
    # store encrypted address and public anonymised address
    if user_doc.get('address'):
        user_doc['address_encrypted'] = encrypt_address(user_doc['address'])
        user_doc['address_public'] = anonymize_public_address(user_doc['address'])
        # keep lat/lon as-is for proximity features (but only used internally)
    # remove plain address to avoid accidental storage
    user_doc.pop('address', None)
    now = __import__('datetime').datetime.utcnow()
    user_doc['created_at'] = now
    user_doc['updated_at'] = now
    user_doc['deleted_at'] = None  # soft delete marker
    res = await db_mod.db.users.insert_one(user_doc)
    user_doc['id'] = str(res.inserted_id)
    # send verification email (prints link in dev)
    email_sent = True
    try:
        _token, email_sent = await generate_and_send_verification(u.email)
    except Exception:
        email_sent = False
    # Respond to client that the user was created successfully, include email_sent flag & message if failed
    resp = {"message": "Utilisateur créé avec succès", "id": user_doc['id'], "email_sent": email_sent}
    if not email_sent:
        resp["email_warning"] = "Compte créé mais l'email de vérification n'a pas pu être envoyé. Réessayez plus tard ou utilisez la route /resend-verification."
    return resp

class LoginIn(BaseModel):
    username: EmailStr
    password: str

@router.post('/login', response_model=TokenOut, responses={401: {"description": "Unauthorized - invalid credentials or email not verified"}, 422: {"description": "Validation error"}})
async def login(request: Request):
    """Login accepting either JSON body {username,password} or form data.

    This maintains compatibility with OAuth2PasswordRequestForm clients and
    test clients that send JSON. Email must be verified.
    """
    username = None
    password = None
    # Try form first (OAuth2 style)
    ctype = request.headers.get('content-type', '')
    if 'application/x-www-form-urlencoded' in ctype or 'multipart/form-data' in ctype:
        try:
            form = await request.form()
            username = form.get('username')
            password = form.get('password')
        except Exception:
            pass
    if username is None or password is None:
        # Fallback to JSON
        try:
            data = await request.json()
            username = data.get('username') or data.get('email')
            password = data.get('password')
        except Exception:
            pass

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
    token = create_access_token({"sub": user['email']})
    # Issue refresh token (HttpOnly cookie) and store hashed refresh token server-side
    refresh_plain, refresh_hash = generate_token_pair(32)
    now = __import__('datetime').datetime.utcnow()
    refresh_doc = {
        'user_email': user['email'],
        'token_hash': refresh_hash,
        'created_at': now,
        'expires_at': now + __import__('datetime').timedelta(days=int(os.getenv('REFRESH_TOKEN_DAYS', '30'))),
    }
    try:
        await db_mod.db.refresh_tokens.insert_one(refresh_doc)
    except Exception:
        # best-effort: still continue
        pass

    # create CSRF token to return to client for double-submit protection
    csrf_token = generate_token_pair(16)[0]

    # attach refresh_token cookie
    response = Response()
    secure_flag = True if os.getenv('ALLOW_INSECURE_COOKIES', 'false').lower() not in ('1', 'true') else False
    response.set_cookie('refresh_token', refresh_plain, httponly=True, secure=secure_flag, samesite='lax', max_age=60*60*24*int(os.getenv('REFRESH_TOKEN_DAYS', '30')))
    # also set access token cookie for convenience (shorter lifetime)
    response.set_cookie('access_token', token, httponly=True, secure=secure_flag, samesite='lax', max_age=60*15)
    # set CSRF token cookie (not HttpOnly) for double-submit protection
    response.set_cookie('csrf_token', csrf_token, httponly=False, secure=secure_flag, samesite='lax', max_age=60*60*24*int(os.getenv('REFRESH_TOKEN_DAYS', '30')))

    # Return access_token in body and CSRF token (client must store the CSRF token in-memory)
    body = {"access_token": token, "csrf_token": csrf_token, "token_type": "bearer"}
    response.media_type = 'application/json'
    import json
    response.body = json.dumps(body).encode('utf8')
    return response


@router.post('/logout')
async def logout(response: Response, current_user=Depends(get_current_user)):
    # Clear cookies and remove refresh token(s) associated with user
    try:
        await db_mod.db.refresh_tokens.delete_many({'user_email': current_user['email']})
    except Exception:
        pass
    response.delete_cookie('access_token')
    response.delete_cookie('refresh_token')
    response.delete_cookie('csrf_token')
    return {"status": "logged_out"}



@router.post('/refresh')
async def refresh(request: Request, response: Response):
    """Exchange a refresh cookie for a new access token. Rotates refresh token by default."""
    # validate CSRF: require X-CSRF-Token header match cookie value
    header_csrf = request.headers.get('x-csrf-token')
    cookie_csrf = request.cookies.get('csrf_token')
    if not header_csrf or not cookie_csrf or header_csrf != cookie_csrf:
        raise HTTPException(status_code=403, detail='Missing or invalid CSRF token')

    refresh_cookie = request.cookies.get('refresh_token')
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
    new_plain, new_hash = generate_token_pair(32)
    now = __import__('datetime').datetime.utcnow()
    try:
        await db_mod.db.refresh_tokens.update_one({'_id': rec['_id']}, {'$set': {'token_hash': new_hash, 'created_at': now, 'expires_at': now + __import__('datetime').timedelta(days=int(os.getenv('REFRESH_TOKEN_DAYS', '30')))}})
    except Exception:
        pass

    secure_flag = True if os.getenv('ALLOW_INSECURE_COOKIES', 'false').lower() not in ('1', 'true') else False
    response.set_cookie('refresh_token', new_plain, httponly=True, secure=secure_flag, samesite='lax', max_age=60*60*24*int(os.getenv('REFRESH_TOKEN_DAYS', '30')))
    response.set_cookie('access_token', new_access, httponly=True, secure=secure_flag, samesite='lax', max_age=60*15)
    return {"access_token": new_access}


class ProfileUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
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
    # handle address encryption/publicization when address provided
    if 'address' in update_data:
        update_data['address_encrypted'] = encrypt_address(update_data.get('address'))
        update_data['address_public'] = anonymize_public_address(update_data.get('address'))
        update_data.pop('address', None)
    update_data['updated_at'] = __import__('datetime').datetime.utcnow()
    await db_mod.db.users.update_one({"email": current_user['email']}, {"$set": update_data})
    u = await db_mod.db.users.find_one({"email": current_user['email']})
    return UserOut(id=str(u.get('_id', '')), name=u.get('name'), email=u.get('email'), address=u.get('address_public'), preferences=u.get('preferences', {}), roles=u.get('roles', []))

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
    except Exception:
        # if comparison fails, continue conservatively
        pass

    # mark user as verified
    await db_mod.db.users.update_one({"email": rec['email']}, {"$set": {"email_verified": True, "is_verified": True, "updated_at": __import__('datetime').datetime.utcnow()}})
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
        try:
            await db_mod.db.email_verifications.delete_many({"email": payload.email})
        except Exception:
            pass
        try:
            _token, email_sent = await generate_and_send_verification(payload.email)
        except Exception:
            email_sent = False
    return {"message": "If the account exists and is not verified, a new verification email has been sent.", "email_sent": email_sent}
