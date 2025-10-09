import datetime
from urllib.parse import quote

import pytest

from app import db as db_mod
from app.utils import generate_token_pair


@pytest.mark.asyncio
async def test_verify_email_accepts_double_encoded_token(client):
    email = "normalize@example.com"
    token, token_hash = generate_token_pair()
    now = datetime.datetime.now(datetime.timezone.utc)

    await db_mod.db.users.delete_many({"email": email})
    await db_mod.db.email_verifications.delete_many({"email": email})

    await db_mod.db.users.insert_one(
        {
            "email": email,
            "password_hash": "irrelevant",
            "email_verified": False,
            "failed_login_attempts": 0,
            "lockout_until": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    await db_mod.db.email_verifications.insert_one(
        {
            "email": email,
            "token_hash": token_hash,
            "created_at": now,
            "expires_at": now + datetime.timedelta(hours=1),
        }
    )

    # simulate a client double-encoding the token before sending the request
    double_encoded = quote(quote(token, safe=""), safe="")

    resp = await client.get(f"/verify-email?token={double_encoded}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("status") == "verified"

    user = await db_mod.db.users.find_one({"email": email})
    assert user is not None
    assert user.get("email_verified") is True

    remaining = await db_mod.db.email_verifications.find_one({"email": email})
    assert remaining is None
