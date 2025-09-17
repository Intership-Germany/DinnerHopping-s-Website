import os
import secrets
import datetime
import logging
import asyncio  # for to_thread and sleep
from typing import Optional, Sequence, Mapping
from . import db as db_mod
import smtplib
from email.message import EmailMessage
import email.utils
import uuid

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
    lat_cell = round(lat / GRID_SIZE_DEG) * GRID_SIZE_DEG
    lon_cell = round(lon / GRID_SIZE_DEG) * GRID_SIZE_DEG
    return {"lat": lat_cell, "lon": lon_cell}

# Simple helper to strip address exactness
def anonymize_address(lat: float, lon: float) -> dict:
    cell = anonymize_coords(lat, lon)
    return {
        "center": cell,
        "approx_radius_m": 500,
    }

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
        except Exception:
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
                        server.starttls(); server.ehlo()
                    if smtp_user and smtp_pass:
                        server.login(smtp_user, smtp_pass)
                    envelope_from = smtp_user or from_addr
                    result = server.send_message(msg, from_addr=envelope_from, to_addrs=recipients)
                    if result:
                        logger.warning("SMTP partial failures: %s", result)
            return True, None
        except Exception as exc:
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


async def generate_and_send_verification(recipient: str) -> str:
    """Create a verification token & send verification email using send_email()."""
    token = secrets.token_urlsafe(32)
    created_at = datetime.datetime.now(datetime.timezone.utc)
    doc = {"email": recipient, "token": token, "created_at": created_at}
    try:
        await db_mod.db.email_verifications.insert_one(doc)
        logger.info("Stored verification token for %s", recipient)
    except Exception as e:
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
    return token


async def send_notification(recipient: str, title: str, message_lines: Sequence[str]) -> bool:
    """Generic plaintext notification helper.

    Example usage (future): await send_notification(user_email, "Event Updated", ["The event you registered for has a new venue."])
    """
    body = "\n".join(message_lines) + "\n"
    return await send_email(to=recipient, subject=title, body=body, category="notification")