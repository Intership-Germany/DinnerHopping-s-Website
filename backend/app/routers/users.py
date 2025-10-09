"""Users router

Migration note (2025-09):
 - Removed duplicated full name field 'name'. Use 'first_name' and 'last_name'.
 - Removed legacy boolean 'is_verified'; standardized on 'email_verified'.
 - Address now always returned as structured components under 'address'.
Existing records may still contain 'name' or 'is_verified'; they are ignored.
"""
import json
import logging
import os
import re
from contextlib import suppress
from typing import List, Literal, Optional
from urllib.parse import unquote

from fastapi import (APIRouter, Depends, Form, HTTPException, Request,
                     Response, status)
from pydantic import BaseModel, EmailStr, field_validator
import phonenumbers

from .. import db as db_mod
from ..auth import (authenticate_user, create_access_token, get_current_user,
                    get_user_by_email, hash_password, validate_password)
from ..enums import Gender
from ..utils import (anonymize_public_address, encrypt_address,
                     generate_and_send_verification, generate_token_pair,
                     hash_token, send_email)

######### Constants #########

# Predefined list of valid allergies
VALID_ALLERGIES = [
    "nuts",
    "shellfish",
    "dairy",
    "eggs",
    "gluten",
    "soy",
    "fish",
    "sesame"
]


def _normalize_phone_number(phone: str | None) -> str | None:
    if phone is None:
        return None
    try:
        # Parse the phone number and normalize it to E.164 format
        parsed_phone = phonenumbers.parse(phone, None)  # Automatically detects the region
        if not phonenumbers.is_valid_number(parsed_phone):
            raise HTTPException(status_code=400, detail="Invalid phone number.")
        return phonenumbers.format_number(parsed_phone, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException as e:
        raise HTTPException(status_code=400, detail=f"Invalid phone number: {str(e)}")

# Utility function to validate phone numbers


def validate_phone_number(phone: str) -> str:
    """
    Validates and normalizes a phone number using the phonenumbers library.

    Args:
        phone (str): The phone number to validate.

    Returns:
        str: The normalized phone number in E.164 format.

    Raises:
        HTTPException: If the phone number is invalid.
    """
    try:
        # Automatically detects the region
        parsed_phone = phonenumbers.parse(phone, None)
        if not phonenumbers.is_valid_number(parsed_phone):
            raise HTTPException(
                status_code=400, detail="Invalid phone number.")
        return phonenumbers.format_number(parsed_phone, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid phone number: {str(e)}")

######### Router / Endpoints #########


logger = logging.getLogger("auth")


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
    gender: Gender
    phone_number: str | None = None
    # Optional extras
    lat: float | None = None
    lon: float | None = None
    allergies: list[str] | None = []

    @field_validator('phone_number')
    @classmethod
    def validate_phone(cls, v):
        try:
            return _normalize_phone_number(v)
        except ValueError as exc:
            raise ValueError(str(exc))

    @field_validator('allergies')
    @classmethod
    def validate_allergies(cls, v):
        if v is None or v == []:
            return []
        # Allow valid allergies and any custom ones that don't match predefined list
        validated_allergies = []
        for allergy in v:
            allergy_lower = allergy.lower().strip()
            if allergy_lower in VALID_ALLERGIES:
                validated_allergies.append(allergy_lower)
            elif allergy_lower not in VALID_ALLERGIES and allergy.strip():
                # Allow custom allergies that aren't in the predefined list
                validated_allergies.append(allergy.strip())
        return validated_allergies

    @field_validator('gender', mode='before')
    @classmethod
    def normalize_gender(cls, value):
        return Gender.normalize(value)


class UserOut(BaseModel):
    """Public/own profile user representation without duplicated full name field.

    Legacy field 'name' (full name) and 'is_verified' have been removed to avoid
    duplication. Use first_name + last_name for display and 'email_verified' for
    verification status.
    """
    id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: EmailStr
    # Structured address components stored under `address_struct`.
    address: dict | None = None
    phone_number: Optional[str] = None
    allergies: list[str] | None = []
    roles: list[str] | None = []
    # Optional profile fields
    kitchen_available: Optional[bool] = None
    main_course_possible: Optional[bool] = None
    default_dietary_preference: Optional[Literal['vegan',
                                                 'vegetarian', 'omnivore']] = None
    field_of_study: Optional[str] = None
    optional_profile_completed: bool = False
    profile_prompt_pending: bool = False


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post(
    '/register',
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Bad Request - e.g. password validation failed"},
        409: {"description": "Conflict - email already registered"},
    },
)
async def register(u: UserCreate):
    # normalize email to lowercase (keep EmailStr type intact; use a local string for storage)
    email_lower = str(u.email).lower()
    existing = await db_mod.db.users.find_one({"email": email_lower})
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    # validate password under policy
    if u.password != u.password_confirm:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    validate_password(u.password)
    # Validate phone number
    validate_phone_number(u.phone_number)
    # Build user document explicitly (do not trust arbitrary fields)
    user_doc = {
        'email': email_lower,
        'first_name': u.first_name.strip(),
        'last_name': u.last_name.strip(),
        'gender': Gender.normalize(u.gender).value,
        'phone_number': u.phone_number,
        'address_struct': {
            'street': u.street,
            'street_no': u.street_no,
            'postal_code': u.postal_code,
            'city': u.city,
        },
        'lat': u.lat,
        'lon': u.lon,
        'allergies': u.allergies or [],
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
    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
    user_doc['created_at'] = now
    user_doc['updated_at'] = now
    user_doc['deleted_at'] = None  # soft delete marker
    res = await db_mod.db.users.insert_one(user_doc)
    user_doc['id'] = str(res.inserted_id)
    # send verification email (prints link in dev)
    email_sent = False
    with suppress(Exception):
        _token, email_sent = await generate_and_send_verification(email_lower)
    # Respond to client that the user was created successfully, include email_sent flag & message if failed
    resp = {"message": "User created successfully", "id": user_doc['id'], "email_sent": email_sent}
    if not email_sent:
        resp["email_warning"] = (
            "Account created but the verification email could not be sent. "
            "Please try again later or use the /resend-verification route."
        )
    return resp


class LoginIn(BaseModel):
    username: EmailStr
    password: str


def _extract_credentials_from_source(source, current_username: str | None, current_password: str | None):
    if not isinstance(source, dict):
        return current_username, current_password

    candidate_username = source.get('username') or source.get('email')
    if isinstance(candidate_username, str) and candidate_username.strip() and not current_username:
        current_username = candidate_username.strip()

    candidate_password = source.get('password')
    if isinstance(candidate_password, str) and candidate_password and not current_password:
        current_password = candidate_password

    # Inspect nested payloads commonly used by clients
    nested_keys = ('payload', 'data', 'credentials')
    for key in nested_keys:
        nested = source.get(key)
        if isinstance(nested, dict):
            current_username, current_password = _extract_credentials_from_source(nested, current_username, current_password)
        elif isinstance(nested, str):
            try:
                nested_dict = json.loads(nested)
            except (TypeError, ValueError):
                continue
            current_username, current_password = _extract_credentials_from_source(nested_dict, current_username, current_password)

    return current_username, current_password


async def _resolve_login_credentials(
    request: Request,
    username_field: str | None,
    password_field: str | None,
    payload_form: str | None,
    payload_inline: str | None,
) -> tuple[str, str]:
    sources: list[dict] = []

    for raw in (payload_form, payload_inline):
        if raw is None:
            continue
        try:
            parsed = json.loads(raw) if raw else {}
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail='payload must be valid JSON') from exc
        else:
            if isinstance(parsed, dict):
                sources.append(parsed)

    with suppress(Exception):
        data = await request.json()
        if isinstance(data, dict):
            sources.append(data)

    username_value = username_field.strip() if isinstance(username_field, str) and username_field.strip() else None
    password_value = password_field.strip() if isinstance(password_field, str) and password_field.strip() else None

    for source in sources:
        username_value, password_value = _extract_credentials_from_source(source, username_value, password_value)
        if username_value and password_value:
            break

    if not (username_value and password_value):
        with suppress(Exception):
            form_data = await request.form()
            if form_data:
                form_dict = {key: form_data.get(key) for key in form_data.keys()}
                sources.append(form_dict)
                username_value, password_value = _extract_credentials_from_source(form_dict, username_value, password_value)

    if not (username_value and password_value):
        raise HTTPException(
            status_code=422, detail='username and password required')

    return username_value.lower(), password_value


@router.post('/login', response_model=TokenOut, responses={401: {"description": "Unauthorized - invalid credentials or email not verified"}, 422: {"description": "Validation error"}})
async def login(
    request: Request,
    username: EmailStr | None = Form(None),
    password: str | None = Form(None),
    payload_form: str | None = Form(None),
    payload: str | None = Form(None),
):
    """Login accepting either JSON body {username,password} or form data.

    This maintains compatibility with OAuth2PasswordRequestForm clients and
    test clients that send JSON. Email must be verified.
    """
    # Accept credentials from multiple client types: form-data (OAuth2 clients),
    # optional `payload_form` (a JSON string or empty string), or a raw JSON body.
    username, password = await _resolve_login_credentials(request, username, password, payload_form, payload)
    # require verified email
    user_obj = await get_user_by_email(username)
    if not user_obj:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user_obj.get('email_verified'):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Email not verified")
    user = await authenticate_user(username, password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    try:
        token = create_access_token({"sub": user['email']})
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail='Failed to create access token') from exc
    # first login timestamp and profile prompt
    now = __import__('datetime').datetime.now(
        __import__('datetime').timezone.utc)
    if not user.get('first_login_at'):
        with suppress(Exception):
            await db_mod.db.users.update_one({'email': user['email']}, {'$set': {'first_login_at': now}})
            user['first_login_at'] = now
    # Issue refresh token (HttpOnly cookie) and store hashed refresh token server-side
    # use configured refresh token size
    try:
        refresh_bytes = int(os.getenv('REFRESH_TOKEN_BYTES',
                            os.getenv('REFRESH_TOKEN_SIZE', '32')))
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

    secure_flag = True if os.getenv(
        'ALLOW_INSECURE_COOKIES', 'false').lower() not in ('1', 'true') else False
    use_host_prefix = secure_flag and (
        os.getenv('USE_HOST_PREFIX_COOKIES', 'true').lower() in ('1', 'true', 'yes'))
    max_refresh = 60 * 60 * 24 * int(os.getenv('REFRESH_TOKEN_DAYS', '30'))

    needs_profile_completion = bool((user.get('profile_prompt_pending', True)) and (
        not user.get('optional_profile_completed')))
    resp = JSONResponse(content={"access_token": token, "csrf_token": csrf_token,
                        "token_type": "bearer", "needs_profile_completion": needs_profile_completion})
    # Cookie names and attributes
    if use_host_prefix:
        # __Host- cookies require Secure and Path=/ and no Domain
        resp.set_cookie('__Host-refresh_token', refresh_plain, httponly=True,
                        secure=True, samesite='lax', max_age=max_refresh, path='/')
        resp.set_cookie('__Host-access_token', token, httponly=True,
                        secure=True, samesite='lax', max_age=60*60*24, path='/')
        resp.set_cookie('__Host-csrf_token', csrf_token, httponly=False,
                        secure=True, samesite='lax', max_age=max_refresh, path='/')
    else:
        # Dev fallback over http
        resp.set_cookie('refresh_token', refresh_plain, httponly=True,
                        secure=False, samesite='lax', max_age=max_refresh, path='/')
        resp.set_cookie('access_token', token, httponly=True,
                        secure=False, samesite='lax', max_age=60*60*24, path='/')
        resp.set_cookie('csrf_token', csrf_token, httponly=False,
                        secure=False, samesite='lax', max_age=max_refresh, path='/')

    return resp


@router.post('/logout')
async def logout(response: Response, current_user=Depends(get_current_user)):
    # Clear cookies and remove refresh token(s) associated with user
    with suppress(Exception):
        await db_mod.db.refresh_tokens.delete_many({'user_email': current_user['email']})
    # delete both dev and __Host- variants
    for name in ('access_token', 'refresh_token', 'csrf_token', '__Host-access_token', '__Host-refresh_token', '__Host-csrf_token'):
        with suppress(Exception):
            response.delete_cookie(name, path='/')
    return {"status": "logged_out"}


@router.post('/refresh')
async def refresh(request: Request, response: Response):
    """Exchange a refresh cookie for a new access token. Rotates refresh token by default."""
    # validate CSRF: require X-CSRF-Token header match cookie value
    header_csrf = request.headers.get('x-csrf-token')
    cookie_csrf = request.cookies.get(
        '__Host-csrf_token') or request.cookies.get('csrf_token')
    if not header_csrf or not cookie_csrf or header_csrf != cookie_csrf:
        raise HTTPException(
            status_code=403, detail='Missing or invalid CSRF token')

    refresh_cookie = request.cookies.get(
        '__Host-refresh_token') or request.cookies.get('refresh_token')
    if not refresh_cookie:
        raise HTTPException(status_code=401, detail='Missing refresh token')
    # lookup hashed refresh token
    th = hash_token(refresh_cookie)
    rec = await db_mod.db.refresh_tokens.find_one({'token_hash': th})
    if not rec:
        raise HTTPException(status_code=401, detail='Invalid refresh token')
    # check expiry
    if rec.get('expires_at') and rec.get('expires_at') < __import__('datetime').datetime.now(__import__('datetime').timezone.utc):
        # revoke
        await db_mod.db.refresh_tokens.delete_one({'_id': rec['_id']})
        raise HTTPException(status_code=401, detail='Refresh token expired')

    # issue new access token and rotate refresh token
    new_access = create_access_token({"sub": rec['user_email']})
    # rotate refresh
    try:
        refresh_bytes = int(os.getenv('REFRESH_TOKEN_BYTES',
                            os.getenv('REFRESH_TOKEN_SIZE', '32')))
    except (TypeError, ValueError):
        refresh_bytes = 32
    new_plain, new_hash = generate_token_pair(refresh_bytes)
    now = __import__('datetime').datetime.now(
        __import__('datetime').timezone.utc)
    # Implement single-use rotation: insert new refresh record and delete the old one
    new_doc = {
        'user_email': rec['user_email'],
        'token_hash': new_hash,
        'created_at': now,
        'expires_at': now + __import__('datetime').timedelta(days=int(os.getenv('REFRESH_TOKEN_DAYS', '30'))),
    }
    try:
        await db_mod.db.refresh_tokens.insert_one(new_doc)
        # best-effort delete of previous token record to avoid reuse
        await db_mod.db.refresh_tokens.delete_one({'_id': rec['_id']})
    except Exception:
        # Fallback: attempt to update the old record if insert/delete fails
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
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    gender: Gender | None = None
    email: EmailStr | None = None
    # Structured address components only
    street: Optional[str] = None
    street_no: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    lat: float | None = None
    lon: float | None = None
    allergies: list[str] | None = None
    phone_number: Optional[str] = None

    @field_validator('phone_number')
    @classmethod
    def validate_phone(cls, v):
        try:
            return _normalize_phone_number(v)
        except ValueError as exc:
            raise ValueError(str(exc))

    @field_validator('allergies')
    @classmethod
    def validate_allergies(cls, v):
        if v is None:
            return v
        # Allow valid allergies and any custom ones that don't match predefined list
        # This allows the "others" functionality while ensuring known allergies use standard names
        validated_allergies = []
        for allergy in v:
            allergy_lower = allergy.lower().strip()
            if allergy_lower in VALID_ALLERGIES:
                validated_allergies.append(allergy_lower)
            elif allergy_lower not in VALID_ALLERGIES and allergy.strip():
                # Allow custom allergies that aren't in the predefined list
                validated_allergies.append(allergy.strip())
        return validated_allergies

    @field_validator('gender', mode='before')
    @classmethod
    def normalize_gender(cls, value):
        if value is None:
            return None
        return Gender.normalize(value)
    # Optional profile fields (user-editable)
    kitchen_available: Optional[bool] = None
    main_course_possible: Optional[bool] = None
    default_dietary_preference: Optional[Literal['vegan','vegetarian','omnivore']] = None
    field_of_study: Optional[str] = None
    # allow skipping optional profile prompt via this endpoint
    skip_optional_profile: Optional[bool] = False


@router.get('/allergies', response_model=dict)
async def get_allergies():
    """Get the list of valid allergies for the allergy dropdown."""
    return {
        "allergies": VALID_ALLERGIES,
        "supports_other": True
    }


@router.get('/profile', response_model=UserOut)
async def get_profile(current_user=Depends(get_current_user)):
    u = await db_mod.db.users.find_one({"email": current_user['email']})
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    return UserOut(
        id=str(u.get('_id', '')),
        first_name=u.get('first_name'),
        last_name=u.get('last_name'),
        email=u.get('email'),
        # Return the structured address components stored in `address_struct`.
        address=u.get('address_struct'),
        phone_number=u.get('phone_number'),
        allergies=u.get('allergies', []),
        roles=u.get('roles', []),
        kitchen_available=u.get('kitchen_available'),
        main_course_possible=u.get('main_course_possible'),
        default_dietary_preference=u.get('default_dietary_preference'),
        field_of_study=u.get('field_of_study'),
        optional_profile_completed=bool(u.get('optional_profile_completed')),
        profile_prompt_pending=bool(u.get('profile_prompt_pending')),
    )


@router.put('/profile', response_model=UserOut)
async def update_profile(payload: ProfileUpdate, current_user=Depends(get_current_user)):
    raw_payload = payload.dict()
    update_data = {k: v for k, v in raw_payload.items() if v is not None}
    fields_set = getattr(payload, '__fields_set__', None) or getattr(payload, 'model_fields_set', None) or set()
    if 'phone_number' in fields_set and raw_payload.get('phone_number') is None:
        update_data['phone_number'] = None

    # Security: Reject password updates via profile endpoint
    if 'password' in update_data:
        raise HTTPException(
            status_code=400,
            detail='Password updates must use the dedicated /users/password endpoint for security'
        )

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
    # no longer maintaining combined 'name' field; clients compose front-end
    # normalize gender
    if 'gender' in update_data:
        update_data['gender'] = Gender.normalize(update_data.get('gender')).value
    # handle structured address updates
    if any(k in update_data for k in ('street', 'street_no', 'postal_code', 'city')):
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
    # handle optional profile fields provided via the main profile endpoint
    if any(k in update_data for k in ('kitchen_available', 'main_course_possible', 'default_dietary_preference', 'field_of_study', 'skip_optional_profile')):
        # map skip flag
        if update_data.get('skip_optional_profile'):
            update_data['profile_prompt_pending'] = False
            # don't mark optional_profile_completed when user skips
            update_data.pop('skip_optional_profile', None)
        else:
            # if user provided optional profile fields, consider prompt completed
            # ensure logical consistency: if kitchen_available is False, main_course_possible must be False
            if 'kitchen_available' in update_data and update_data.get('kitchen_available') is False:
                update_data['main_course_possible'] = False
            # mark optional profile completed when any optional field is supplied
            update_data['optional_profile_completed'] = True
            update_data['profile_prompt_pending'] = False
            update_data.pop('skip_optional_profile', None)
    update_data['updated_at'] = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
    await db_mod.db.users.update_one({"email": current_user['email']}, {"$set": update_data})
    u = await db_mod.db.users.find_one({"email": current_user['email']})
    # If email changed, lookup by new email
    if update_data.get('email'):
        u = await db_mod.db.users.find_one({"email": update_data.get('email')})
    return UserOut(
        id=str(u.get('_id', '')),
        first_name=u.get('first_name'),
        last_name=u.get('last_name'),
        email=u.get('email'),
        address=u.get('address_struct'),
        phone_number=u.get('phone_number'),
        allergies=u.get('allergies', []),
        roles=u.get('roles', []),
        kitchen_available=u.get('kitchen_available'),
        main_course_possible=u.get('main_course_possible'),
        default_dietary_preference=u.get('default_dietary_preference'),
        field_of_study=u.get('field_of_study'),
        optional_profile_completed=bool(u.get('optional_profile_completed')),
        profile_prompt_pending=bool(u.get('profile_prompt_pending')),
    )


@router.get('/verify-email')
async def verify_email(token: str | None = None):
    if not token:
        raise HTTPException(status_code=400, detail='Missing token')

    def _collect_candidates(raw: str) -> list[str]:
        if raw is None:
            return []
        variants: list[str] = []
        seen: set[str] = set()

        def add_variant(value: str | None) -> None:
            if not value:
                return
            normalized = value.strip()
            if not normalized:
                return
            if normalized not in seen:
                seen.add(normalized)
                variants.append(normalized)

        add_variant(raw)
        # account for clients translating plus to space
        add_variant(raw.replace(' ', '+'))

        current = raw
        for _ in range(2):  # single + double decoding as fallback
            decoded = unquote(current)
            if decoded == current:
                break
            add_variant(decoded)
            current = decoded

        return variants

    candidates = _collect_candidates(token)
    rec = None
    for idx, candidate in enumerate(candidates):
        th = hash_token(candidate)
        rec = await db_mod.db.email_verifications.find_one({"token_hash": th})
        if rec:
            if idx > 0:
                logger.info(
                    "verify_email matched token after normalization",
                    extra={
                        "token_length": len(candidate),
                        "candidate_index": idx,
                    },
                )
            break

    if not rec:
        logger.warning(
            "verify_email token not found",
            extra={
                "original_length": len(token),
                "candidates_checked": len(candidates),
            },
        )
        raise HTTPException(status_code=404, detail='Verification token not found')
    # check expiry
    expires_at = rec.get('expires_at')
    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
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
    await db_mod.db.users.update_one({"email": rec['email']}, {"$set": {"email_verified": True, "updated_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc)}})
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
    if user and not user.get('email_verified'):
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
    await db_mod.db.users.update_one({'email': current_user['email']}, {'$set': {'password_hash': hash_password(payload.new_password), 'updated_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc)}})
    return {"status": "password_changed"}


class ForgotPasswordIn(BaseModel):
    email: EmailStr


@router.post('/forgot-password')
async def forgot_password(payload: ForgotPasswordIn):
    """Initiate password reset: create a single-use reset token and email the reset link.

    Response is intentionally generic to avoid revealing account existence.
    """
    email = str(payload.email).lower()
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
        now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
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
            email_sent = await send_email(
                to=email,
                subject=subject,
                body=body,
                category='password_reset',
                template_vars={'reset_url': reset_link, 'email': email}
            )

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
        except (RuntimeError, TypeError):
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
    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
    try:
        if expires_at and isinstance(expires_at, __import__('datetime').datetime) and expires_at < now:
            await db_mod.db.password_resets.update_one({'_id': rec['_id']}, {'$set': {'status': 'expired', 'expired_at': now}})
            raise HTTPException(status_code=400, detail='Reset token expired')
    except TypeError:
        pass
    # validate password policy
    validate_password(new_password)
    # update user's password
    await db_mod.db.users.update_one({'email': rec['email']}, {'$set': {'password_hash': hash_password(new_password), 'updated_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc)}})
    # delete/reset token
    with suppress(Exception):
        await db_mod.db.password_resets.delete_one({'_id': rec['_id']})
    return {"status": "password_reset"}


@router.get('/reset-password')
async def reset_password_form(token: str | None = None):
    """Validate a password-reset token and return a JSON hint for clients.

    This endpoint does not return HTML. It only checks the token exists and is not expired,
    and returns a minimal JSON response instructing the client to POST the new password.
    """
    if not token:
        raise HTTPException(status_code=400, detail='Missing token')
    th = hash_token(token)
    rec = await db_mod.db.password_resets.find_one({'token_hash': th})
    if not rec:
        raise HTTPException(status_code=404, detail='Reset token not found')
    # check expiry
    expires_at = rec.get('expires_at')
    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
    try:
        if expires_at and isinstance(expires_at, __import__('datetime').datetime) and expires_at < now:
            await db_mod.db.password_resets.update_one({'_id': rec['_id']}, {'$set': {'status': 'expired', 'expired_at': now}})
            raise HTTPException(status_code=400, detail='Reset token expired')
    except TypeError:
        pass
    return {"status": "valid", "message": "Token valid. Submit new password to POST /reset-password with fields 'token' and 'new_password'."}


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
    set_fields: dict = {"updated_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc)}
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


@router.get('/csrf')
async def get_csrf(request: Request, response: Response):
    """Expose CSRF token for browser clients.

    Reads the CSRF cookie (__Host-csrf_token or csrf_token) and returns it in JSON
    and as an X-CSRF-Token header so fetch clients can store it for subsequent
    mutating requests.
    """
    token = request.cookies.get('__Host-csrf_token') or request.cookies.get('csrf_token') or ''
    if token:
        response.headers['X-CSRF-Token'] = token
    return {'csrf_token': token}
