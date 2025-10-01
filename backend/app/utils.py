"""
Utility functions for the DinnerHopping backend.

This module provides helper functions for:
- Anonymizing geographic coordinates and addresses to preserve user privacy.
- Sending emails with support for SMTP configuration, retries, and development fallbacks.
- Generating and sending email verification tokens.
- Sending generic notification emails.

Logging is configured for email-related operations. 
The module is intended for internal use within the backend application.
"""
import asyncio  # for to_thread and sleep
import datetime
import email.utils
import logging
import os
import secrets
import smtplib
import uuid
import math
from email.message import EmailMessage
from typing import Mapping, Optional, Sequence
import base64
import re
import hmac
import hashlib

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover - optional dependency in lightweight dev setups
    AESGCM = None

from . import db as db_mod
from pymongo.errors import PyMongoError
from bson.errors import InvalidId
from bson.objectid import ObjectId

# Simple approach: quantize lat/lon to ~500m grid using Haversine-based degrees approximation
# For typical latitudes, 0.0045 deg ~ 500m (varies). We'll use 0.0045 to approximate 500m.

GRID_SIZE_DEG = 0.0045

logger = logging.getLogger("email")
if not logger.handlers:
    # Basic handler if application didn't configure logging yet
    _h = logging.StreamHandler()
    _fmt = logging.Formatter('[%(asctime)s] %(levelname)s email: %(message)s')
    _h.setFormatter(_fmt)
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

def anonymize_coords(lat: float, lon: float) -> dict:
    """Anonymize coordinates by snapping to a grid cell of ~500m size."""
    lat_cell = round(lat / GRID_SIZE_DEG) * GRID_SIZE_DEG
    lon_cell = round(lon / GRID_SIZE_DEG) * GRID_SIZE_DEG
    return {"lat": lat_cell, "lon": lon_cell}

# Simple helper to strip address exactness
def anonymize_address(lat: float, lon: float) -> dict:
    """Anonymize an address by returning a grid cell center and approximate radius."""
    cell = anonymize_coords(lat, lon)
    return {
        "center": cell,
        "approx_radius_m": 500,
    }


# ---------- Distance / Travel Utilities (Matching support) ----------

EARTH_RADIUS_M = 6371000.0

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points in meters.

    Simple implementation adequate for intra-city routing estimation.
    """
    # convert degrees -> radians
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_M * c


def approx_travel_time_minutes(distance_m: float, mode: str = 'walk') -> float:
    """Approximate travel time based on naive average speeds.

    - walk: 4.5 km/h
    - bike: 15 km/h
    """
    if distance_m <= 0:
        return 0.0
    speed_map = {
        'walk': 4_500.0,  # meters per hour
        'bike': 15_000.0,
    }
    speed = speed_map.get(mode, speed_map['walk'])
    hours = distance_m / speed
    return hours * 60.0


def distance_matrix(points: list[tuple[float, float]]) -> list[list[float]]:
    """Return a symmetric distance matrix (meters) for given (lat,lon) points.

    Used by matching algorithm for scoring travel cost.
    """
    n = len(points)
    mtx = [[0.0]*n for _ in range(n)]
    for i in range(n):
        lat1, lon1 = points[i]
        for j in range(i+1, n):
            lat2, lon2 = points[j]
            d = haversine_m(lat1, lon1, lat2, lon2)
            mtx[i][j] = mtx[j][i] = d
    return mtx


def _load_address_key() -> bytes | None:
    """Load ADDRESS_KEY from env (base64) and return raw bytes, or None if unset.

    ADDRESS_KEY should be 32 bytes (base64-encoded) for AES-256-GCM.
    """
    b64 = os.getenv('ADDRESS_KEY')
    if not b64:
        return None
    try:
        key = base64.b64decode(b64)
        if len(key) not in (16, 24, 32):
            raise ValueError('invalid key length')
        return key
    except (TypeError, ValueError, base64.binascii.Error):
        # invalid configuration; treat as unset
        return None


def encrypt_address(plain: str) -> str:
    """Encrypt a plaintext address with AES-GCM and return base64(nonce + ciphertext).

    If ADDRESS_KEY is not set, return the plaintext (dev fallback).
    """
    key = _load_address_key()
    if not key or AESGCM is None:
        # dev fallback: store plaintext
        return plain
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plain.encode('utf8'), None)
    out = base64.b64encode(nonce + ct).decode('ascii')
    return out


def decrypt_address(b64: str) -> str:
    """Decrypt an address produced by encrypt_address. Returns plaintext or original input on failure.

    If ADDRESS_KEY not set, assumes input is plaintext and returns it.
    """
    key = _load_address_key()
    if not key or AESGCM is None:
        return b64
    try:
        raw = base64.b64decode(b64)
        nonce = raw[:12]
        ct = raw[12:]
        aesgcm = AESGCM(key)
        pt = aesgcm.decrypt(nonce, ct, None)
        return pt.decode('utf8')
    except (TypeError, ValueError, base64.binascii.Error):
        # on any error, return original value to avoid breaking callers
        return b64


def anonymize_public_address(address: str) -> str:
    """Create a privacy-preserving public address string from a detailed address.

    Strategy (best-effort):
    - If address contains commas, keep street (without house number) and the last segment (city/postcode).
    - Remove explicit house numbers.
    - Fall back to removing digit sequences and collapsing whitespace.
    """
    if not address:
        return ''
    parts = [p.strip() for p in address.split(',') if p.strip()]
    if len(parts) >= 2:
        street = parts[0]
        city = parts[-1]
        # remove house numbers from street (e.g. "Bahnhofstraße 5" -> "Bahnhofstraße")
        street_clean = re.sub(r"\b\d+[A-Za-z\-]?\b", "", street).strip()
        return f"{street_clean}, {city}"
    # fallback: remove standalone digit groups (postal codes will be removed)
    cleaned = re.sub(r"\b\d+\b", "", address).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned

async def send_email(
    *,
    to: Sequence[str] | str,
    subject: str,
    body: str,
    from_address: str | None = None,
    headers: Mapping[str, str] | None = None,
    category: str = "generic",
) -> bool:
    """Low-level reusable email sender with retry & console fallback.

    Sends an email to one or more recipients using SMTP, with support for retries and a development fallback that prints the email to stdout if SMTP is not configured.

    Args:
        to (Sequence[str] | str): Recipient email address or a sequence of addresses.
        subject (str): Subject line of the email.
        body (str): Plain text body of the email.
        from_address (str | None, optional): Sender's email address. Defaults to environment variable SMTP_FROM_ADDRESS or "info@acrevon.fr".
        headers (Mapping[str, str] | None, optional): Additional email headers to include. Defaults to None.
        category (str, optional): Custom category for the email, used for logging and headers. Defaults to "generic".

    Returns:
        bool: True if an SMTP delivery attempt reported success or if in development mode (no SMTP configured), False otherwise.

    Raises:
        ValueError: If no recipients are provided.

    Notes:
        - In development mode (no SMTP configured), the email is printed to stdout and considered sent.
        - Retries sending the email up to SMTP_MAX_RETRIES times with exponential backoff on failure.
        - Only plain text emails are supported; can be extended for HTML.

    Returns True if an SMTP delivery attempt reported success, False otherwise.
    In development (no SMTP configured) it prints the email to stdout and returns True
    so callers can treat it as a best-effort notification.
    """
    if isinstance(to, str):
        recipients = [to]
    else:
        recipients = list(to)
    if not recipients:
        raise ValueError("No recipients provided")

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port_env = os.getenv("SMTP_PORT")
    smtp_port: Optional[int] = int(smtp_port_env) if smtp_port_env else None
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    use_tls_env = os.getenv("SMTP_USE_TLS", "true").lower()
    use_tls = use_tls_env in ("1", "true", "yes")
    smtp_timeout = int(os.getenv("SMTP_TIMEOUT_SECONDS", "10"))
    max_retries = int(os.getenv("SMTP_MAX_RETRIES", "2"))
    from_addr = from_address or os.getenv("SMTP_FROM_ADDRESS", "info@acrevon.fr")

    # Build message (text only for now; can extend with HTML alternative)
    def _build_message() -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipients)
        msg.set_content(body)
        msg["Date"] = email.utils.formatdate(localtime=True)
        try:
            from_domain = from_addr.split('@', 1)[1]
        except (IndexError, AttributeError):
            from_domain = 'dinnerhopping.local'
        msg["Message-ID"] = f"<{uuid.uuid4().hex}@{from_domain}>"
        msg["X-Mailer"] = "DinnerHopping/1.0"
        msg["X-DH-Category"] = category
        if headers:
            for k, v in headers.items():
                if k.lower() in {"from", "to", "subject"}:
                    continue
                msg[k] = v
        return msg

    def _send_once():
        msg = _build_message()
        try:
            if smtp_port == 465:
                with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=smtp_timeout) as server:
                    server.ehlo()
                    if smtp_user and smtp_pass:
                        server.login(smtp_user, smtp_pass)
                    envelope_from = smtp_user or from_addr
                    result = server.send_message(msg, from_addr=envelope_from, to_addrs=recipients)
                    if result:
                        logger.warning("SMTP_SSL partial failures: %s", result)
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=smtp_timeout) as server:
                    server.ehlo()
                    if use_tls:
                        server.starttls()
                        server.ehlo()
                    if smtp_user and smtp_pass:
                        server.login(smtp_user, smtp_pass)
                    envelope_from = smtp_user or from_addr
                    result = server.send_message(msg, from_addr=envelope_from, to_addrs=recipients)
                    if result:
                        logger.warning("SMTP partial failures: %s", result)
            return True, None
        except Exception as exc:
            # Catch all network/SMTP exceptions (e.g., socket.gaierror) and surface as (False, exc)
            return False, exc

    if not (smtp_host and smtp_port):
        # dev fallback: print
        printable = f"[email dev-fallback] to={recipients} subject={subject}\n{body}\n-- end --"
        print(printable)
        logger.info("Email (category=%s) printed to console (no SMTP configured)", category)
        return True

    attempt = 0
    last_exc = None
    while attempt <= max_retries:
        attempt += 1
        try:
            ok, exc = await asyncio.to_thread(_send_once)
        except Exception as exc:  # defensive: to_thread or underlying raised unexpectedly
            ok, exc = False, exc
        if ok:
            logger.info("Email sent category=%s to=%s attempt=%d", category, recipients, attempt)
            return True
        last_exc = exc
        if attempt > max_retries:
            logger.error("Failed to send email category=%s to=%s after %d attempts: %r", category, recipients, attempt, last_exc)
            return False
        backoff = min(2 ** (attempt - 1), 8)
        logger.warning("Email send retry category=%s attempt=%d error=%r backoff=%ss", category, attempt, exc, backoff)
        await asyncio.sleep(backoff)
    return False


async def generate_and_send_verification(recipient: str) -> tuple[str, bool]:
    """Create a verification token & send verification email.

    Returns:
        tuple[str, bool]: (token, email_sent) where email_sent indicates whether
        the email was actually sent (or printed in dev fallback). False means all
        SMTP attempts failed when SMTP was configured.
    """
    token, token_hash = generate_token_pair()
    created_at = datetime.datetime.now(datetime.timezone.utc)
    # TTL for verification tokens (hours)
    try:
        ttl_hours = int(os.getenv('EMAIL_VERIFICATION_EXPIRES_HOURS', os.getenv('EMAIL_VERIFICATION_EXPIRES', '48')))
    except (TypeError, ValueError):
        ttl_hours = 48
    expires_at = created_at + datetime.timedelta(hours=ttl_hours)
    # store a non-reversible hash of the token instead of the plaintext token
    doc = {"email": recipient, "token_hash": token_hash, "created_at": created_at, "expires_at": expires_at}
    try:
        await db_mod.db.email_verifications.insert_one(doc)
        logger.info("Stored verification token for %s", recipient)
    except PyMongoError as e:
        # Best-effort: log a warning and continue on DB insertion failures
        logger.warning("Could not persist verification token for %s: %s", recipient, e)

    base = os.getenv("BACKEND_BASE_URL", "http://localhost:8000")
    verification_url = f"{base}/verify-email?token={token}"
    subject = "Please verify your DinnerHopping account"
    body = (
        f"Hi,\n\nPlease verify your email by clicking the link below:\n{verification_url}\n\n"
        "If you didn't request this, ignore this message.\n\nThanks,\nDinnerHopping Team"
    )
    ok = await send_email(
        to=recipient,
        subject=subject,
        body=body,
        category="verification"
    )
    if not ok:
        # fallback print of link if send failed *despite* having SMTP configured
        print(f"[email fallback] Verification link for {recipient}: {verification_url}")
    return token, ok


def hash_token(token: str) -> str:
    """Return HMAC-SHA256(token, TOKEN_PEPPER) hex digest for safe storage/lookup.

    If TOKEN_PEPPER is unset the function will still compute a hash with an empty pepper
    but it's strongly recommended to set a long random TOKEN_PEPPER in production.
    """
    pepper = os.getenv('TOKEN_PEPPER', '')
    pepper_bytes = pepper.encode('utf8') if isinstance(pepper, str) else pepper
    return hmac.new(pepper_bytes, token.encode('utf8'), hashlib.sha256).hexdigest()


def _default_token_bytes() -> int:
    try:
        return int(os.getenv('ACCESS_TOKEN_BYTES', os.getenv('TOKEN_BYTES', '32')))
    except (TypeError, ValueError):
        return 32


def generate_token_pair(bytes_entropy: int | None = None) -> tuple[str, str]:
    """Generate a secure random token and its stored hash.

    Returns (token, token_hash) where token is safe to send to the user and
    token_hash is the HMAC-SHA256 hex digest for storage.
    """
    if bytes_entropy is None:
        bytes_entropy = _default_token_bytes()
    t = secrets.token_urlsafe(bytes_entropy)
    return t, hash_token(t)


######### Event / Registration helpers #########
from fastapi import HTTPException


async def get_event(event_id) -> Optional[dict]:
    """Return an event document by id (accepts str or ObjectId) or None if not found/invalid.

    Usage: ev = await get_event(event_id)
    """
    if not event_id:
        return None
    try:
        oid = event_id if isinstance(event_id, ObjectId) else ObjectId(event_id)
    except (InvalidId, TypeError, ValueError):
        return None
    return await db_mod.db.events.find_one({'_id': oid})


async def get_registration_by_any_id(registration_id) -> Optional[dict]:
    """Return a registration document by id accepting either ObjectId-like strings or raw string IDs.

    This helper first tries to interpret the input as a BSON ObjectId and query by
    {'_id': ObjectId(...)}, and if that fails or returns nothing it falls back to
    querying the collection with the raw value ({'_id': registration_id}).
    """
    if not registration_id:
        return None
    # try ObjectId conversion first
    try:
        oid = registration_id if isinstance(registration_id, ObjectId) else ObjectId(registration_id)
    except (InvalidId, TypeError, ValueError):
        oid = None

    if oid is not None:
        try:
            reg = await db_mod.db.registrations.find_one({'_id': oid})
            if reg:
                return reg
        except PyMongoError:
            # ignore DB errors here and try raw lookup below
            pass

    # fallback: try raw string _id (some fixtures or older records may store string ids)
    try:
        reg = await db_mod.db.registrations.find_one({'_id': registration_id})
        if reg:
            return reg
    except PyMongoError:
        return None

    return None


async def require_event_published(event_id) -> dict:
    """Raise HTTPException if event not found or not in an accessible lifecycle state.

    Backward compatibility: legacy 'published' maps to 'open'. We also allow
    later lifecycle states (closed, matched, released) for read / matching /
    refunds admin actions so that tooling can operate after registrations end.
    In tests (USE_FAKE_DB_FOR_TESTS) we relax further to accept 'draft'.
    """
    ev = await get_event(event_id)
    if not ev:
        raise HTTPException(status_code=404, detail='Event not found')
    status = (ev.get('status') or '').lower()
    if status == 'published':  # legacy migration path
        status = 'open'
    allowed = {'open', 'closed', 'matched', 'released'}  # active/visible phases
    # Some admin flows may still reference 'published' directly in old data
    allowed.add('published')
    if os.getenv('USE_FAKE_DB_FOR_TESTS'):
        allowed.add('draft')
    if status not in allowed:
        raise HTTPException(status_code=400, detail='Event is not open for this action')
    return ev


async def user_registered_or_organizer(user: dict, event_id) -> bool:
    """Return True if user is registered for event or is the organizer."""
    ev = await get_event(event_id)
    if not ev:
        return False
    try:
        is_organizer = str(ev.get('organizer_id')) == str(user.get('_id'))
    except (TypeError, ValueError):
        is_organizer = False
    reg = await db_mod.db.registrations.find_one({'event_id': ev.get('_id'), 'user_email_snapshot': user.get('email')})
    return bool(reg) or bool(is_organizer)


async def require_user_registered_or_organizer(user: dict, event_id) -> dict:
    """Raise 403 if user is neither registered nor organizer. Returns event on success."""
    ok = await user_registered_or_organizer(user, event_id)
    if not ok:
        raise HTTPException(status_code=403, detail='Not registered for this event')
    ev = await get_event(event_id)
    if not ev:
        # defensive: if event vanishes between checks
        raise HTTPException(status_code=404, detail='Event not found')
    return ev

######### End helpers #########


######### Authorization helpers (owner/admin, deadlines) #########

# reuse HTTPException imported earlier in this module to avoid duplicate imports
_HTTPException = HTTPException


def _is_admin(user: dict) -> bool:
    roles = (user or {}).get('roles') or []
    return 'admin' in roles


async def get_registration_by_any_id(registration_id) -> dict | None:
    if registration_id is None:
        return None
    candidate_values: list = []
    if isinstance(registration_id, ObjectId):
        candidate_values.append(registration_id)
        candidate_values.append(str(registration_id))
    elif isinstance(registration_id, str):
        rid = registration_id.strip()
        if not rid:
            return None
        try:
            candidate_values.append(ObjectId(rid))
        except (InvalidId, TypeError):
            pass
        candidate_values.append(rid)
    else:
        try:
            candidate_values.append(ObjectId(registration_id))
        except (InvalidId, TypeError):
            pass
        candidate_values.append(registration_id)

    seen: set = set()
    for value in candidate_values:
        if value in seen:
            continue
        seen.add(value)
        reg = await db_mod.db.registrations.find_one({'_id': value})
        if reg:
            return reg
    return None


async def require_registration_owner_or_admin(user: dict, registration_id) -> dict:
    """Return registration document if current user is its owner or admin; else raise 403.

    Ownership is determined by either matching user_id with user's _id or
    matching user_email_snapshot with user's email (for legacy/compat records).
    """
    if registration_id is None:
        raise _HTTPException(status_code=400, detail='registration_id required')
    if isinstance(registration_id, str) and not registration_id.strip():
        raise _HTTPException(status_code=400, detail='registration_id required')
    reg = await get_registration_by_any_id(registration_id)
    if not reg:
        raise _HTTPException(status_code=404, detail='Registration not found')
    if _is_admin(user):
        return reg
    if reg.get('user_id') == user.get('_id'):
        return reg
    if (reg.get('user_email_snapshot') or '').lower() == (user.get('email') or '').lower():
        return reg
    raise _HTTPException(status_code=403, detail='Forbidden')


def _now_utc() -> datetime.datetime:
    # Keep using naive UTC datetime to match existing storage pattern
    return datetime.datetime.utcnow()


def require_event_registration_open(ev: dict) -> None:
    """Raise 400 if registration_deadline passed for the event."""
    if not ev:
        raise _HTTPException(status_code=404, detail='Event not found')
    ddl = ev.get('registration_deadline')
    if ddl:
        # Handle both datetime objects and ISO string formats
        deadline_dt = None
        if isinstance(ddl, datetime.datetime):
            # Ensure naive datetime is treated as UTC
            deadline_dt = ddl if ddl.tzinfo is not None else ddl.replace(tzinfo=datetime.timezone.utc)
        elif isinstance(ddl, str):
            try:
                from . import datetime_utils
                deadline_dt = datetime_utils.parse_iso(ddl)
            except (ValueError, ImportError):
                # If parsing fails, skip deadline check to avoid breaking registrations
                return
        
        if deadline_dt:
            # Compare as naive UTC for compatibility with existing code
            now_utc = _now_utc()
            deadline_naive = deadline_dt.replace(tzinfo=None) if deadline_dt.tzinfo else deadline_dt
            if now_utc > deadline_naive:
                raise _HTTPException(status_code=400, detail='Registration deadline passed')


def require_event_payment_open(ev: dict) -> None:
    """Raise 400 if payment_deadline passed for the event."""
    if not ev:
        raise _HTTPException(status_code=404, detail='Event not found')
    ddl = ev.get('payment_deadline')
    if ddl:
        # Handle both datetime objects and ISO string formats
        deadline_dt = None
        if isinstance(ddl, datetime.datetime):
            # Ensure naive datetime is treated as UTC
            deadline_dt = ddl if ddl.tzinfo is not None else ddl.replace(tzinfo=datetime.timezone.utc)
        elif isinstance(ddl, str):
            try:
                from . import datetime_utils
                deadline_dt = datetime_utils.parse_iso(ddl)
            except (ValueError, ImportError):
                # If parsing fails, skip deadline check to avoid breaking payments
                return
        
        if deadline_dt:
            # Compare as naive UTC for compatibility with existing code
            now_utc = _now_utc()
            deadline_naive = deadline_dt.replace(tzinfo=None) if deadline_dt.tzinfo else deadline_dt
            if now_utc > deadline_naive:
                raise _HTTPException(status_code=400, detail='Payment deadline passed')



async def send_notification(recipient: str, title: str, message_lines: Sequence[str]) -> bool:
    """Generic plaintext notification helper."""
    body = "\n".join(message_lines) + "\n"
    return await send_email(to=recipient, subject=title, body=body, category="notification")


# ---- Team helpers ----

def compute_team_diet(*diets: str | None) -> str:
    """Return the resulting team diet given member diets with precedence Vegan > Vegetarian > Omnivore.

    Accepts any casings and ignores unknown/None values by treating them as omnivore.
    Returns one of: 'vegan', 'vegetarian', 'omnivore'.
    """
    norm = [str(d).strip().lower() for d in diets if d]
    if 'vegan' in norm:
        return 'vegan'
    if 'vegetarian' in norm:
        return 'vegetarian'
    return 'omnivore'


async def send_payment_confirmation(registration_id) -> bool:
    """Send confirmation email(s) after successful payment for a registration.

    - If the registration is part of a team (team_size==2), notify both creator and partner (if registered)
    - Otherwise, notify the single registrant
    Returns True if at least one email was attempted (best-effort).
    """
    try:
        oid = registration_id if isinstance(registration_id, ObjectId) else ObjectId(registration_id)
    except InvalidId:
        return False
    reg = await db_mod.db.registrations.find_one({'_id': oid})
    if not reg:
        return False
    # Load event for context
    ev = None
    try:
        ev = await db_mod.db.events.find_one({'_id': reg.get('event_id')}) if reg.get('event_id') else None
    except PyMongoError:
        ev = None
    title = (ev or {}).get('title') or 'DinnerHopping Event'
    date = (ev or {}).get('date') or ''
    # Build recipient list
    recipients = set()
    if reg.get('user_email_snapshot'):
        recipients.add(reg['user_email_snapshot'])
    if reg.get('team_id'):
        async for other in db_mod.db.registrations.find({'team_id': reg['team_id']}):
            em = other.get('user_email_snapshot')
            if em:
                recipients.add(em)
    if not recipients:
        return False
    subject = f"Registration confirmed for {title}"
    lines = [
        f"Thanks for your payment! Your registration for '{title}' on {date} is now confirmed.",
        "You'll receive more information and your schedule closer to the event.",
        "",
        "Have a great time!",
        "— DinnerHopping Team",
    ]
    ok_any = False
    for to in recipients:
        ok = await send_email(to=to, subject=subject, body="\n".join(lines), category='payment_confirmation')
        ok_any = ok_any or ok
    return ok_any


async def finalize_registration_payment(registration_id, payment_id=None) -> bool:
    """Finalize a successful payment for a registration (solo or team) in an idempotent way.

    Responsibilities:
    - Mark the target registration (and any teammate registrations sharing team_id) as paid.
    - If a team is involved, also update the team document status to 'paid'.
    - Send confirmation emails (once) to all involved participant email snapshots.
    - Mark the Payment document (if provided) with confirmation_email_sent_at to avoid duplicate mails.

    This function is safe to call multiple times (e.g. concurrent webhook + return URL)
    because it checks the payment document for an existing confirmation_email_sent_at timestamp.
    """
    try:
        oid = registration_id if isinstance(registration_id, ObjectId) else ObjectId(registration_id)
    except (InvalidId, TypeError):
        return False
    reg = await db_mod.db.registrations.find_one({'_id': oid})
    if not reg:
        return False
    now = datetime.datetime.utcnow()

    # If supplied, load payment doc to check idempotency flag
    pay_doc = None
    if payment_id is not None:
        try:
            pay_oid = payment_id if isinstance(payment_id, ObjectId) else ObjectId(payment_id)
            pay_doc = await db_mod.db.payments.find_one({'_id': pay_oid})
        except (InvalidId, TypeError):
            pay_doc = None

    # Update registration(s) status to paid (idempotent updates)
    team_id = reg.get('team_id')
    if team_id:
        # Mark all registrations in the team paid
        try:
            await db_mod.db.registrations.update_many(
                {'team_id': team_id, 'status': {'$ne': 'paid'}},
                {'$set': {'status': 'paid', 'paid_at': now, 'updated_at': now}}
            )
        except PyMongoError:
            pass
        # Mark team document
        try:
            await db_mod.db.teams.update_one({'_id': team_id}, {'$set': {'status': 'paid', 'updated_at': now}})
        except PyMongoError:
            pass
    else:
        try:
            await db_mod.db.registrations.update_one(
                {'_id': reg['_id'], 'status': {'$ne': 'paid'}},
                {'$set': {'status': 'paid', 'paid_at': now, 'updated_at': now}}
            )
        except PyMongoError:
            pass

    # Only send emails once per payment
    if pay_doc and pay_doc.get('confirmation_email_sent_at'):
        return True

    await send_payment_confirmation(reg['_id'])

    if pay_doc:
        try:
            await db_mod.db.payments.update_one({'_id': pay_doc['_id']}, {'$set': {'confirmation_email_sent_at': now}})
        except PyMongoError:
            pass
    return True
