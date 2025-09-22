"""Authentication utilities for FastAPI application."""
import datetime
import os
import re

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from passlib.exc import UnknownHashError

from . import db as db_mod

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# Make the OAuth2 scheme optional in dependency so the OpenAPI docs
# include the Bearer auth scheme (shows Authorize button) but runtime
# code can still fall back to cookie-based auth.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login", auto_error=False)

JWT_SECRET = os.getenv('JWT_SECRET', 'change-me')
JWT_ALGO = 'HS256'
JWT_ISSUER = os.getenv('JWT_ISSUER')

def _access_token_ttl_minutes() -> int:
    try:
        return int(os.getenv('ACCESS_TOKEN_MINUTES', '15'))
    except Exception:
        return 15

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

def create_access_token(data: dict, expires_minutes: int | None = None):
    to_encode = data.copy()
    now = datetime.datetime.utcnow()
    exp_minutes = expires_minutes if isinstance(expires_minutes, int) and expires_minutes > 0 else _access_token_ttl_minutes()
    expire = now + datetime.timedelta(minutes=exp_minutes)
    # Standard JWT claims
    to_encode.update({"exp": expire, "iat": now})
    if JWT_ISSUER:
        to_encode["iss"] = JWT_ISSUER
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
        except (TypeError, ValueError):
            # if lock_until isn't a datetime (older records), ignore
            pass

    # Support legacy 'password' field and new 'password_hash'
    stored_hash = user.get('password_hash') or user.get('password')
    # Guard against missing/invalid stored hash
    try:
        ok = bool(stored_hash) and verify_password(password, stored_hash)
    except (UnknownHashError, ValueError):
        # If the stored value is actually plaintext from older records, compare directly
        ok = stored_hash == password
    if not ok:
        # increment failed attempts
        await db_mod.db.users.update_one({"email": email}, {"$inc": {"failed_login_attempts": 1}})
        user = await get_user_by_email(email)
        if user.get('failed_login_attempts', 0) >= 5:
            # lock the account for 15 minutes
            await db_mod.db.users.update_one({"email": email}, {"$set": {"lockout_until": datetime.datetime.utcnow() + datetime.timedelta(minutes=15)}})
        return None

    # successful login: reset failed attempts and remove lockout
    # If legacy plaintext password exists, migrate it to a hash now
    updates = {"failed_login_attempts": 0}
    unset = {"lockout_until": ""}
    if user.get('password'):
        updates['password_hash'] = hash_password(password)
        unset['password'] = ""
    await db_mod.db.users.update_one({"email": email}, {"$set": updates, "$unset": unset})
    return user

async def get_current_user(request: Request, token: str = Depends(oauth2_scheme)):
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

    # Prefer token from the OAuth2 dependency (this will pick up the
    # Authorization: Bearer <token> header when present). If the dependency
    # did not provide a token (auto_error=False), fall back to the cookie.
    if not token:
        # prefer secure __Host- cookie if present
        cookie_token = request.cookies.get('__Host-access_token') or request.cookies.get('access_token')
        if cookie_token:
            token = cookie_token

    if not token:
        # no token provided via header or cookie
        raise credentials_exception

    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGO],
            options={"require": ["exp", "sub"]},
        )
        if JWT_ISSUER and payload.get("iss") != JWT_ISSUER:
            raise credentials_exception
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc
    user = await get_user_by_email(email)
    if user is None:
        raise credentials_exception
    return user


def require_role(role: str):
    """Factory returning a FastAPI dependency that ensures the current user has `role`.

    Usage: Depends(require_role('admin')) or use the convenience `require_admin`.
    """
    def _dependency(current_user=Depends(get_current_user)):
        roles = current_user.get('roles') or []
        if role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Forbidden')
        return current_user

    return _dependency


def require_admin(current_user=Depends(get_current_user)):
    """Dependency that allows only admin users.

    Returns the current_user on success.
    """
    roles = current_user.get('roles') or []
    if 'admin' not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Admin required')
    return current_user
