import json
import pytest


@pytest.mark.asyncio
async def test_login_accepts_json_username(client, verified_user):
    resp = await client.post(
        "/login",
        json={"username": verified_user["email"], "password": verified_user["password"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("access_token")


@pytest.mark.asyncio
async def test_login_accepts_json_email_key(client, verified_user):
    resp = await client.post(
        "/login",
        json={"email": verified_user["email"], "password": verified_user["password"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("access_token")


@pytest.mark.asyncio
async def test_login_accepts_form_fields(client, verified_user):
    resp = await client.post(
        "/login",
        data={"username": verified_user["email"], "password": verified_user["password"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("access_token")


@pytest.mark.asyncio
async def test_login_accepts_payload_form_json(client, verified_user):
    payload = json.dumps({"username": verified_user["email"], "password": verified_user["password"]})
    resp = await client.post(
        "/login",
        data={"payload_form": payload},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("access_token")


@pytest.mark.asyncio
async def test_login_accepts_payload_field_json(client, verified_user):
    payload = json.dumps({"email": verified_user["email"], "password": verified_user["password"]})
    resp = await client.post(
        "/login",
        data={"payload": payload},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("access_token")


@pytest.mark.asyncio
async def test_login_rejects_invalid_payload_json(client):
    resp = await client.post(
        "/login",
        data={"payload_form": "{not-json"},
    )
    assert resp.status_code == 422
    assert resp.json().get("detail") == "payload must be valid JSON"
