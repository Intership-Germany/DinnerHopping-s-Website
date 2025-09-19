#!/usr/bin/env python3
"""Bootstrap MongoDB schema (collections + indexes) without importing CSV data.

Usage:
  python3 scripts/mongo_init.py --mongo-uri=mongodb://localhost:27017/dinnerhopping

Idempotent: running multiple times will leave existing data untouched and only ensure indexes.
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta
from pymongo import MongoClient, errors as pymongo_errors


def ensure_indexes(db):
    """Create indexes per ER diagram (ignore failures if they already exist)."""
    # USERS
    try:
        db.users.create_index('email', unique=True, sparse=True)
        db.users.create_index('email_verified')
        db.users.create_index('deleted_at')
        db.users.create_index('created_at')
        db.users.create_index('updated_at')
        db.users.create_index('roles')
        db.users.create_index('preferences')
    except Exception:  # noqa: BLE001
        pass

    # EVENTS
    try:
        db.events.create_index('organizer_id')
        db.events.create_index('status')
        db.events.create_index('date')
        db.events.create_index([('location.point', '2dsphere')])
    except Exception:  # noqa: BLE001
        pass

    # REGISTRATIONS
    try:
        db.registrations.create_index('event_id')
        db.registrations.create_index('user_id')
        db.registrations.create_index('status')
        db.registrations.create_index('user_email_snapshot')
    except Exception:  # noqa: BLE001
        pass

    # INVITATIONS
    try:
        db.invitations.create_index('token', unique=True)
        db.invitations.create_index('registration_id')
        db.invitations.create_index('invited_email')
        db.invitations.create_index('expires_at')
    except Exception:  # noqa: BLE001
        pass

    # PAYMENTS
    try:
        db.payments.create_index('registration_id', unique=True)
        db.payments.create_index('provider_payment_id', unique=True, sparse=True)
        db.payments.create_index('idempotency_key')
        db.payments.create_index('status')
    except Exception:  # noqa: BLE001
        pass

    # MATCHES
    try:
        db.matches.create_index('event_id')
        db.matches.create_index('status')
        db.matches.create_index('version')
    except Exception:  # noqa: BLE001
        pass

    # PLANS (legacy / derived from matches)
    try:
        db.plans.create_index('user_email')
        db.plans.create_index('event_id')
    except Exception:  # noqa: BLE001
        pass

def main():
    parser = argparse.ArgumentParser(description='Bootstrap MongoDB schema (no CSV import).')
    parser.add_argument('--mongo-uri', default=os.getenv('MONGO_URI', 'mongodb://localhost:27017/dinnerhopping'))
    parser.add_argument('--skip-seed', action='store_true', help='Do not insert any placeholder seed data')
    args = parser.parse_args()

    mongo_uri = args.mongo_uri
    print(f'Connecting to MongoDB at {mongo_uri} ...')

    client = None
    for attempt in range(10):
        try:
            client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
            client.admin.command('ping')
            break
        except pymongo_errors.ServerSelectionTimeoutError:
            print(f'Waiting for MongoDB ({attempt+1}/10)...')
            time.sleep(2)
    if not client:
        print('Failed to connect to MongoDB; aborting bootstrap.')
        return

    db = client.get_default_database()
    ensure_indexes(db)
    print('Indexes ensured.')

    print('Schema bootstrap complete.')


if __name__ == '__main__':
    main()
