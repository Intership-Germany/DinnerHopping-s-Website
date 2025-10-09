import logging
import json
from pathlib import Path
import requests
from test_create_accounts import PASSWORD

# Variables
BASE_URL = "http://localhost:8000"
LOGIN_ENDPOINT = f"{BASE_URL}/login"
REGISTER_EVENT_ENDPOINT = f"{BASE_URL}/events"
PREDEFINED_USERS_FILE = Path(__file__).parent / "predefined_users.json"
EVENT_ID = "68e376f95c31fde35471dc59"  # Replace with actual event ID
ACCOUNT_COUNT = 20  # Number of accounts to process

# Load predefined users from external JSON file
with open(PREDEFINED_USERS_FILE, "r") as file:
    PREDEFINED_USERS = json.load(file)

# Define helper functions

def login_and_get_token(email, password):
    """Logs in a user and returns the cookies and CSRF token."""
    response = requests.post(LOGIN_ENDPOINT, json={"email": email, "password": password})
    if response.status_code == 200:
        csrf_token = response.cookies.get("csrf_token")
        return response.cookies, csrf_token
    else:
        logging.error(f"Login failed for {email}: {response.status_code}, {response.text}")
        return None, None

def register_to_event(cookies, csrf_token, event_id):
    """Registers the user to an event."""
    payload = {
        # "team_size": 1,
        "preferences": {
            "course_preference": None,
            "kitchen_available": True,
            "main_course_possible": True
        },
        "diet": "omnivore",  # Default diet for testing
        "invited_emails": []  # No invited emails for this test
    }
    headers = {
        "X-CSRF-Token": csrf_token
    }
    url = f"{REGISTER_EVENT_ENDPOINT}/{event_id}/register"
    response = requests.post(url, json=payload, cookies=cookies, headers=headers)
    if response.status_code == 200:
        logging.info(f"Successfully registered to event {event_id}. With the response: {response.json()}")
    else:
        logging.error(f"Failed to register to event {event_id}: {response.status_code}, {response.text}")

def test_register(event_id, account_count, password):
    """Test script to register multiple users to an event."""
    for i, user in enumerate(PREDEFINED_USERS[:account_count]):
        logging.info(f"Processing user {i + 1}: {user['email']}")
        cookies, csrf_token = login_and_get_token(user['email'], password)
        if cookies and csrf_token:
            register_to_event(cookies, csrf_token, event_id)

if __name__ == "__main__":
    test_register(EVENT_ID, ACCOUNT_COUNT, PASSWORD)