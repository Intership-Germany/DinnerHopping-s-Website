"""Tests for payment amount validation."""
import os
import pytest
from fastapi import HTTPException

# Use fake DB for tests
os.environ['USE_FAKE_DB_FOR_TESTS'] = '1'

from app import db as db_mod
from app.routers.payments import create_payment
from bson.objectid import ObjectId
import datetime


@pytest.fixture
async def test_payment_data():
    """Setup fake DB with test data for payment tests."""
    # Wait for DB connection from conftest
    if not db_mod.db:
        from app.db import connect as connect_to_mongo
        await connect_to_mongo()
    
    # Clear collections
    db_mod.db.users._store.clear()
    db_mod.db.events._store.clear()
    db_mod.db.registrations._store.clear()
    db_mod.db.payments._store.clear()
    
    # Create test user
    user_id = ObjectId()
    await db_mod.db.users.insert_one({
        '_id': user_id,
        'email': 'test@example.com',
    })
    
    # Create test event
    event_id = ObjectId()
    await db_mod.db.events.insert_one({
        '_id': event_id,
        'title': 'Test Event',
        'status': 'published',
        'fee_cents': 500,  # €5.00
        'capacity': 100,
        'attendee_count': 0,
        'date': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7),
    })
    
    # Create test registration
    reg_id = ObjectId()
    await db_mod.db.registrations.insert_one({
        '_id': reg_id,
        'event_id': event_id,
        'user_id': user_id,
        'user_email_snapshot': 'test@example.com',
        'team_size': 1,
        'status': 'pending_payment',
        'created_at': datetime.datetime.now(datetime.timezone.utc),
        'updated_at': datetime.datetime.now(datetime.timezone.utc),
    })
    
    return {
        'user_id': user_id,
        'user': {'email': 'test@example.com', '_id': user_id},
        'event_id': event_id,
        'reg_id': reg_id,
        'expected_amount': 500,
    }


@pytest.mark.asyncio
async def test_payment_amount_must_match_event_fee(test_payment_data):
    """Test that payment amount must match event fee * team_size."""
    data = test_payment_data
    
    # Mock the payment request
    class PaymentRequest:
        def __init__(self, reg_id, amount_cents):
            self.registration_id = str(reg_id)
            self.amount_cents = amount_cents
            self.idempotency_key = None
            self.provider = 'wero'  # Use wero since it doesn't require external API
            self.flow = 'redirect'
            self.currency = 'EUR'
    
    # Correct amount should work (or at least not fail on amount validation)
    correct_request = PaymentRequest(data['reg_id'], data['expected_amount'])
    
    try:
        result = await create_payment(correct_request, data['user'])
        # Should succeed (may have other issues but not amount validation)
        assert result is not None
        assert result.get('amount_cents') == data['expected_amount']
    except HTTPException as e:
        # Should not be a 400 error about amount mismatch
        if e.status_code == 400:
            assert 'amount' not in str(e.detail).lower()
    
    # Incorrect amount should fail
    wrong_request = PaymentRequest(data['reg_id'], 1000)  # Wrong: should be 500
    
    with pytest.raises(HTTPException) as exc_info:
        await create_payment(wrong_request, data['user'])
    
    assert exc_info.value.status_code == 400
    assert 'amount' in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_payment_amount_calculated_for_team(test_payment_data):
    """Test that payment amount is correctly calculated for teams (team_size * fee)."""
    data = test_payment_data
    
    # Update registration to be a team
    await db_mod.db.registrations.update_one(
        {'_id': data['reg_id']},
        {'$set': {'team_size': 2}}
    )
    
    # Mock the payment request
    class PaymentRequest:
        def __init__(self, reg_id, amount_cents):
            self.registration_id = str(reg_id)
            self.amount_cents = amount_cents
            self.idempotency_key = None
            self.provider = 'wero'
            self.flow = 'redirect'
            self.currency = 'EUR'
    
    # Correct team amount should be 2 * 500 = 1000
    correct_request = PaymentRequest(data['reg_id'], 1000)
    
    try:
        result = await create_payment(correct_request, data['user'])
        assert result is not None
        assert result.get('amount_cents') == 1000
    except HTTPException as e:
        if e.status_code == 400:
            assert 'amount' not in str(e.detail).lower()
    
    # Wrong amount (solo amount for team) should fail
    wrong_request = PaymentRequest(data['reg_id'], 500)
    
    with pytest.raises(HTTPException) as exc_info:
        await create_payment(wrong_request, data['user'])
    
    assert exc_info.value.status_code == 400
    assert 'amount' in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_payment_with_no_fee_event(test_payment_data):
    """Test that free events (fee_cents=0) don't require payment."""
    data = test_payment_data
    
    # Update event to be free
    await db_mod.db.events.update_one(
        {'_id': data['event_id']},
        {'$set': {'fee_cents': 0}}
    )
    
    # Mock the payment request
    class PaymentRequest:
        def __init__(self, reg_id):
            self.registration_id = str(reg_id)
            self.amount_cents = None
            self.idempotency_key = None
            self.provider = 'auto'
            self.flow = 'redirect'
            self.currency = 'EUR'
    
    request = PaymentRequest(data['reg_id'])
    result = await create_payment(request, data['user'])
    
    # Should return status indicating no payment required
    assert result is not None
    assert result.get('status') == 'no_payment_required'
    assert result.get('amount_cents') == 0
