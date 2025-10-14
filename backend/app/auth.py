"""Authentication utilities for FastAPI application.

Provides authentication and authorization functions including:
- Password hashing and validation with policy enforcement
- JWT token creation and validation  
- User authentication via credentials or tokens
- Admin role checking
- Session management

Adds structured logging around authentication attempts for observability.
"""

######### Imports #########

import datetime
import os
import re
import logging

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from passlib.exc import UnknownHashError

from . import db as db_mod
from .datetime_utils import to_iso, parse_iso

######### Password Hashing Configuration #########

pwd_context = CryptContext(
    schemes=["argon2", "bcrypt_sha256"],
    default="argon2",
    deprecated="auto",
)


legacy_pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
)

######### Logging and Security Configuration #########

auth_logger = logging.getLogger('auth')
# Make the OAuth2 scheme optional in dependency so the OpenAPI docs
# include the Bearer auth scheme (shows Authorize button) but runtime
# code can still fall back to cookie-based auth.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login", auto_error=False)

from .settings import get_settings

settings = get_settings()
JWT_SECRET = settings.jwt_secret
JWT_ALGO = 'HS256'
JWT_ISSUER = os.getenv('JWT_ISSUER')

######### Helper Functions #########

def _access_token_ttl_minutes() -> int:
    try:
        # prefer ACCESS_TOKEN_EXPIRES_MINUTES to be explicit in .env
        return int(os.getenv('ACCESS_TOKEN_EXPIRES_MINUTES', os.getenv('ACCESS_TOKEN_MINUTES', '60')))
    except (TypeError, ValueError):
        return 60

######### Password Management #########

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def validate_password(password: str):
    """Password policy configurable via environment variables.

    Environment variables consulted (all optional):
    - PASSWORD_MIN_LENGTH (int)
    - PASSWORD_REQUIRE_NUMERIC (true/false)
    - PASSWORD_REQUIRE_UPPER (true/false)
    - PASSWORD_REQUIRE_LOWER (true/false)
    - PASSWORD_REQUIRE_SPECIAL (true/false)
    """
    try:
        minlen = int(os.getenv('PASSWORD_MIN_LENGTH', '8'))
    except (TypeError, ValueError):
        minlen = 8

    def _bool_env(name: str, default: bool) -> bool:
        return os.getenv(name, str(default)).lower() in ('1', 'true', 'yes')

    require_numeric = _bool_env('PASSWORD_REQUIRE_NUMERIC', True)
    require_upper = _bool_env('PASSWORD_REQUIRE_UPPER', False)
    require_lower = _bool_env('PASSWORD_REQUIRE_LOWER', False)
    require_special = _bool_env('PASSWORD_REQUIRE_SPECIAL', False)

    if not password or len(password) < minlen:
        raise HTTPException(status_code=400, detail=f'Password must be at least {minlen} characters long')
    if require_numeric and not re.search(r"[0-9]", password):
        raise HTTPException(status_code=400, detail='Password must contain a number')
    if require_upper and not re.search(r"[A-Z]", password):
        raise HTTPException(status_code=400, detail='Password must contain an uppercase letter')
    if require_lower and not re.search(r"[a-z]", password):
        raise HTTPException(status_code=400, detail='Password must contain a lowercase letter')
    if require_special and not re.search(r"[!@#\$%\^&\*()_+\-=\[\]{};':\",.<>\/?\\|`~]", password):
        raise HTTPException(status_code=400, detail='Password must contain a special character')

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except UnknownHashError:
        return _verify_legacy_password(plain, hashed)
    except ValueError:
        # bcrypt backend may reject >72 byte secrets; fall back to legacy handling
        return _verify_legacy_password(plain, hashed)


def _verify_legacy_password(plain: str, hashed: str) -> bool:
    try:
        return legacy_pwd_context.verify(plain, hashed)
    except ValueError:
        # mimic historical bcrypt behaviour which truncated to 72 bytes silently
        truncated = plain.encode("utf-8")[:72]
        return legacy_pwd_context.verify(truncated, hashed)

def create_access_token(data: dict, expires_minutes: int | None = None):
    to_encode = data.copy()
    now_dt = datetime.datetime.now(datetime.timezone.utc)  # still use datetime for JWT exp/iat numeric processing
    exp_minutes = expires_minutes if isinstance(expires_minutes, int) and expires_minutes > 0 else _access_token_ttl_minutes()
    expire = now_dt + datetime.timedelta(minutes=exp_minutes)
    # Standard JWT claims
    to_encode.update({"exp": expire, "iat": now_dt})
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
        auth_logger.info('auth.login.failed reason=not_found email=%s', email)
        return None
    # deny soft-deleted accounts
    if user.get('deleted_at') is not None:
        auth_logger.info('auth.login.failed reason=deleted email=%s', email)
        return None
    # check lockout
    lock_until = user.get('lockout_until')
    if lock_until:
        try:
            # lock_until may now be stored as ISO string -> parse for comparison
            if isinstance(lock_until, str):
                lock_until_dt = parse_iso(lock_until)
            else:
                lock_until_dt = lock_until
            now_cmp = datetime.datetime.now(datetime.timezone.utc)
            # ensure both sides are timezone-aware datetimes for comparison
            if lock_until_dt and isinstance(lock_until_dt, datetime.datetime):
                if lock_until_dt.tzinfo is None:
                    lock_until_dt = lock_until_dt.replace(tzinfo=datetime.timezone.utc)
            if lock_until_dt and now_cmp < lock_until_dt:
                auth_logger.warning('auth.login.locked email=%s until=%s', email, lock_until)
                return None
        except (TypeError, ValueError):
            # if lock_until isn't valid, ignore
            pass

    # Support legacy 'password' field and new 'password_hash'
    stored_hash = user.get('password_hash') or user.get('password')
    # Guard against missing/invalid stored hash
    try:
        ok = bool(stored_hash) and verify_password(password, stored_hash)
    except (UnknownHashError, ValueError) as exc:
        # Legacy or unsupported hash detected. We do NOT accept plaintext fallback.
        # Force a password reset path instead of silently accepting insecure storage.
        auth_logger.warning('auth.login.legacy_hash_or_error email=%s', email)
        raise HTTPException(status_code=400, detail='Account requires password reset. Please use forgot password to set a new password') from exc
    if not ok:
        # increment failed attempts atomically
        try:
            res = await db_mod.db.users.find_one_and_update({"email": email}, {"$inc": {"failed_login_attempts": 1}}, return_document=True)
        except Exception:
            res = await get_user_by_email(email)

        attempts = (res.get('failed_login_attempts') or 0) if res else 0
        # lock the account for 15 minutes if too many attempts
        if attempts >= 5:
            lock_until_iso = to_iso(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15))
            try:
                await db_mod.db.users.update_one({"email": email}, {"$set": {"lockout_until": lock_until_iso}})
            except Exception:
                pass
            auth_logger.warning('auth.login.lockout email=%s attempts=%s', email, attempts)
            # Emit high-level security log for monitoring systems
            logging.getLogger('auth.security').warning('multiple_failed_logins email=%s attempts=%s', email, attempts)
        else:
            auth_logger.info('auth.login.failed reason=bad_credentials email=%s attempts=%s', email, attempts)
        return None

    # successful login: reset failed attempts and remove lockout
    # If legacy plaintext password exists, migrate it to a hash now
    updates = {"failed_login_attempts": 0}
    unset = {"lockout_until": ""}

    if user.get('password'):
        updates['password_hash'] = hash_password(password)
        unset['password'] = ""
    else:
        await db_mod.db.users.update_one({"email": email}, {"$set": updates, "$unset": unset})
        if stored_hash:
            try:
                if pwd_context.needs_update(stored_hash):
                    updates['password_hash'] = hash_password(password)
            except ValueError:
                # If the stored hash is invalid or uses an unsupported backend, refresh it.
                updates['password_hash'] = hash_password(password)
    await db_mod.db.users.update_one({"email": email}, {"$set": updates, "$unset": unset})
    auth_logger.info('auth.login.success email=%s', email)
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

    # Prefer token from the OAuth2 dependency (Authorization header). If not
    # present, fall back to the HttpOnly access_token cookie for browser
    # session flows.
    if not token:
        # prefer secure __Host- cookie if present
        cookie_token = request.cookies.get('__Host-access_token') or request.cookies.get('access_token')
        if cookie_token:
            token = cookie_token
        else:
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
