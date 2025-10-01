import datetime

import pytest
from bson.objectid import ObjectId

from app import db as db_mod


async def _login(client, email: str, password: str) -> str:
    resp = await client.post(
        "/login",
        json={"username": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    token = data.get("access_token")
    assert token
    return token


async def _setup_registration(email: str, fee_cents: int = 1500):
    user = await db_mod.db.users.find_one({"email": email})
    assert user is not None
    event_id = ObjectId()
    await db_mod.db.events.insert_one({
        "_id": event_id,
        "status": "open",
        "fee_cents": fee_cents,
        "title": "Test Event",
    })
    reg_id = ObjectId()
    now = datetime.datetime.now(datetime.timezone.utc)
    await db_mod.db.registrations.insert_one({
        "_id": reg_id,
        "event_id": event_id,
        "user_id": user.get("_id"),
        "user_email_snapshot": email,
        "team_size": 1,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    })
    return reg_id


@pytest.mark.asyncio
async def test_wero_payment_is_idempotent(client, verified_user):
    reg_id = await _setup_registration(verified_user["email"])
    token = await _login(client, verified_user["email"], verified_user["password"])
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"registration_id": str(reg_id), "provider": "wero"}

    first = await client.post("/payments/", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    second = await client.post("/payments/", json=payload, headers=headers)
    assert second.status_code == 200, second.text

    body1 = first.json()
    body2 = second.json()
    assert body1["payment_id"] == body2["payment_id"]
    assert body1["idempotency_key"] == body2["idempotency_key"]
    assert body1["status"] == body2["status"]


@pytest.mark.asyncio
async def test_custom_idempotency_key_sanitized(client, verified_user):
    reg_id = await _setup_registration(verified_user["email"])
    token = await _login(client, verified_user["email"], verified_user["password"])
    headers = {"Authorization": f"Bearer {token}"}
    custom_key = "  Custom Key 123\n"
    payload = {
        "registration_id": str(reg_id),
        "provider": "wero",
        "idempotency_key": custom_key,
    }

    resp = await client.post("/payments/", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    payment_id = body["payment_id"]
    assert payment_id
    stored = await db_mod.db.payments.find_one({"_id": ObjectId(payment_id)})
    assert stored is not None
    assert stored.get("idempotency_key") == "custom-key-123"


@pytest.mark.asyncio
async def test_idempotency_key_truncated_to_limit(client, verified_user):
    reg_id = await _setup_registration(verified_user["email"])
    token = await _login(client, verified_user["email"], verified_user["password"])
    headers = {"Authorization": f"Bearer {token}"}
    very_long_key = "x" * 200
    payload = {
        "registration_id": str(reg_id),
        "provider": "wero",
        "idempotency_key": very_long_key,
    }

    resp = await client.post("/payments/", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    payment_id = resp.json()["payment_id"]
    stored = await db_mod.db.payments.find_one({"_id": ObjectId(payment_id)})
    assert stored is not None
    key = stored.get("idempotency_key")
    assert isinstance(key, str)
    assert len(key) == 128
    assert key == "x" * 128
