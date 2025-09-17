from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, EmailStr, Field
from ..auth import hash_password, create_access_token, authenticate_user, get_current_user, get_user_by_email, validate_password
from ..utils import generate_and_send_verification
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

class UserOut(BaseModel):
    id: str
    name: str
    email: EmailStr
    address: str | None = None
    preferences: dict | None = {}

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

@router.post('/register', status_code=status.HTTP_201_CREATED)
async def register(u: UserCreate):
    existing = await db_mod.db.users.find_one({"email": u.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    # validate password under policy
    validate_password(u.password)
    user_doc = u.dict()
    user_doc['password'] = hash_password(u.password)
    # initialize failed login counters/lockout
    user_doc['failed_login_attempts'] = 0
    user_doc['lockout_until'] = None
    # newly created users are not verified until they confirm their email
    user_doc['is_verified'] = False
    res = await db_mod.db.users.insert_one(user_doc)
    user_doc['id'] = str(res.inserted_id)
    # send verification email (prints link in dev)
    try:
        await generate_and_send_verification(u.email)
    except Exception:
        pass
    # Respond to client that the user was created successfully
    return {"message": "Utilisateur créé avec succès", "id": user_doc['id']}

class LoginIn(BaseModel):
    username: EmailStr
    password: str

@router.post('/login', response_model=TokenOut)
async def login(credentials: LoginIn):
    # require verified email
    user_obj = await get_user_by_email(credentials.username)
    if not user_obj:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user_obj.get('is_verified', False):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email not verified")

    user = await authenticate_user(credentials.username, credentials.password)
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


@router.get('/me', response_model=UserOut)
async def get_my_profile(current_user=Depends(get_current_user)):
    u = await db_mod.db.users.find_one({"email": current_user['email']})
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    return UserOut(id=str(u.get('_id', '')), name=u.get('name'), email=u.get('email'), address=u.get('address'), preferences=u.get('preferences', {}))


@router.put('/me', response_model=UserOut)
async def update_my_profile(payload: ProfileUpdate, current_user=Depends(get_current_user)):
    update_data = {k: v for k, v in payload.dict().items() if v is not None}
    if 'password' in update_data:
        update_data['password'] = hash_password(update_data['password'])
    await db_mod.db.users.update_one({"email": current_user['email']}, {"$set": update_data})
    u = await db_mod.db.users.find_one({"email": current_user['email']})
    return UserOut(id=str(u.get('_id', '')), name=u.get('name'), email=u.get('email'), address=u.get('address'), preferences=u.get('preferences', {}))


@router.get('/profile', response_model=UserOut)
async def get_profile_alias(current_user=Depends(get_current_user)):
    return await get_my_profile(current_user)


@router.put('/profile', response_model=UserOut)
async def update_profile_alias(payload: ProfileUpdate, current_user=Depends(get_current_user)):
    return await update_my_profile(payload, current_user)


@router.get('/verify-email')
async def verify_email(token: str | None = None):
    if not token:
        raise HTTPException(status_code=400, detail='Missing token')
    rec = await db_mod.db.email_verifications.find_one({"token": token})
    if not rec:
        raise HTTPException(status_code=404, detail='Verification token not found')
    # mark user as verified
    await db_mod.db.users.update_one({"email": rec['email']}, {"$set": {"is_verified": True}})
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
    if user and not user.get('is_verified', False):
        try:
            await db_mod.db.email_verifications.delete_many({"email": payload.email})
        except Exception:
            pass
        try:
            await generate_and_send_verification(payload.email)
        except Exception:
            pass
    return {"message": "If the account exists and is not verified, a new verification email has been sent."}
