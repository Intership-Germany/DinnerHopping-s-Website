"""Authentication utilities for FastAPI application."""
import datetime
import os
import re

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from . import db as db_mod

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

JWT_SECRET = os.getenv('JWT_SECRET', 'change-me')
JWT_ALGO = 'HS256'

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def validate_password(password: str):
    """Basic password policy: min 8 chars, contains letter and number."""
    if not password or len(password) < 8:
        raise HTTPException(status_code=400, detail='Password must be at least 8 characters long')
    if not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9]", password):
        raise HTTPException(status_code=400, detail='Password must contain letters and numbers')

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict, expires_minutes: int = 60*24):
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=expires_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGO)

async def get_user_by_email(email: str):
    if not email:
        return None
    return await db_mod.db.users.find_one({"email": email.lower()})

async def authenticate_user(email: str, password: str):
    user = await get_user_by_email(email)
    if not user:
        return None
    # deny soft-deleted accounts
    if user.get('deleted_at') is not None:
        return None
    # check lockout
    lock_until = user.get('lockout_until')
    if lock_until:
        try:
            if datetime.datetime.utcnow() < lock_until:
                return None
        except Exception:
            # if lock_until isn't a datetime (older records), ignore
            pass

    # Support legacy 'password' field and new 'password_hash'
    stored_hash = user.get('password_hash') or user.get('password')
    if not verify_password(password, stored_hash):
        # increment failed attempts
        await db_mod.db.users.update_one({"email": email}, {"$inc": {"failed_login_attempts": 1}})
        user = await get_user_by_email(email)
        if user.get('failed_login_attempts', 0) >= 5:
            # lock the account for 15 minutes
            await db_mod.db.users.update_one({"email": email}, {"$set": {"lockout_until": datetime.datetime.utcnow() + datetime.timedelta(minutes=15)}})
        return None

    # successful login: reset failed attempts and remove lockout
    await db_mod.db.users.update_one({"email": email}, {"$set": {"failed_login_attempts": 0}, "$unset": {"lockout_until": "", "password": ""}})
    return user

async def get_current_user(request: Request):
    """Dependency that returns the current authenticated user.

    Token retrieval order:
    1. Authorization: Bearer <token> header
    2. HttpOnly cookie named 'access_token'

    This fallback allows browser clients to authenticate using secure cookies.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = None
    # 1) Authorization header
    auth_header = request.headers.get('authorization')
    if auth_header and auth_header.lower().startswith('bearer '):
        token = auth_header.split(' ', 1)[1].strip()

    # 2) cookie fallback
    if not token:
        cookie_token = request.cookies.get('access_token')
        if cookie_token:
            token = cookie_token

    if not token:
        raise credentials_exception

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = await get_user_by_email(email)
    if user is None:
        raise credentials_exception
    return user
