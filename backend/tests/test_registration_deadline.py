import pytest
import datetime
from unittest.mock import patch, AsyncMock
from fastapi import HTTPException

from app.utils import require_event_registration_open, _now_utc


def test_require_event_registration_open_no_deadline():
    """Test that events without deadline pass the check."""
    event = {'_id': 'test', 'title': 'Test Event'}
    # Should not raise
    require_event_registration_open(event)


def test_require_event_registration_open_future_deadline():
    """Test that events with future deadlines pass the check."""
    future_time = _now_utc() + datetime.timedelta(hours=1)
    event = {'_id': 'test', 'title': 'Test Event', 'registration_deadline': future_time}
    # Should not raise
    require_event_registration_open(event)


def test_require_event_registration_open_past_deadline():
    """Test that events with past deadlines fail the check."""
    past_time = _now_utc() - datetime.timedelta(hours=1)
    event = {'_id': 'test', 'title': 'Test Event', 'registration_deadline': past_time}
    
    with pytest.raises(HTTPException) as exc_info:
        require_event_registration_open(event)
    
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == 'Registration deadline passed'


def test_require_event_registration_open_string_deadline():
    """Test that events with string deadlines are properly handled."""
    # Test with past deadline as string - should raise exception
    past_time_str = "2023-01-01T10:00:00"  # A past date as string
    event = {'_id': 'test', 'title': 'Test Event', 'registration_deadline': past_time_str}
    
    with pytest.raises(HTTPException) as exc_info:
        require_event_registration_open(event)
    
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == 'Registration deadline passed'
    
    # Test with future deadline as string - should not raise
    future_time_str = "2099-01-01T10:00:00"  # A future date as string
    future_event = {'_id': 'test', 'title': 'Test Event', 'registration_deadline': future_time_str}
    
    # Should not raise
    require_event_registration_open(future_event)


def test_require_event_registration_open_invalid_string_deadline():
    """Test that events with invalid string deadlines gracefully skip check."""
    # Test with invalid string - should not raise (graceful fallback)
    invalid_time_str = "invalid-date-string"
    event = {'_id': 'test', 'title': 'Test Event', 'registration_deadline': invalid_time_str}
    
    # Should not raise - graceful fallback
    require_event_registration_open(event)


def test_require_event_registration_open_none_deadline():
    """Test that events with None deadline pass the check."""
    event = {'_id': 'test', 'title': 'Test Event', 'registration_deadline': None}
    # Should not raise
    require_event_registration_open(event)


def test_require_event_registration_open_no_event():
    """Test that None event raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        require_event_registration_open(None)
    
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == 'Event not found'