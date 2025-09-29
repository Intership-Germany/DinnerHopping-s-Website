"""
Integration tests to verify that registration deadline enforcement works
across all registration endpoints.
"""
import pytest
import datetime
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.utils import _now_utc


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_db():
    """Mock database operations."""
    with patch('app.routers.events.db_mod.db') as mock_db, \
         patch('app.routers.registrations.db_mod.db') as mock_reg_db:
        mock_db.events = mock_db
        mock_reg_db.events = mock_reg_db
        mock_reg_db.registrations = mock_reg_db
        yield mock_db


def test_events_register_endpoint_respects_string_deadline(client, mock_db):
    """Test that /events/{event_id}/register endpoint respects string deadlines."""
    from bson import ObjectId
    from unittest.mock import AsyncMock
    
    event_id = str(ObjectId())
    past_deadline_str = "2023-01-01T10:00:00"  # Past deadline as string
    
    # Mock event with past deadline as string
    mock_event = {
        '_id': ObjectId(event_id),
        'title': 'Test Event',
        'status': 'open',
        'registration_deadline': past_deadline_str
    }
    
    mock_db.find_one = AsyncMock(return_value=mock_event)
    
    # Mock user
    with patch('app.auth.get_current_user', return_value={'_id': 'user123', 'email': 'test@example.com'}):
        response = client.post(f'/events/{event_id}/register', json={})
        
        # Should get 400 error for past deadline
        assert response.status_code == 400
        assert 'Registration deadline passed' in response.json()['detail']


def test_registrations_solo_endpoint_respects_string_deadline(client, mock_db):
    """Test that /registrations/solo endpoint respects string deadlines."""
    from bson import ObjectId
    from unittest.mock import AsyncMock
    
    event_id = str(ObjectId())
    past_deadline_str = "2023-01-01T10:00:00"  # Past deadline as string
    
    # Mock event with past deadline as string
    mock_event = {
        '_id': ObjectId(event_id),
        'title': 'Test Event',
        'status': 'open',
        'registration_deadline': past_deadline_str
    }
    
    mock_db.find_one = AsyncMock(return_value=mock_event)
    
    # Mock require_event_published
    with patch('app.routers.registrations.require_event_published', new=AsyncMock()):
        # Mock user
        with patch('app.auth.get_current_user', return_value={'_id': 'user123', 'email': 'test@example.com'}):
            response = client.post('/registrations/solo', json={
                'event_id': event_id
            })
            
            # Should get 400 error for past deadline
            assert response.status_code == 400
            assert 'Registration deadline passed' in response.json()['detail']


def test_registrations_team_endpoint_respects_string_deadline(client, mock_db):
    """Test that /registrations/team endpoint respects string deadlines."""
    from bson import ObjectId
    from unittest.mock import AsyncMock
    
    event_id = str(ObjectId())
    past_deadline_str = "2023-01-01T10:00:00"  # Past deadline as string
    
    # Mock event with past deadline as string
    mock_event = {
        '_id': ObjectId(event_id),
        'title': 'Test Event',
        'status': 'open',
        'registration_deadline': past_deadline_str
    }
    
    mock_db.find_one = AsyncMock(return_value=mock_event)
    
    # Mock require_event_published
    with patch('app.routers.registrations.require_event_published', new=AsyncMock()):
        # Mock user
        with patch('app.auth.get_current_user', return_value={'_id': 'user123', 'email': 'test@example.com'}):
            response = client.post('/registrations/team', json={
                'event_id': event_id,
                'partner_external': {
                    'name': 'John Doe',
                    'email': 'john@example.com'
                },
                'cooking_location': 'creator'
            })
            
            # Should get 400 error for past deadline
            assert response.status_code == 400
            assert 'Registration deadline passed' in response.json()['detail']


def test_endpoints_allow_future_string_deadline(client, mock_db):
    """Test that registration works with future string deadlines."""
    from bson import ObjectId
    from unittest.mock import AsyncMock
    
    event_id = str(ObjectId())
    future_deadline_str = "2099-01-01T10:00:00"  # Future deadline as string
    
    # Mock event with future deadline as string
    mock_event = {
        '_id': ObjectId(event_id),
        'title': 'Test Event',
        'status': 'open',
        'registration_deadline': future_deadline_str,
        'capacity': 100
    }
    
    mock_db.find_one = AsyncMock(return_value=mock_event)
    
    # Mock require_event_published
    with patch('app.routers.registrations.require_event_published', new=AsyncMock()):
        # Mock existing registration check
        with patch('app.routers.registrations.db_mod.db.registrations') as mock_registrations:
            mock_registrations.find_one = AsyncMock(return_value=None)  # No existing registration
            mock_registrations.insert_one = AsyncMock(return_value=type('obj', (object,), {'inserted_id': ObjectId()})())
            
            # Mock _ensure_user
            with patch('app.routers.registrations._ensure_user', new=AsyncMock(return_value={'_id': ObjectId(), 'email': 'test@example.com'})):
                # Mock user
                with patch('app.auth.get_current_user', return_value={'_id': 'user123', 'email': 'test@example.com'}):
                    response = client.post('/registrations/solo', json={
                        'event_id': event_id
                    })
                    
                    # Should succeed with future deadline
                    assert response.status_code == 200