import datetime

import pytest
from bson.objectid import ObjectId

from app import db as db_mod


@pytest.mark.asyncio
async def test_stripe_webhook_processed_and_replayed(client, verified_user, monkeypatch):
    # Create a pending stripe payment
    reg = await db_mod.db.registrations.find_one({"user_email_snapshot": verified_user["email"]})
    if not reg:
        # create a registration
        ev_id = ObjectId()
        await db_mod.db.events.insert_one({"_id": ev_id, "status": "open", "fee_cents": 1500, "title": "E"})
        reg_id = ObjectId()
        now = datetime.datetime.now(datetime.timezone.utc)
        await db_mod.db.registrations.insert_one({"_id": reg_id, "event_id": ev_id, "user_id": None, "user_email_snapshot": verified_user["email"], "team_size": 1, "status": "pending", "created_at": now, "updated_at": now})
    else:
        reg_id = reg.get("_id")

    payment = {
        "_id": ObjectId(),
        "registration_id": reg_id,
        "amount": 15.0,
        "currency": "EUR",
        "status": "pending",
        "provider": "stripe",
        "provider_payment_id": "sess_123",
        "idempotency_key": "test-stripe-1",
    "created_at": datetime.datetime.now(datetime.timezone.utc),
    }
    await db_mod.db.payments.insert_one(payment)

    # Prepare a fake stripe event
    fake_event = {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {"id": "sess_123"}}}

    class FakeWebhook:
        @staticmethod
        def construct_event(payload, sig, secret):
            return fake_event

    monkeypatch.setitem(__import__('sys').modules, 'stripe', type('M', (), {'Webhook': FakeWebhook}))
    # Ensure the route uses signature verification branch
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_test')

    resp = await client.post('/payments/webhooks/stripe', content=b"{}", headers={'Stripe-Signature': 't=1,v1=abc'})
    assert resp.status_code == 200, resp.text
    assert resp.json().get('status') == 'processed'

    # Replay: same event id should be ignored
    resp2 = await client.post('/payments/webhooks/stripe', content=b"{}", headers={'Stripe-Signature': 't=1,v1=abc'})
    assert resp2.status_code == 200, resp2.text
    assert resp2.json().get('status') in ('already_processed', 'not_found', 'ignored')


@pytest.mark.asyncio
async def test_stripe_webhook_invalid_signature_rejected(client, verified_user, monkeypatch):
    # Ensure invalid signature is rejected when STRIPE_WEBHOOK_SECRET set
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_test')

    class BadWebhook:
        @staticmethod
        def construct_event(payload, sig, secret):
            raise ValueError('invalid')

    monkeypatch.setitem(__import__('sys').modules, 'stripe', type('M', (), {'Webhook': BadWebhook}))
    resp = await client.post('/payments/webhooks/stripe', content=b"{}", headers={'Stripe-Signature': 't=1,v1=bad'})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_paypal_webhook_signature_and_replay(client, verified_user, monkeypatch):
    # Create a pending paypal payment
    reg = await db_mod.db.registrations.find_one({"user_email_snapshot": verified_user["email"]})
    if not reg:
        ev_id = ObjectId()
        await db_mod.db.events.insert_one({"_id": ev_id, "status": "open", "fee_cents": 1500, "title": "E"})
        reg_id = ObjectId()
        now = datetime.datetime.now(datetime.timezone.utc)
        await db_mod.db.registrations.insert_one({"_id": reg_id, "event_id": ev_id, "user_id": None, "user_email_snapshot": verified_user["email"], "team_size": 1, "status": "pending", "created_at": now, "updated_at": now})
    else:
        reg_id = reg.get("_id")

    payment = {
        "_id": ObjectId(),
        "registration_id": reg_id,
        "amount": 15.0,
        "currency": "EUR",
        "status": "pending",
        "provider": "paypal",
        "provider_payment_id": "ord_abc",
        "idempotency_key": "test-paypal-1",
    "created_at": datetime.datetime.now(datetime.timezone.utc),
    }
    await db_mod.db.payments.insert_one(payment)

    # Fake verification to always succeed
    async def fake_verify(*args, **kwargs):
        return True

    monkeypatch.setattr('app.payments_providers.paypal.verify_webhook_signature', fake_verify)

    body = {"id": "evt_paypal_1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "ord_abc"}}
    resp = await client.post('/payments/webhooks/paypal', json=body, headers={
        'Paypal-Transmission-Id': 't1',
        'Paypal-Transmission-Time': 'now',
        'Paypal-Cert-Url': 'https://example',
        'Paypal-Auth-Algo': 'SHA256',
        'Paypal-Transmission-Sig': 'sig',
    })
    assert resp.status_code == 200, resp.text
    assert resp.json().get('status') == 'ok'

    # Replay: same event should be ignored/OK
    resp2 = await client.post('/payments/webhooks/paypal', json=body, headers={
        'Paypal-Transmission-Id': 't1',
        'Paypal-Transmission-Time': 'now',
        'Paypal-Cert-Url': 'https://example',
        'Paypal-Auth-Algo': 'SHA256',
        'Paypal-Transmission-Sig': 'sig',
    })
    assert resp2.status_code == 200, resp2.text
    assert resp2.json().get('status') in ('ok', 'ignored')


@pytest.mark.asyncio
async def test_paypal_webhook_invalid_signature_rejected(client, verified_user, monkeypatch):
    # Make verify return False to simulate invalid signature
    async def fake_verify_false(*args, **kwargs):
        return False

    # Ensure verification branch is exercised
    monkeypatch.setenv('PAYPAL_WEBHOOK_ID', 'wh_id_test')
    monkeypatch.setattr('app.payments_providers.paypal.verify_webhook_signature', fake_verify_false)
    body = {"id": "evt_paypal_2", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "ord_xyz"}}
    resp = await client.post('/payments/webhooks/paypal', json=body, headers={
        'Paypal-Transmission-Id': 't2',
        'Paypal-Transmission-Time': 'now',
        'Paypal-Cert-Url': 'https://example',
        'Paypal-Auth-Algo': 'SHA256',
        'Paypal-Transmission-Sig': 'sig',
    })
    assert resp.status_code == 400
