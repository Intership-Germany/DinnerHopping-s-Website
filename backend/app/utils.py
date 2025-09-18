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
from email.message import EmailMessage
from typing import Mapping, Optional, Sequence
import base64
import os
import re

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception:  # pragma: no cover - optional dependency in lightweight dev setups
    AESGCM = None

from . import db as db_mod

# Anonymisation: grid-cell centré, rayon 500m
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
    except Exception:
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
    except Exception:
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
        except smtplib.SMTPException as exc:
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
        ok, exc = await asyncio.to_thread(_send_once)
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
    token = secrets.token_urlsafe(32)
    created_at = datetime.datetime.now(datetime.timezone.utc)
    doc = {"email": recipient, "token": token, "created_at": created_at}
    try:
        await db_mod.db.email_verifications.insert_one(doc)
        logger.info("Stored verification token for %s", recipient)
    except Exception as e:  # broad except to avoid dependency on db.errors in tests
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


async def send_notification(recipient: str, title: str, message_lines: Sequence[str]) -> bool:
    """Generic plaintext notification helper."""
    body = "\n".join(message_lines) + "\n"
    return await send_email(to=recipient, subject=title, body=body, category="notification")
