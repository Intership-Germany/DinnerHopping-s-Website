import pytest
from httpx import AsyncClient
from app import db as db_mod


@pytest.mark.asyncio
async def test_manual_payment_flow(client: AsyncClient, admin_token, verified_user):
    """Test the complete manual payment flow: create, list, approve."""
    
    # 1. Login as verified user
    login_resp = await client.post("/login", json={"username": verified_user['email'], "password": verified_user['password']})
    assert login_resp.status_code == 200, login_resp.text
    user_token = login_resp.json()["access_token"]
    
    # 2. Admin creates an event with fee
    event_payload = {
        "title": "Manual Payment Test Event",
        "description": "Test event for manual payments",
        "date": "2030-06-15",
        "capacity": 20,
        "fee_cents": 1500,  # 15 EUR
        "status": "open"
    }
    create_event = await client.post("/events", json=event_payload, headers={"Authorization": f"Bearer {admin_token}"})
    assert create_event.status_code in (200, 201), f"Unexpected status {create_event.status_code}: {create_event.text}"
    event = create_event.json()
    event_id = event['id']
    
    # 3. User registers for the event (solo)
    reg_payload = {"event_id": event_id}
    reg_resp = await client.post("/registrations/solo", json=reg_payload, headers={"Authorization": f"Bearer {user_token}"})
    assert reg_resp.status_code == 200, reg_resp.text
    reg_data = reg_resp.json()
    registration_id = reg_data['registration_id']
    
    # 4. Create payment with "other" provider (manual payment)
    payment_payload = {
        "registration_id": registration_id,
        "provider": "other"
    }
    payment_resp = await client.post("/payments/create", json=payment_payload, headers={"Authorization": f"Bearer {user_token}"})
    assert payment_resp.status_code == 200, payment_resp.text
    payment_data = payment_resp.json()
    
    # Verify next_action indicates manual approval
    assert payment_data.get("next_action", {}).get("type") == "manual_approval"
    payment_id = payment_data.get("payment_id")
    assert payment_id is not None
    
    # Verify payment status is waiting_manual_approval
    payment_doc = await db_mod.db.payments.find_one({"_id": db_mod.ObjectId(payment_id)})
    assert payment_doc is not None
    assert payment_doc["status"] == "waiting_manual_approval"
    assert payment_doc["provider"] == "other"
    
    # 5. Admin lists manual payments
    list_resp = await client.get("/payments/admin/manual-payments?status=waiting_manual_approval", headers={"Authorization": f"Bearer {admin_token}"})
    assert list_resp.status_code == 200, list_resp.text
    list_data = list_resp.json()
    payments = list_data.get("payments", [])
    assert len(payments) >= 1
    
    # Find our payment in the list
    our_payment = next((p for p in payments if p["payment_id"] == payment_id), None)
    assert our_payment is not None
    assert our_payment["user_email"] == verified_user['email']
    assert our_payment["amount"] == 15.0
    assert our_payment["currency"] == "EUR"
    
    # 6. Admin approves the payment
    approve_resp = await client.post(f"/payments/admin/manual-payments/{payment_id}/approve", headers={"Authorization": f"Bearer {admin_token}"})
    assert approve_resp.status_code == 200, approve_resp.text
    approve_data = approve_resp.json()
    assert approve_data["status"] == "approved"
    
    # Verify payment is now succeeded
    payment_doc_after = await db_mod.db.payments.find_one({"_id": db_mod.ObjectId(payment_id)})
    assert payment_doc_after["status"] == "succeeded"
    assert payment_doc_after.get("paid_at") is not None
    
    # Verify registration is now paid
    reg_doc = await db_mod.db.registrations.find_one({"_id": db_mod.ObjectId(registration_id)})
    assert reg_doc["status"] == "paid"


@pytest.mark.asyncio
async def test_manual_payment_rejection(client: AsyncClient, admin_token, verified_user):
    """Test rejecting a manual payment."""
    
    # 1. Login as verified user
    login_resp = await client.post("/login", json={"username": verified_user['email'], "password": verified_user['password']})
    assert login_resp.status_code == 200, login_resp.text
    user_token = login_resp.json()["access_token"]
    
    # 2. Admin creates an event with fee
    event_payload = {
        "title": "Manual Payment Rejection Test",
        "description": "Test event",
        "date": "2030-07-15",
        "capacity": 20,
        "fee_cents": 2000,  # 20 EUR
        "status": "open"
    }
    create_event = await client.post("/events", json=event_payload, headers={"Authorization": f"Bearer {admin_token}"})
    assert create_event.status_code in (200, 201), create_event.text
    event = create_event.json()
    event_id = event['id']
    
    # 3. User registers for the event
    reg_payload = {"event_id": event_id}
    reg_resp = await client.post("/registrations/solo", json=reg_payload, headers={"Authorization": f"Bearer {user_token}"})
    assert reg_resp.status_code == 200, reg_resp.text
    registration_id = reg_resp.json()['registration_id']
    
    # 4. Create manual payment
    payment_payload = {
        "registration_id": registration_id,
        "provider": "other"
    }
    payment_resp = await client.post("/payments/create", json=payment_payload, headers={"Authorization": f"Bearer {user_token}"})
    assert payment_resp.status_code == 200, payment_resp.text
    payment_id = payment_resp.json().get("payment_id")
    
    # 5. Admin rejects the payment
    reject_resp = await client.post(f"/payments/admin/manual-payments/{payment_id}/reject", headers={"Authorization": f"Bearer {admin_token}"})
    assert reject_resp.status_code == 200, reject_resp.text
    reject_data = reject_resp.json()
    assert reject_data["status"] == "rejected"
    
    # Verify payment status is failed
    payment_doc = await db_mod.db.payments.find_one({"_id": db_mod.ObjectId(payment_id)})
    assert payment_doc["status"] == "failed"


@pytest.mark.asyncio
async def test_other_provider_in_list(client: AsyncClient):
    """Test that 'other' provider is included in the providers list."""
    
    # Get providers list (no auth required)
    providers_resp = await client.get("/payments/providers")
    assert providers_resp.status_code == 200, providers_resp.text
    providers_data = providers_resp.json()
    
    providers = providers_data.get("providers", [])
    assert "other" in providers
