# Script to create multiple user accounts for testing purposes
# This script generates user data by cycling through a predefined list of names
# and sends POST requests to the backend API to create accounts.
# WARNING: All accounts will have the same password and their email addresses will not be verified.

import requests
import random
import string
import logging
from itertools import cycle
import json
from pathlib import Path

# Backend API URL
BASE_URL = "http://localhost:8000"
CREATE_USER_ENDPOINT = f"{BASE_URL}/register"

# Fixed password
PASSWORD = "Azertyuiop12!"
# Number of accounts to create
ACCOUNT_COUNT = 20

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load predefined users from external JSON file
PREDEFINED_USERS_FILE = Path(__file__).parent / "predefined_users.json"
with open(PREDEFINED_USERS_FILE, "r", encoding='utf-8') as file:
    PREDEFINED_USERS = json.load(file)

# Create a cycle iterator for predefined users
user_cycle = cycle(PREDEFINED_USERS)

# Generate user data by cycling through the predefined list
def generate_random_user():
    user = next(user_cycle)
    username = f"{user['first_name'].lower()}{user['last_name'].lower()}"
    email = f"{user['email']}"
    address = {
        "street": f"{user['street']}",
        "street_no": f"{user['street_no']}",
        "postal_code": f"37{random.randint(100, 999)}",
        "city": "Göttingen"
    }
    return {
        "username": username,  # Added username to the returned dictionary
        "email": email,
        "password": PASSWORD,
        "password_confirm": PASSWORD,
        "first_name": user['first_name'],
        "last_name": user['last_name'],
        "gender": "male",  # Default gender for testing
        "phone_number": f"+491512345678{random.randint(0, 9)}",
        **address,
        # "lat": random.uniform(-90, 90),
        # "lon": random.uniform(-180, 180),
        "allergies": []
    }

# Create accounts
def create_accounts(count):
    for i in range(count):
        user_data = generate_random_user()
        try:
            # logging.info(f"Attempting to create account {i + 1} with data: {user_data}")
            response = requests.post(CREATE_USER_ENDPOINT, json=user_data)
            if response.status_code == 201:
                logging.info(f"Account {i + 1} created successfully: {user_data['username']}.")
            else:
                logging.error(f"Failed to create account {i + 1}: {response.status_code}, {response.text}")
        except Exception as e:
            logging.exception(f"Error creating account {i + 1}: {e}")

if __name__ == "__main__":
    create_accounts(ACCOUNT_COUNT)