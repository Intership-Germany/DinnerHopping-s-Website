import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_team_registration_requires_exactly_one_partner(
    client: AsyncClient,
    admin_token,
    verified_user,
):
    event_payload = {
        "title": "Team Event",
        "description": "Test",
        "date": "2030-05-01",
        "capacity": 10,
        "fee_cents": 0,
        "status": "open",
    }
    event_resp = await client.post(
        "/events",
        json=event_payload,
        headers={"Authorization": f"Bearer {admin_token}"},
        follow_redirects=True,
    )
    assert event_resp.status_code in (200, 201), event_resp.text
    event_id = event_resp.json()["id"]

    login_resp = await client.post(
        "/login",
        json={"username": verified_user["email"], "password": verified_user["password"]},
    )
    assert login_resp.status_code == 200, login_resp.text
    token = login_resp.json()["access_token"]

    headers = {"Authorization": f"Bearer {token}"}
    base_payload = {"event_id": event_id, "cooking_location": "creator"}

    missing_partner = await client.post(
        "/registrations/team",
        json=base_payload,
        headers=headers,
    )
    assert missing_partner.status_code == 400
    assert missing_partner.json().get("detail") == "exactly one of partner_existing or partner_external required"

    payload_both = {
        **base_payload,
        "partner_existing": {"email": "admin@example.com"},
        "partner_external": {
            "name": "External Friend",
            "email": "friend@example.com",
        },
    }
    both_resp = await client.post(
        "/registrations/team",
        json=payload_both,
        headers=headers,
    )
    assert both_resp.status_code == 400
    assert both_resp.json().get("detail") == "exactly one of partner_existing or partner_external required"
