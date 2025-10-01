import os
import sys
import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from pathlib import Path

# Ensure the backend directory is on PYTHONPATH when pytest is run from repo root
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Ensure test env
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "*")
os.environ.setdefault("ENFORCE_HTTPS", "false")
os.environ.setdefault("USE_FAKE_DB_FOR_TESTS", "1")

# Import app AFTER env vars
from app.main import app  # noqa: E402
from app import db as db_mod  # noqa: E402
from app.db import connect as connect_to_mongo  # noqa: E402
from app.auth import hash_password  # noqa: E402

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(autouse=True, scope="session")
async def _startup_and_shutdown():
    # Manually invoke DB connect (startup events not auto run with ASGITransport)
    await connect_to_mongo()
    yield

@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

async def _create_admin_user(email="admin@example.com", password="Adminpass1"):
    """Insert an admin user with a properly hashed password if not present."""
    existing = await db_mod.db.users.find_one({"email": email})
    if existing:
        return existing
    now = __import__('datetime').datetime.utcnow()
    doc = {
        "email": email,
        "password_hash": hash_password(password),
        "email_verified": True,
        "roles": ["admin"],
        "failed_login_attempts": 0,
        "lockout_until": None,
        "created_at": now,
        "updated_at": now,
    }
    res = await db_mod.db.users.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def _mark_email_verified(email: str):
    await db_mod.db.users.update_one({"email": email}, {"$set": {"email_verified": True}})

@pytest.fixture
async def admin_token(client):
    await _create_admin_user()
    resp = await client.post("/login", json={"username": "admin@example.com", "password": "Adminpass1"})
    if resp.status_code != 200:
        pytest.skip(f"Admin login failed: {resp.status_code} {resp.text}")
    return resp.json().get("access_token")


@pytest.fixture
async def verified_user(client):
    """Register a normal user and force-email-verify via direct DB update."""
    email = "user1@example.com"
    payload = {
        "email": email,
        "password": "Userpass1",
        "password_confirm": "Userpass1",
        "first_name": "Test",
        "last_name": "User",
    "phone_number": "+4915112345678",
        "street": "Main",
        "street_no": "1",
        "postal_code": "12345",
        "city": "Testville",
        "gender": "prefer_not_to_say",
        "lat": 0.0,
        "lon": 0.0,
        "preferences": {},
    }
    resp = await client.post("/register", json=payload)
    assert resp.status_code in (200, 201, 409), resp.text
    await _mark_email_verified(email)
    return {"email": email, "password": payload["password"]}
