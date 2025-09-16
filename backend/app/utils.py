import math
import os
import secrets
import datetime
from . import db as db_mod

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
    except Exception:
        # don't fail registration if the verification insert fails; still return token
        pass

    base = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
    verification_url = f"{base}/verify-email?token={token}"
    # For now: print to stdout. This is the visible 'email' during local/dev runs.
    print(f"[email] Verification for {email}: {verification_url}")
    return token
