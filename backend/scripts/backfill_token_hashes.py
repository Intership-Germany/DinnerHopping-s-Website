#!/usr/bin/env python3
"""Backfill script to compute and store token_hash for existing invitations and email_verifications.

This script is safe to run multiple times. It will:
- connect to MongoDB using MONGO_URI env var
- for invitations: if a document contains a plaintext 'token' field, compute token_hash and $set it, and optionally remove the plaintext token
- for email_verifications: same as above

Usage:
  TOKEN_PEPPER=... MONGO_URI=mongodb://... python3 backend/scripts/backfill_token_hashes.py --dry-run

Note: set TOKEN_PEPPER to the same value the application will use in production.
"""
import os
import argparse
import hashlib
import hmac
from pymongo import MongoClient


def hash_token(token: str, pepper: str | None) -> str:
    p = (pepper or '').encode('utf8')
    return hmac.new(p, token.encode('utf8'), hashlib.sha256).hexdigest()


def main():
    parser = argparse.ArgumentParser(description='Backfill token_hash for invitations and email_verifications')
    parser.add_argument('--dry-run', action='store_true', help='Do not write changes')
    parser.add_argument('--remove-plaintext', action='store_true', help='Remove plaintext token field after backfill')
    args = parser.parse_args()

    mongo_uri = os.getenv('MONGO_URI') or os.getenv('MONGO_URL') or 'mongodb://localhost:27017'
    db_name = os.getenv('MONGO_DB') or os.getenv('MONGO_DATABASE') or 'dinnerhopping'
    pepper = os.getenv('TOKEN_PEPPER', '')

    client = MongoClient(mongo_uri)
    db = client[db_name]

    # Invitations
    inv_coll = db.invitations
    qry = {'token': {'$exists': True}}
    total = inv_coll.count_documents(qry)
    print(f'Found {total} invitation docs with plaintext token')
    if total == 0:
        print('No invitations to backfill')
    else:
        cursor = inv_coll.find(qry)
        for doc in cursor:
            token = doc.get('token')
            if not token:
                continue
            token_hash = hash_token(token, pepper)
            update = {'$set': {'token_hash': token_hash}}
            if args.remove_plaintext:
                update['$unset'] = {'token': ''}
            print(f"Updating invitation {doc.get('_id')} -> token_hash={token_hash[:8]}...")
            if not args.dry_run:
                inv_coll.update_one({'_id': doc['_id']}, update)

    # Email verifications
    ev_coll = db.email_verifications
    qry2 = {'token': {'$exists': True}}
    total2 = ev_coll.count_documents(qry2)
    print(f'Found {total2} email_verification docs with plaintext token')
    if total2 == 0:
        print('No email_verifications to backfill')
    else:
        cursor = ev_coll.find(qry2)
        for doc in cursor:
            token = doc.get('token')
            if not token:
                continue
            token_hash = hash_token(token, pepper)
            update = {'$set': {'token_hash': token_hash}}
            if args.remove_plaintext:
                update['$unset'] = {'token': ''}
            print(f"Updating email_verification {doc.get('_id')} -> token_hash={token_hash[:8]}...")
            if not args.dry_run:
                ev_coll.update_one({'_id': doc['_id']}, update)

    print('Backfill complete')


if __name__ == '__main__':
    main()
