import os
import secrets
import datetime
import logging
import asyncio  # added for to_thread and sleep
from typing import Optional  # added for smtp_port typing
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

async def generate_and_send_verification(email: str) -> str:
    """Create a verification token, persist it and attempt to send it.

    - Stores a record in `email_verifications`.
    - Attempts to send via SMTP (supports implicit TLS on 465 and STARTTLS on 587).
    - Runs the blocking SMTP work in a thread so the async loop isn't blocked.
    - Retries transient failures a few times with exponential backoff.
    - Falls back to printing the link on failure.
    """
    # 1) create token & persist (async DB operation)
    token = secrets.token_urlsafe(32)
    created_at = datetime.datetime.now(datetime.timezone.utc)
    doc = {"email": email, "token": token, "created_at": created_at}
    try:
        await db_mod.db.email_verifications.insert_one(doc)
        logger.info(f"Stored verification token for {email}")
    except Exception as e:
        logger.warning(f"Could not persist verification token for {email}: {e}")

    # 2) build verification URL and email body
    base = os.getenv("BACKEND_BASE_URL", "http://localhost:8000")
    verification_url = f"{base}/verify-email?token={token}"

    subject = "Please verify your DinnerHopping account"
    from_addr = os.getenv("SMTP_FROM_ADDRESS", "info@acrevon.fr")
    body = (
        f"Hi,\n\nPlease verify your email by clicking the link below:\n{verification_url}\n\n"
        "If you didn't request this, ignore this message.\n\nThanks,\nDinnerHopping Team"
    )

    # 3) collect SMTP config
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port_env = os.getenv("SMTP_PORT")
    smtp_port: Optional[int] = int(smtp_port_env) if smtp_port_env else None
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    use_tls_env = os.getenv("SMTP_USE_TLS", "true").lower()
    use_tls = use_tls_env in ("1", "true", "yes")
    smtp_timeout = int(os.getenv("SMTP_TIMEOUT_SECONDS", "10"))
    # Log config (mask password) for diagnostics
    logger.debug(
        "SMTP config host=%s port=%s user=%s tls=%s timeout=%ss", 
        smtp_host, smtp_port, smtp_user, use_tls, smtp_timeout
    )

    # 4) helper: synchronous send (to run in thread)
    def _send_sync():
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = email
        msg.set_content(body)
        # Add headers to improve deliverability and tracing
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = f"<{uuid.uuid4().hex}@dinnerhopping.local>"
        msg["X-Mailer"] = "DinnerHopping/1.0"

        # Decide between implicit TLS (SMTPS) and STARTTLS
        # Common convention: port 465 => implicit TLS (SMTP_SSL)
        # port 587 or other => SMTP + STARTTLS when use_tls is true
        try:
            if smtp_port == 465:
                logger.debug("Using implicit TLS (SMTP_SSL) because port=465")
                with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=smtp_timeout) as server:
                    server.ehlo()
                    if smtp_user and smtp_pass:
                        server.login(smtp_user, smtp_pass)
                    result = server.send_message(msg)
                    if result:
                        logger.warning("SMTP_SSL send returned failures: %s", result)
            else:
                # plain or STARTTLS
                with smtplib.SMTP(smtp_host, smtp_port, timeout=smtp_timeout) as server:
                    server.ehlo()
                    if use_tls:
                        server.starttls()
                        server.ehlo()
                    if smtp_user and smtp_pass:
                        server.login(smtp_user, smtp_pass)
                    result = server.send_message(msg)
                    # result is a dict of recipients that were refused
                    if result:
                        logger.warning("SMTP send returned failures: %s", result)
            return True, None
        except Exception as exc:
            # return exception for async caller to handle
            return False, exc

    # 5) attempt send with retries if configured; otherwise fallback to print
    if smtp_host and smtp_port:
        logger.info(f"Attempting SMTP send host={smtp_host} port={smtp_port} tls={use_tls} to={email}")
        max_retries = int(os.getenv("SMTP_MAX_RETRIES", "2"))
        attempt = 0
        last_exc = None
        while attempt <= max_retries:
            attempt += 1
            start = datetime.datetime.now(datetime.timezone.utc)
            ok, exc = await asyncio.to_thread(_send_sync)
            elapsed = (datetime.datetime.now(datetime.timezone.utc) - start).total_seconds()
            if ok:
                logger.info(f"Verification email sent to {email} in {elapsed:.2f}s")
                break
            last_exc = exc
            # If we've exhausted attempts, log and fallback
            if attempt > max_retries:
                logger.error(f"Failed SMTP send to {email} after {attempt} attempts: {last_exc}. Falling back to console link.")
                print(f"[email] Verification link (fallback): {verification_url}")
                break
            # Exponential backoff (small, safe for request flow)
            backoff = min(2 ** (attempt - 1), 8)
            logger.warning(f"SMTP send attempt {attempt} failed after {elapsed:.2f}s: {exc!r}. Retrying in {backoff}s...")
            await asyncio.sleep(backoff)
    else:
        # No SMTP configured -> dev fallback
        logger.info(f"No SMTP configured; printing verification link for {email}")
        print(f"[email] Verification for {email}: {verification_url}")

    return token