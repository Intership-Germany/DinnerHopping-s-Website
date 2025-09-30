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
from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl, quote
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

def _strip_quotes(s: str | None) -> str | None:
    if not s:
        return s
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        return s[1:-1]
    return s


def _build_uri(base_uri: str, user: str | None, pwd: str | None, auth_source: str | None, db_name: str | None) -> str:
    user = _strip_quotes(user)
    pwd = _strip_quotes(pwd)
    p = urlparse(base_uri)
    # If credentials are already in URI or user not provided, keep as-is
    if (user is None) or ('@' in base_uri):
        return base_uri
    # Ensure db name
    path = p.path if p.path and p.path != '/' else f"/{db_name or 'dinnerhopping'}"
    netloc = f"{quote(user)}:{quote(pwd or '')}@{p.hostname or 'localhost'}"
    if p.port:
        netloc += f":{p.port}"
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    if 'authSource' not in q:
        q['authSource'] = auth_source or (path.lstrip('/') or 'admin')
    return urlunparse((p.scheme or 'mongodb', netloc, path, '', urlencode(q), ''))


def main():
    parser = argparse.ArgumentParser(description='Bootstrap MongoDB schema (no CSV import).')
    parser.add_argument('--mongo-uri', default=os.getenv('MONGO_URI', 'mongodb://localhost:27017/dinnerhopping'))
    parser.add_argument('--skip-seed', action='store_true', help='Do not insert any placeholder seed data')
    args = parser.parse_args()

    base_uri = args.mongo_uri
    user = os.getenv('MONGO_USER')
    pwd = os.getenv('MONGO_PASSWORD')
    auth_source = os.getenv('MONGO_AUTH_SOURCE')
    # derive DB name from URI path
    p = urlparse(base_uri)
    db_name = (p.path or '/').lstrip('/') or None

    mongo_uri = _build_uri(base_uri, user, pwd, auth_source, db_name)
    safe_uri = mongo_uri.replace(pwd or '', '***') if pwd else mongo_uri
    print(f'Connecting to MongoDB at {safe_uri} ...')

    client = None
    for attempt in range(10):
        try:
            client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
            client.admin.command('ping')
            break
        except pymongo_errors.ServerSelectionTimeoutError:
            print(f'Waiting for MongoDB ({attempt+1}/10)...')
            time.sleep(2)
        except pymongo_errors.OperationFailure as exc:
            print(f'Authentication failed while connecting to {safe_uri}: {exc}')
            raise
    if not client:
        print('Failed to connect to MongoDB; aborting bootstrap.')
        return

    db = client.get_default_database()
    ensure_indexes(db)
    print('Indexes ensured.')

    print('Schema bootstrap complete.')


if __name__ == '__main__':
    main()
