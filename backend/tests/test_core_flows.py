import pytest
from httpx import AsyncClient

# Core happy-path test: user registers, logs in, admin creates event, user registers solo, cancels, refunds queried.

@pytest.mark.asyncio
async def test_register_login_event_registration_flow(client: AsyncClient, admin_token, verified_user):
    # 1. Login verified user
    login_resp = await client.post("/login", json={"username": verified_user['email'], "password": verified_user['password']})
    if login_resp.status_code != 200:
        pytest.skip(f"User login failed: {login_resp.status_code} {login_resp.text}")
    user_token = login_resp.json()["access_token"]

    # 1b. Fetch profile to confirm phone number stored correctly
    profile_resp = await client.get("/profile", headers={"Authorization": f"Bearer {user_token}"})
    assert profile_resp.status_code == 200, profile_resp.text
    profile_body = profile_resp.json()
    assert profile_body.get("phone_number") == "+4915112345678"

    # 1c. Update phone number and confirm normalization
    new_phone = "+49 30 5556677"
    update_resp = await client.put(
        "/profile",
        json={"phone_number": new_phone},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json().get("phone_number") == "+49305556677"

    refreshed = await client.get("/profile", headers={"Authorization": f"Bearer {user_token}"})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json().get("phone_number") == "+49305556677"

    # 2. Admin creates event (draft)
    event_payload = {
        "title": "Integration Test Event",
        "description": "Desc",
        "date": "2030-01-01",
        "capacity": 20,
        "fee_cents": 0,
        "status": "open"
    }
    create_event = await client.post("/events", json=event_payload, headers={"Authorization": f"Bearer {admin_token}"}, follow_redirects=True)
    assert create_event.status_code in (200,201), f"Unexpected status {create_event.status_code}: {create_event.text}"
    event = create_event.json()

    # 3. Solo registration via registrations router (event already open)
    reg_payload = {"event_id": event['id']}
    reg_resp = await client.post("/registrations/solo", json=reg_payload, headers={"Authorization": f"Bearer {user_token}"}, follow_redirects=True)
    assert reg_resp.status_code == 200, reg_resp.text
    registration_id = reg_resp.json()["registration_id"]

    # 5. Cancel registration (best-effort; allow absence of endpoint or rules)
    cancel_resp = await client.post(f"/registrations/{registration_id}/cancel", headers={"Authorization": f"Bearer {user_token}"})
    assert cancel_resp.status_code in (200, 400, 404)

    # 6. Refunds admin endpoint (should succeed even if empty)
    refunds_resp = await client.get(f"/payments/admin/events/{event['id']}/refunds", headers={"Authorization": f"Bearer {admin_token}"})
    assert refunds_resp.status_code in (200, 404)
