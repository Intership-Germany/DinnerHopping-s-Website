import asyncio
import datetime

import pytest

from app import db as db_mod
from app.auth import hash_password
from app.utils import generate_token_pair


@pytest.mark.asyncio
async def test_password_reset_token_valid_and_used(client):
    email = "resetuser@example.com"
    # create user
    now = datetime.datetime.now(datetime.timezone.utc)
    user_doc = {
        "email": email,
        "password_hash": hash_password("Userpass1"),
        "email_verified": True,
        "created_at": now,
        "updated_at": now,
    }
    await db_mod.db.users.insert_one(user_doc)

    # request forgot-password
    resp = await client.post("/forgot-password", json={"email": email})
    assert resp.status_code == 200

    # find token record
    rec = await db_mod.db.password_resets.find_one({"email": email})
    assert rec is not None

    # attempt to use token
    # we don't have plaintext token here, but tests can exercise flow by
    # reading token_hash and simulating correct hashed token via generate_token_pair()
    # Instead, replicate generate_token_pair behavior: in the real flow token is emailed.
    # To simulate, we will insert a known token record and then use it.
    token, token_hash = generate_token_pair()
    now = datetime.datetime.now(datetime.timezone.utc)
    await db_mod.db.password_resets.insert_one({"email": email, "token_hash": token_hash, "created_at": now, "expires_at": now + datetime.timedelta(hours=1)})

    # POST reset-password
    resp2 = await client.post("/reset-password", json={"token": token, "new_password": "Newpass1"})
    assert resp2.status_code == 200
    assert resp2.json().get("status") == "password_reset"

    # token should now be marked used (or deleted)
    rec2 = await db_mod.db.password_resets.find_one({"token_hash": token_hash})
    # depending on implementation, token may be updated to status 'used' or deleted; accept either
    if rec2:
        assert rec2.get("status") == "used"


@pytest.mark.asyncio
async def test_password_reset_token_expiry(client):
    email = "expireuser@example.com"
    now = datetime.datetime.now(datetime.timezone.utc)
    user_doc = {
        "email": email,
        "password_hash": hash_password("Userpass1"),
        "email_verified": True,
        "created_at": now,
        "updated_at": now,
    }
    await db_mod.db.users.insert_one(user_doc)

    # create a token that expired an hour ago
    token, token_hash = generate_token_pair()
    expired_at = now - datetime.timedelta(hours=1)
    await db_mod.db.password_resets.insert_one({"email": email, "token_hash": token_hash, "created_at": now - datetime.timedelta(hours=2), "expires_at": expired_at})

    # Attempt GET validation
    resp = await client.get(f"/reset-password?token={token}")
    assert resp.status_code == 400
    assert "expired" in resp.text or "expired" in resp.json().get("detail", "")

    # Attempt POST reset should also fail
    resp2 = await client.post("/reset-password", json={"token": token, "new_password": "Newpass1"})
    assert resp2.status_code == 400 or resp2.status_code == 404