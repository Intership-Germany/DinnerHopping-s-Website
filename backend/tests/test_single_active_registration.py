"""Tests for single active registration enforcement (Option A: global rule)."""
import os
import pytest
from fastapi import HTTPException

# Use fake DB for tests
os.environ['USE_FAKE_DB_FOR_TESTS'] = '1'

from app import db as db_mod
from app.routers.registrations import register_solo, register_team
from bson.objectid import ObjectId
import datetime


@pytest.fixture
async def test_data():
    """Setup fake DB with test data."""
    # Wait for DB connection from conftest
    if not db_mod.db:
        from app.db import connect as connect_to_mongo
        await connect_to_mongo()
    
    # Clear collections
    db_mod.db.users._store.clear()
    db_mod.db.events._store.clear()
    db_mod.db.registrations._store.clear()
    db_mod.db.teams._store.clear()
    
    # Create test user
    user_id = ObjectId()
    await db_mod.db.users.insert_one({
        '_id': user_id,
        'email': 'test@example.com',
        'default_dietary_preference': 'omnivore',
        'kitchen_available': True,
        'main_course_possible': True,
    })
    
    # Create two test events
    event1_id = ObjectId()
    event2_id = ObjectId()
    
    await db_mod.db.events.insert_one({
        '_id': event1_id,
        'title': 'Event 1',
        'status': 'published',
        'fee_cents': 500,
        'capacity': 100,
        'attendee_count': 0,
        'date': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7),
    })
    
    await db_mod.db.events.insert_one({
        '_id': event2_id,
        'title': 'Event 2',
        'status': 'published',
        'fee_cents': 500,
        'capacity': 100,
        'attendee_count': 0,
        'date': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=14),
    })
    
    return {
        'user_id': user_id,
        'user': {'email': 'test@example.com', '_id': user_id},
        'event1_id': event1_id,
        'event2_id': event2_id,
    }


@pytest.mark.asyncio
async def test_solo_registration_prevents_second_active_registration(test_data):
    """Test that a user cannot have two active solo registrations at once."""
    data = test_data
    
    # Mock the payload class
    class SoloPayload:
        def __init__(self, event_id):
            self.event_id = str(event_id)
            self.dietary_preference = None
            self.kitchen_available = None
            self.main_course_possible = None
            self.course_preference = None
    
    # First registration should succeed
    payload1 = SoloPayload(data['event1_id'])
    result1 = await register_solo(payload1, data['user'])
    
    assert result1['registration_id']
    assert result1['registration_status'] == 'pending_payment'
    
    # Second registration for a different event: previous active registration should be auto-cancelled
    # and the new registration should succeed (re-registration / auto-cancel behavior)
    payload2 = SoloPayload(data['event2_id'])
    result2 = await register_solo(payload2, data['user'])
    assert result2['registration_id']
    assert result2['registration_status'] == 'pending_payment'
    # Ensure the previous registration was cancelled
    prev_reg = await db_mod.db.registrations.find_one({'user_email_snapshot': 'test@example.com', 'event_id': data['event1_id']})
    assert prev_reg is not None
    assert prev_reg.get('status') in ('cancelled_by_user', 'cancelled_admin')


@pytest.mark.asyncio
async def test_solo_registration_allows_reregistration_same_event(test_data):
    """Test that a user can update their registration for the same event."""
    data = test_data
    
    class SoloPayload:
        def __init__(self, event_id):
            self.event_id = str(event_id)
            self.dietary_preference = None
            self.kitchen_available = None
            self.main_course_possible = None
            self.course_preference = None
    
    # First registration
    payload1 = SoloPayload(data['event1_id'])
    result1 = await register_solo(payload1, data['user'])
    reg_id_1 = result1['registration_id']
    
    # Second registration for the SAME event should succeed (updates existing)
    payload2 = SoloPayload(data['event1_id'])
    result2 = await register_solo(payload2, data['user'])
    
    # Should return same registration ID (update not create)
    assert result2['registration_id'] == reg_id_1


@pytest.mark.asyncio
async def test_cancelled_registration_allows_new_registration(test_data):
    """Test that cancelling a registration allows registration for another event."""
    data = test_data
    
    class SoloPayload:
        def __init__(self, event_id):
            self.event_id = str(event_id)
            self.dietary_preference = None
            self.kitchen_available = None
            self.main_course_possible = None
            self.course_preference = None
    
    # First registration
    payload1 = SoloPayload(data['event1_id'])
    result1 = await register_solo(payload1, data['user'])
    
    # Cancel the first registration
    reg = await db_mod.db.registrations.find_one({'_id': ObjectId(result1['registration_id'])})
    await db_mod.db.registrations.update_one(
        {'_id': reg['_id']},
        {'$set': {'status': 'cancelled_by_user', 'cancelled_at': datetime.datetime.now(datetime.timezone.utc)}}
    )
    
    # Second registration for different event should now succeed
    payload2 = SoloPayload(data['event2_id'])
    result2 = await register_solo(payload2, data['user'])
    
    assert result2['registration_id']
    assert result2['registration_status'] == 'pending_payment'
    # Should be a different registration
    assert result2['registration_id'] != result1['registration_id']


@pytest.mark.asyncio
async def test_team_registration_prevents_second_active_registration(test_data):
    """Test that team registration also enforces single active registration rule."""
    data = test_data
    
    # Add a partner user
    partner_id = ObjectId()
    await db_mod.db.users.insert_one({
        '_id': partner_id,
        'email': 'partner@example.com',
        'default_dietary_preference': 'vegetarian',
        'kitchen_available': True,
        'main_course_possible': False,
    })
    
    class TeamPayload:
        def __init__(self, event_id):
            self.event_id = str(event_id)
            self.partner_existing = type('obj', (object,), {'email': 'partner@example.com'})()
            self.partner_external = None
            self.dietary_preference = None
            self.kitchen_available = None
            self.main_course_possible = None
            self.course_preference = None
            self.cooking_location = 'creator'
    
    # First, create a solo registration for event 1
    class SoloPayload:
        def __init__(self, event_id):
            self.event_id = str(event_id)
            self.dietary_preference = None
            self.kitchen_available = None
            self.main_course_possible = None
            self.course_preference = None
    
    solo_payload = SoloPayload(data['event1_id'])
    await register_solo(solo_payload, data['user'])
    
    # Try to create team registration for event 2 - should auto-cancel previous solo and succeed
    team_payload = TeamPayload(data['event2_id'])
    result = await register_team(team_payload, data['user'])
    assert result['team_id']
    # Ensure previous solo registration was cancelled
    prev = await db_mod.db.registrations.find_one({'user_email_snapshot': 'test@example.com', 'event_id': data['event1_id']})
    assert prev is not None
    assert prev.get('status') in ('cancelled_by_user', 'cancelled_admin')


@pytest.mark.asyncio
async def test_team_registration_blocks_if_partner_has_active_registration(test_data):
    """Test that team registration fails if partner has active registration for different event."""
    data = test_data
    
    # Add a partner user
    partner_id = ObjectId()
    await db_mod.db.users.insert_one({
        '_id': partner_id,
        'email': 'partner@example.com',
        'default_dietary_preference': 'vegetarian',
        'kitchen_available': True,
        'main_course_possible': False,
    })
    
    # Give partner an active registration for event 1
    await db_mod.db.registrations.insert_one({
        '_id': ObjectId(),
        'event_id': data['event1_id'],
        'user_id': partner_id,
        'user_email_snapshot': 'partner@example.com',
        'team_size': 1,
        'status': 'pending_payment',
        'created_at': datetime.datetime.now(datetime.timezone.utc),
        'updated_at': datetime.datetime.now(datetime.timezone.utc),
    })
    
    class TeamPayload:
        def __init__(self, event_id):
            self.event_id = str(event_id)
            self.partner_existing = type('obj', (object,), {'email': 'partner@example.com'})()
            self.partner_external = None
            self.dietary_preference = None
            self.kitchen_available = None
            self.main_course_possible = None
            self.course_preference = None
            self.cooking_location = 'creator'
    
    # Try to create team registration for event 2 with partner who has active reg for event 1
    # Partner's active registration should be auto-cancelled and team creation should succeed
    team_payload = TeamPayload(data['event2_id'])
    result = await register_team(team_payload, data['user'])
    assert result['team_id']
    # Ensure partner's previous registration was cancelled
    partner_prev = await db_mod.db.registrations.find_one({'user_email_snapshot': 'partner@example.com', 'event_id': data['event1_id']})
    assert partner_prev is not None
    assert partner_prev.get('status') in ('cancelled_by_user', 'cancelled_admin')
