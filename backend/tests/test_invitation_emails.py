import pytest
from httpx import AsyncClient
from app import db as db_mod


@pytest.mark.asyncio
async def test_invitation_email_created(client: AsyncClient, admin_token, verified_user):
    """Test that invitations are created with event_id when registering with invited_emails."""
    
    # 1. Login as verified user
    login_resp = await client.post("/login", json={"username": verified_user['email'], "password": verified_user['password']})
    assert login_resp.status_code == 200, login_resp.text
    user_token = login_resp.json()["access_token"]
    
    # 2. Admin creates an event
    event_payload = {
        "title": "Invitation Test Event",
        "description": "Test event for invitations",
        "date": "2030-08-20",
        "capacity": 30,
        "fee_cents": 0,
        "status": "open"
    }
    create_event = await client.post("/events", json=event_payload, headers={"Authorization": f"Bearer {admin_token}"})
    assert create_event.status_code in (200, 201), create_event.text
    event = create_event.json()
    event_id = event['id']
    
    # 3. User registers with invited friends
    invited_email = "friend@example.com"
    reg_payload = {
        "event_id": event_id,
        "invited_emails": [invited_email]
    }
    reg_resp = await client.post(f"/events/{event_id}/register", json=reg_payload, headers={"Authorization": f"Bearer {user_token}"})
    assert reg_resp.status_code == 200, reg_resp.text
    reg_data = reg_resp.json()
    
    # Verify invitation was sent
    assert invited_email in reg_data.get("invitations_sent", [])
    
    # 4. Check invitation was created in database with event_id
    invitation = await db_mod.db.invitations.find_one({"invited_email": invited_email})
    assert invitation is not None
    assert invitation["status"] == "pending"
    assert invitation.get("event_id") is not None
    assert str(invitation["event_id"]) == event_id
    
    # 5. Verify invitation has an expiration date
    assert invitation.get("expires_at") is not None


@pytest.mark.asyncio
async def test_multiple_invitations(client: AsyncClient, admin_token, verified_user):
    """Test registering with multiple invited friends."""
    
    # 1. Login as verified user
    login_resp = await client.post("/login", json={"username": verified_user['email'], "password": verified_user['password']})
    assert login_resp.status_code == 200, login_resp.text
    user_token = login_resp.json()["access_token"]
    
    # 2. Admin creates an event
    event_payload = {
        "title": "Multi Invitation Test",
        "description": "Test event",
        "date": "2030-09-15",
        "capacity": 40,
        "fee_cents": 0,
        "status": "open"
    }
    create_event = await client.post("/events", json=event_payload, headers={"Authorization": f"Bearer {admin_token}"})
    assert create_event.status_code in (200, 201), create_event.text
    event = create_event.json()
    event_id = event['id']
    
    # 3. User registers with multiple invited friends
    invited_emails = ["friend1@example.com", "friend2@example.com", "friend3@example.com"]
    reg_payload = {
        "event_id": event_id,
        "invited_emails": invited_emails
    }
    reg_resp = await client.post(f"/events/{event_id}/register", json=reg_payload, headers={"Authorization": f"Bearer {user_token}"})
    assert reg_resp.status_code == 200, reg_resp.text
    reg_data = reg_resp.json()
    
    # Verify all invitations were sent
    sent = reg_data.get("invitations_sent", [])
    for email in invited_emails:
        assert email in sent
    
    # 4. Check all invitations were created in database
    for email in invited_emails:
        invitation = await db_mod.db.invitations.find_one({"invited_email": email})
        assert invitation is not None
        assert str(invitation["event_id"]) == event_id


@pytest.mark.asyncio
async def test_registration_without_invitations(client: AsyncClient, admin_token, verified_user):
    """Test that registration works without invited_emails (backward compatibility)."""
    
    # 1. Login as verified user
    login_resp = await client.post("/login", json={"username": verified_user['email'], "password": verified_user['password']})
    assert login_resp.status_code == 200, login_resp.text
    user_token = login_resp.json()["access_token"]
    
    # 2. Admin creates an event
    event_payload = {
        "title": "No Invitation Test",
        "description": "Test event without invitations",
        "date": "2030-10-10",
        "capacity": 20,
        "fee_cents": 0,
        "status": "open"
    }
    create_event = await client.post("/events", json=event_payload, headers={"Authorization": f"Bearer {admin_token}"})
    assert create_event.status_code in (200, 201), create_event.text
    event = create_event.json()
    event_id = event['id']
    
    # 3. User registers without invited_emails
    reg_payload = {
        "event_id": event_id
    }
    reg_resp = await client.post(f"/events/{event_id}/register", json=reg_payload, headers={"Authorization": f"Bearer {user_token}"})
    assert reg_resp.status_code == 200, reg_resp.text
    reg_data = reg_resp.json()
    
    # Verify empty invitations list
    assert reg_data.get("invitations_sent", []) == []
