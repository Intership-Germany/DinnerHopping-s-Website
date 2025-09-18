from fastapi import APIRouter, HTTPException, status, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from ..auth import hash_password, create_access_token, authenticate_user, get_current_user, get_user_by_email, validate_password
from ..utils import generate_and_send_verification, encrypt_address, anonymize_public_address
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
    return TokenOut(access_token=token)


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
    rec = await db_mod.db.email_verifications.find_one({"token": token})
    if not rec:
        raise HTTPException(status_code=404, detail='Verification token not found')
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
