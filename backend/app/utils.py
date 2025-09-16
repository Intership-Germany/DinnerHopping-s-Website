import os
import secrets
import datetime
from . import db as db_mod
import smtplib
from email.message import EmailMessage

# Anonymisation: grid-cell centré, rayon 500m
# Simple approach: quantize lat/lon to ~500m grid using Haversine-based degrees approximation
# For typical latitudes, 0.0045 deg ~ 500m (varies). We'll use 0.0045 to approximate 500m.

GRID_SIZE_DEG = 0.0045

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
    """Create a verification token, persist it and "send" it.

    This is intentionally lightweight: it stores a record in `email_verifications`
    and prints the verification link to stdout. In production replace with
    a real SMTP / transactional email provider.
    """
    token = secrets.token_urlsafe(32)
    doc = {"email": email, "token": token, "created_at": datetime.datetime.utcnow()}
    try:
        await db_mod.db.email_verifications.insert_one(doc)
    except (OSError, Exception):
        # don't fail registration if the verification insert fails; still return token
        # catching OSError for low-level I/O issues; keep broad Exception to avoid
        # breaking registration for unexpected DB errors in this lightweight app.
        pass

    base = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
    verification_url = f"{base}/verify-email?token={token}"

    # Compose a simple verification email
    subject = "Please verify your DinnerHopping account"
    from_addr = os.getenv('SMTP_FROM_ADDRESS', 'info@acrevon.fr')
    body = f"Hi,\n\nPlease verify your email by clicking the link below:\n{verification_url}\n\nIf you didn't request this, ignore this message.\n\nThanks,\nDinnerHopping Team"

    # Try to send via SMTP if configuration exists, otherwise fallback to printing
    smtp_host = os.getenv('SMTP_HOST')
    smtp_port = int(os.getenv('SMTP_PORT', '587')) if os.getenv('SMTP_PORT') else None
    smtp_user = os.getenv('SMTP_USER')
    smtp_pass = os.getenv('SMTP_PASS')
    use_tls = os.getenv('SMTP_USE_TLS', 'true').lower() in ('1', 'true', 'yes')

    if smtp_host and smtp_port:
        try:
            msg = EmailMessage()
            msg['Subject'] = subject
            msg['From'] = from_addr
            msg['To'] = email
            msg.set_content(body)

            if use_tls:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
                server.ehlo()
                server.starttls()
                server.ehlo()
            else:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)

            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)

            server.send_message(msg)
            server.quit()
            print(f"[email] Sent verification to {email} via SMTP {smtp_host}:{smtp_port}")
        except (smtplib.SMTPException, OSError) as e:
            # Don't break registration on email send failure; print for visibility
            print(f"[email][error] Failed to send verification to {email} via SMTP: {e}")
            print(f"[email] Verification link (fallback): {verification_url}")
    else:
        # For now: print to stdout. This is the visible 'email' during local/dev runs.
        print(f"[email] Verification for {email}: {verification_url}")

    return token
