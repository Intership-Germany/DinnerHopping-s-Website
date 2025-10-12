#!/usr/bin/env python3
"""Cleanup provisional invited users older than TTL.

Deletes users where invited=True, password_hash is None and invited_at older than INVITED_TTL_DAYS.
"""
import os
from datetime import datetime, timedelta
from pymongo import MongoClient

MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/dinnerhopping')
DB_NAME = os.getenv('MONGO_DB', None)
TTL_DAYS = int(os.getenv('INVITED_TTL_DAYS', '7'))

if __name__ == '__main__':
    client = MongoClient(MONGO_URI)
    db = client.get_default_database() if DB_NAME is None else client[DB_NAME]
    cutoff = datetime.utcnow() - timedelta(days=TTL_DAYS)
    query = {'invited': True, 'password_hash': None, 'invited_at': {'$lt': cutoff}}
    res = db.users.delete_many(query)
    print(f"Deleted {res.deleted_count} provisional invited users older than {TTL_DAYS} days")
