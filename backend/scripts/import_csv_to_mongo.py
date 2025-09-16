#!/usr/bin/env python3
"""
Import script: backend/database.csv -> MongoDB

Usage:
  - Dry run (parse and show counts):
      python3 import_csv_to_mongo.py
  - Run and insert to MongoDB:
      MONGO_URI='mongodb://localhost:27017/dinnerhopping' python3 import_csv_to_mongo.py --apply

Behavior:
  - Creates/updates `users` collection: one document per email (person1_email and person2_email)
  - Creates `registrations` documents with reference to event (if provided) and CSV fields
  - Normalises Oui/Nein -> bool, splits intolerances by comma, trims whitespace
"""
import csv
import os
import argparse
from pymongo import MongoClient, UpdateOne, errors as pymongo_errors
import time
import secrets
from datetime import datetime
from datetime import timedelta

CSV_PATH = os.path.join(os.path.dirname(__file__), '..', 'database.csv')


def parse_bool(v: str) -> bool:
    if v is None:
        return False
    v = v.strip().lower()
    return v in ('ja', 'yes', 'y', 'true', '1')


def split_list_field(v: str):
    if not v:
        return []
    parts = [p.strip() for p in v.split(',') if p.strip()]
    return parts


def normalize_email(e: str):
    if not e:
        return None
    return e.strip().lower()


def load_csv(path):
    rows = []
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def build_user_doc(name, email, address, study_program, food_pref, intolerances):
    return {
        'name': name or None,
        'email': normalize_email(email),
        'address': address or None,
        'study_program': study_program or None,
        'preferences': {
            'food_pref': food_pref or None,
            'intolerances': split_list_field(intolerances) or []
        }
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Write to MongoDB')
    parser.add_argument('--mongo-uri', default=os.getenv('MONGO_URI', 'mongodb://localhost:27017/dinnerhopping'))
    args = parser.parse_args()

    csv_file = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'database.csv'))
    rows = load_csv(csv_file)
    print(f'Loaded {len(rows)} rows from {csv_file}')

    # If no CSV rows and --apply is passed, bootstrap schema/indexes then exit
    if not rows and args.apply:
        print('No CSV rows found; bootstrapping database schema and indexes per ER diagram')

        # connect to mongo
        client = None
        for attempt in range(5):
            try:
                client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=3000)
                client.admin.command('ping')
                break
            except pymongo_errors.ServerSelectionTimeoutError:
                print(f'Waiting for MongoDB ({attempt+1}/5)...')
                time.sleep(1)
        if not client:
            print('Failed to connect to MongoDB to bootstrap schema')
            return

        db = client.get_default_database()

        # Ensure collections exist and create helpful indexes
        try:
            db.users.create_index('email', unique=True)
            db.users.create_index('email_verified')
        except Exception:
            pass

        try:
            db.events.create_index('external_id', unique=True)
            db.events.create_index('date')
        except Exception:
            pass

        try:
            db.registrations.create_index('user_id')
            db.registrations.create_index('event_id')
        except Exception:
            pass

        try:
            db.invitations.create_index('token', unique=True)
            db.invitations.create_index('invited_email')
            db.invitations.create_index('expires_at')
        except Exception:
            pass

        try:
            db.payments.create_index('registration_id', unique=True)
            db.payments.create_index('provider_payment_id', unique=True)
        except Exception:
            pass

        print('Bootstrap complete')
        return

    users_by_email = {}
    registrations = []

    # iterate rows and build users + registrations; keep row index for import_meta
    for idx, r in enumerate(rows, start=1):
        try:
            event_register_id = int(r.get('event_register_id') or 0)
        except (TypeError, ValueError):
            event_register_id = 0

        # person1
        p1_email = normalize_email(r.get('person1_email'))
        p1 = build_user_doc(r.get('person1_name'), p1_email, r.get('adresse'), r.get('studiengang'), r.get('essenspraeferenz'), r.get('unvertraeglichkeiten'))
        if p1_email:
            users_by_email[p1_email] = p1

        # person2 (optional)
        p2_email = normalize_email(r.get('person2_email'))
        if p2_email:
            p2 = build_user_doc(r.get('person2_name'), p2_email, r.get('adresse'), r.get('person2_studiengang') or r.get('studiengang'), r.get('essenspraeferenz'), r.get('unvertraeglichkeiten'))
            users_by_email[p2_email] = p2

        # registration entry(s): create one registration per person email present
        for email_field, name_field, study_field in ((p1_email, r.get('person1_name'), r.get('studiengang')), (p2_email, r.get('person2_name'), r.get('person2_studiengang'))):
            if not email_field:
                continue
            now = datetime.utcnow()
            reg = {
                # snapshot of the user's email at import time
                'user_email_snapshot': email_field,
                # keep original csv event id for mapping to Events
                'event_external_id': event_register_id,
                'event_id': None,  # will be set to ObjectId after inserting events
                'name': name_field or None,
                'address': r.get('adresse') or None,
                'study_program': study_field or None,
                'food_pref': r.get('essenspraeferenz') or None,
                'intolerances': split_list_field(r.get('unvertraeglichkeiten')),
                'course_pref': r.get('gang_wunsch') or None,
                'kitchen_available': parse_bool(r.get('kueche_vorhanden')),
                'main_course_possible': parse_bool(r.get('hauptspeise_moeglich')),
                'status': 'pending',
                'import_meta': {
                    'source': 'csv',
                    'file': os.path.basename(csv_file),
                    'row_number': idx
                },
                'created_at': now,
                'updated_at': now
            }
            registrations.append(reg)

    print(f'Prepared {len(users_by_email)} unique users and {len(registrations)} registrations')

    if not args.apply:
        print('Dry run: not writing to MongoDB. Use --apply to insert.')
        return

    # write to MongoDB with retry/wait for mongo availability
    client = None
    for attempt in range(10):
        try:
            client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=3000)
            # force a call to ensure server is reachable
            client.admin.command('ping')
            break
        except pymongo_errors.ServerSelectionTimeoutError:
            print(f'Waiting for MongoDB ({attempt+1}/10)...')
            time.sleep(2)
    if not client:
        print('Failed to connect to MongoDB, aborting')
        return

    db = client.get_default_database()

    # if the database already contains data, do not import
    try:
        if (db.users.count_documents({}) > 0) or (db.events.count_documents({}) > 0) or (db.registrations.count_documents({}) > 0):
            print('Database not empty — skipping import to avoid duplicates')
            return
    except Exception:
        # in case collections don't exist yet, continue
        pass

    # create events collection entries for each unique csv event id
    unique_event_ids = set()
    for r in rows:
        try:
            event_register_id = int(r.get('event_register_id') or 0)
        except (TypeError, ValueError):
            event_register_id = 0
        if event_register_id:
            unique_event_ids.add(event_register_id)

    event_docs = []
    now = datetime.utcnow()
    for eid in sorted(unique_event_ids):
        # minimal location: put CSV address into public address; encryption/geocoding is left as a follow-up
        event_docs.append({
            'external_id': eid,
            'title': f'Event {eid}',
            'source': 'csv_import',
            'location': {
                'address_encrypted': None,
                'address_public': None,
                'point': None
            },
            'capacity': None,
            'attendee_count': 0,
            'status': 'published',
            'created_at': now,
            'updated_at': now
        })

    if event_docs:
        try:
            res = db.events.insert_many(event_docs, ordered=False)
            print(f'Inserted {len(res.inserted_ids)} events')
        except pymongo_errors.BulkWriteError as bwe:
            print('Warning: bulk write error while inserting events (some may already exist):', bwe.details)
        except pymongo_errors.PyMongoError as e:
            print('Warning: could not insert events (pymongo error):', e)

    # Upsert users by email
    ops = []
    for email, u in users_by_email.items():
        if not email:
            continue
        q = {'email': email}
        update = {'$setOnInsert': {'email': email}, '$set': {k: v for k, v in u.items() if k != 'email'}}
        ops.append(UpdateOne(q, update, upsert=True))

    if ops:
        res = db.users.bulk_write(ops)
        print('Users bulk_write result:', res.bulk_api_result)

    # create helpful indexes for invitations/payments to support workflow
    try:
        db.invitations.create_index('token', unique=True)
        db.invitations.create_index('invited_email')
    except Exception:
        pass
    try:
        db.payments.create_index('registration_id', unique=True)
    except Exception:
        pass

    # map emails -> user_id (ObjectId) so registrations can reference users
    email_map = {}
    try:
        cursor = db.users.find({'email': {'$in': list(users_by_email.keys())}}, {'email': 1})
        for doc in cursor:
            email_map[doc.get('email')] = doc.get('_id')
    except Exception:
        # fallback: leave registrations without user_id
        email_map = {}

    # attach user_id where possible
    for reg in registrations:
        uid = email_map.get(reg.get('user_email') or reg.get('user_email_snapshot'))
        if uid:
            reg['user_id'] = uid

    # build map external event id -> ObjectId
    event_external_map = {}
    try:
        cursor = db.events.find({'external_id': {'$in': [r.get('event_external_id') for r in registrations if r.get('event_external_id')]}}, {'external_id': 1})
        for doc in cursor:
            event_external_map[doc.get('external_id')] = doc.get('_id')
    except Exception:
        event_external_map = {}

    for reg in registrations:
        ext = reg.get('event_external_id')
        if ext and ext in event_external_map:
            reg['event_id'] = event_external_map[ext]

    # Insert registrations
    inserted_reg_ids = []
    if registrations:
        res = db.registrations.insert_many(registrations)
        inserted_reg_ids = res.inserted_ids
        print(f'Inserted {len(res.inserted_ids)} registrations')

    # Create invitations for registrations without linked user (guest invites)
    invitation_docs = []
    now = datetime.utcnow()
    for idx, reg in enumerate(registrations):
        reg_id = inserted_reg_ids[idx] if idx < len(inserted_reg_ids) else None
        if not reg.get('user_id') and reg_id is not None:
            token = secrets.token_urlsafe(16)
            invitation_docs.append({
                'registration_id': reg_id,
                'invited_email': reg.get('user_email_snapshot'),
                'token': token,
                'status': 'pending',
                'created_at': now,
                'expires_at': now + timedelta(days=30)
            })

    invitation_ids = []
    if invitation_docs:
        res = db.invitations.insert_many(invitation_docs)
        invitation_ids = res.inserted_ids
        print(f'Created {len(invitation_ids)} invitations')

        # update registrations with invitation_id
        for i, inv_id in enumerate(invitation_ids):
            reg_id = invitation_docs[i]['registration_id']
            try:
                db.registrations.update_one({'_id': reg_id}, {'$set': {'invitation_id': inv_id}})
            except Exception:
                pass

    # Create payment records for each registration and link them
    payment_docs = []
    for idx, reg in enumerate(registrations):
        reg_id = inserted_reg_ids[idx] if idx < len(inserted_reg_ids) else None
        if reg_id is None:
            continue
        payment_docs.append({
            'registration_id': reg_id,
            'provider': None,
            'provider_payment_id': None,
            'idempotency_key': None,
            'amount': 0,
            'currency': 'EUR',
            'status': 'created',
            'paid_at': None,
            'meta': None,
            'created_at': now,
        })

    if payment_docs:
        res = db.payments.insert_many(payment_docs)
        payment_ids = res.inserted_ids
        print(f'Created {len(payment_ids)} payment records')
        # update registrations with payment_id
        for i, pay_id in enumerate(payment_ids):
            reg_id = payment_docs[i]['registration_id']
            try:
                db.registrations.update_one({'_id': reg_id}, {'$set': {'payment_id': pay_id}})
            except Exception:
                pass

    print('Import complete')


if __name__ == '__main__':
    main()
