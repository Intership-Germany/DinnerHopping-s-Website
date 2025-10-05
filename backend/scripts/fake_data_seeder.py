#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fake data seeder that uses the backend HTTP API to create:
- ~10 realistic events (admin account required)
- register many users from the CSV dataset to events (solo or team)
- simulate some cancellations
- create chat groups and messages for some events

Usage:
    BACKEND_BASE_URL=http://localhost:8000 python backend/scripts/fake_data_seeder.py

Environment:
- BACKEND_BASE_URL (default http://localhost:8000)
- ADMIN_EMAIL (default admin@example.com)
- ADMIN_PASSWORD (default Adminpass1)
- PASSWORD (default for created users: Azertyuiop12!)
- RATE_LIMIT_SECONDS (default 0.2)

Notes:
- The script tries to create an admin user (idempotent) and login to get an admin token to create events.
- It reuses existing helper conventions from other scripts in this repo (same dataset parsing logic).
"""
from __future__ import annotations

import csv
import os
import random
import sys
import time
import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional

import httpx

# Config
DEFAULT_PASSWORD = os.getenv("PASSWORD", "Azertyuiop12!")
BASE_URL = os.getenv("BACKEND_BASE_URL", "https://dinnerhoppings.acrevon.fr/api").rstrip("/")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "info@acrevon.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Azertyuiop12!")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
RATE_LIMIT_SECONDS = float(os.getenv("RATE_LIMIT_SECONDS", "0.25"))
DATASET_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "dataset_for_event.csv"
# fallback to provided attachment location if the dataset above doesn't exist
ATTACHED_SAMPLE = Path("/Users/antonin/Desktop/Stage/Var/data_sample.csv")
AUTO_VERIFY_DB = os.getenv("AUTO_VERIFY_DB", "false").lower() in ("1", "true", "yes")
MONGO_URI = os.getenv("MONGO_URI", os.getenv("MONGO_URI", "mongodb://localhost:27017/dinnerhopping"))

_last_call_ts: float = 0.0

def _rate_limit():
    global _last_call_ts
    now = time.time()
    elapsed = now - _last_call_ts
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_call_ts = time.time()


# Minimal CSV parsing helpers (adapted from register scripts to be robust wrt header names)

def pick(d: Dict, *keys):
    for k in keys:
        if k in d and d[k] is not None and str(d[k]).strip() != "":
            return d[k]
    return ""


def split_name(full_name: str):
    full_name = (full_name or "").strip()
    if not full_name:
        return "", ""
    parts = full_name.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def parse_address(addr: str):
    if not addr:
        return "", "", "", ""
    parts = [p.strip() for p in str(addr).split(",")]
    left = parts[0] if parts else ""
    right = parts[1] if len(parts) > 1 else ""
    # left -> street + number heuristic
    import re
    m = re.match(r"^(.*?)[\s,]+(\d+[A-Za-z]?)\s*$", left)
    if m:
        street, no = m.group(1).strip(), m.group(2).strip()
    else:
        street, no = left.strip(), ""
    # right -> postal + city
    m2 = re.match(r"^\s*(\d{4,5})\s+(.+?)\s*$", right)
    if m2:
        postal, city = m2.group(1).strip(), m2.group(2).strip()
    else:
        postal, city = "", right.strip()
    return street, no, postal, city


# HTTP helpers

def post(path: str, **kwargs):
    _rate_limit()
    url = f"{BASE_URL}{path}"
    with httpx.Client(timeout=30.0, follow_redirects=True) as c:
        return c.post(url, allow_redirects=True, **kwargs)


def get(path: str, **kwargs):
    _rate_limit()
    url = f"{BASE_URL}{path}"
    with httpx.Client(timeout=30.0, follow_redirects=True) as c:
        return c.get(url, allow_redirects=True, **kwargs)


def auth_client(token: str):
    headers = {"Authorization": f"Bearer {token}"}
    return httpx.Client(timeout=30.0, headers=headers, follow_redirects=True)


# API actions

def ensure_admin():
    """Create admin user if missing and return admin token."""
    # If ADMIN_TOKEN was provided via env, prefer it
    if ADMIN_TOKEN:
        print("Using ADMIN_TOKEN from environment")
        return ADMIN_TOKEN

    # Try login first
    try:
        _rate_limit()
        with httpx.Client(timeout=20.0) as c:
            headers = {
            "accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            }
            data = {
            "username": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "payload_form": "",
            "payload": "",
            }
            r = c.post(f"{BASE_URL}/login", headers=headers, data=data)
            if r.status_code == 200:
                return r.json().get("access_token")
            else:
                print("Admin login attempt returned", r.status_code, r.text)
    except Exception:
        pass
    print("Could not obtain admin token. Please ensure an admin user exists with ADMIN_EMAIL/ADMIN_PASSWORD or run this script after creating one.")
    return None


def create_events(admin_token: str, n: int = 10) -> List[dict]:
    """Create n events via admin API. Return list of created events (id + title)."""
    now = dt.datetime.utcnow().date()
    events = []
    if not admin_token:
        print("No admin token; skipping event creation")
        return events

    with auth_client(admin_token) as c:  
        #get admin user id
        _rate_limit()
        r = c.get(f"{BASE_URL}/profile")
        if r.status_code != 200:
            print("Could not fetch admin user info; aborting event creation")
            return events
        admin_user = r.json()
        admin_user_id = admin_user.get("id")
        if not admin_user_id:
            print("Admin user info does not contain id; aborting event creation")
            return events
        
        for i in range(n):
            title = f"Demo Dinner Event {i+1}"
            date = (now + dt.timedelta(days=random.randint(1, 40))).isoformat()
            start_at = (dt.datetime.combine(dt.date.fromisoformat(date), dt.time(19, 0))).isoformat()
            fee_cents = random.choice([0, 0, 500, 1000, 1500])
            capacity = random.choice([20, 30, 40, 0])
            payload = {
                "title": title,
                "description": "A demo DinnerHopping event for testing/presentation.",
                "extra_info": "Additional information about the event.",
                "date": date,
                "start_at": start_at,
                "capacity": capacity,
                "fee_cents": fee_cents,
                "city": "Göttingen",
                "registration_deadline": (dt.datetime.combine(dt.date.fromisoformat(date), dt.time(18, 0))).isoformat(),
                "payment_deadline": (dt.datetime.combine(dt.date.fromisoformat(date), dt.time(18, 0))).isoformat(),
                "valid_zip_codes": ["37000", "74889"],
                "after_party_location": {
                    "address": "Main Street 123, Göttingen",
                    "lat": 51.533,
                    "lon": 9.935,
                },
                "organizer_id": admin_user_id,
                "status": "draft",
                "refund_on_cancellation": True if random.random() < 0.3 else False,
                "chat_enabled": True if random.random() < 0.8 else False,
            }
            _rate_limit()
            r = c.post(f"{BASE_URL}/events/", json=payload)
            if r.status_code in (200, 201):
                ev = r.json()
                events.append(ev)
                print(f"Created event {ev.get('id')} {ev.get('title')}")
            else:
                print("WARN: create event failed", r.status_code)
                try:
                    print("headers:", dict(r.headers))
                    print("body:", r.text[:1000])
                except Exception:
                    pass
    return events


def read_dataset() -> List[Dict]:
    path = DATASET_PATH if DATASET_PATH.exists() else ATTACHED_SAMPLE
    if not path.exists():
        print("Dataset not found at expected locations:", DATASET_PATH, ATTACHED_SAMPLE)
        sys.exit(1)
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            rows.append(r)
    return rows


def register_users_from_rows(rows: List[Dict]):
    """Register users from rows using /register, and attempt auto-verify by invoking /resend-verification (best-effort)."""
    created = 0
    with httpx.Client(timeout=20.0) as c:
        for r in rows:
            for person in (1, 2):
                name = pick(r, f"person{person}_name", f"person{person}_nachname", f"person{person}_full")
                email = pick(r, f"person{person}_email", f"person{person}_mail")
                if not email:
                    continue
                first, last = split_name(name or email.split("@")[0])
                street, no, postal, city = parse_address(pick(r, "adresse", "address"))
                # Ensure required address fields are present (UserCreate requires them)
                if not street:
                    street = "Unknown"
                if not no:
                    no = "1"
                if not postal:
                    postal = "00000"
                if not city:
                    city = "Unknown"
                # Ensure gender is always provided and valid (UserCreate expects Gender)
                gender_val = pick(r, "gender", "sex", "geschlecht")
                if not gender_val:
                    gender_val = random.choice(["female", "male", "diverse", "prefer_not_to_say"])
                payload = {
                    "email": email.strip().lower(),
                    "password": DEFAULT_PASSWORD,
                    "password_confirm": DEFAULT_PASSWORD,
                    "first_name": first or "",
                    "last_name": last or "",
                    "gender": gender_val,
                    "street": street,
                    "street_no": no,
                    "postal_code": postal,
                    "city": city,
                }
                try:
                    _rate_limit()
                    resp = c.post(f"{BASE_URL}/register", json=payload)
                    if resp.status_code in (200, 201, 202):
                        created += 1
                    else:
                        # log non-success to help debugging
                        print("WARN register failed", resp.status_code, resp.text, payload.get('email'))
                except Exception as e:
                    print("WARN register exception", e)
    # Optionally auto-verify created emails by connecting to MongoDB directly
    if AUTO_VERIFY_DB:
        emails = []
        for r in rows:
            e1 = pick(r, "person1_email", "person1_mail")
            e2 = pick(r, "person2_email", "person2_mail")
            if e1:
                emails.append(e1.strip().lower())
            if e2:
                emails.append(e2.strip().lower())
        emails = list(dict.fromkeys([e for e in emails if e]))
        if emails:
            mark_emails_verified_via_db(emails)

def mark_emails_verified_via_db(emails: List[str]):
    """Mark the provided emails as email_verified=True using pymongo. Best-effort."""
    try:
        from pymongo import MongoClient
    except Exception:
        print("AUTO_VERIFY_DB requested but pymongo is not installed; skipping DB verification")
        return
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        dbname = client.get_default_database().name if client.get_default_database() else 'dinnerhopping'
        db = client.get_database(dbname)
        res = db.users.update_many({"email": {"$in": emails}}, {"$set": {"email_verified": True}})
        print(f"AUTO_VERIFY_DB: marked {getattr(res, 'modified_count', '?')} users as verified in DB")
    except Exception as e:
        print("AUTO_VERIFY_DB failed:", e)

def login_get_token(email: str, password: str = DEFAULT_PASSWORD) -> Optional[str]:
    try:
        _rate_limit()
        with httpx.Client(timeout=20.0) as c:
            r = c.post(f"{BASE_URL}/login", json={"username": email, "password": password})
            if r.status_code == 200:
                return r.json().get("access_token")
    except Exception:
        pass
    return None


def register_many_to_events(rows: List[Dict], events: List[dict], prob_team: float = 0.12):
    """For each row, randomly pick an event and attempt to register person1 (and person2 sometimes) to that event.
    Returns list of created registration ids for later operations.
    """
    created_regs = []
    # build a pool of emails
    emails = []
    for r in rows:
        em1 = pick(r, "person1_email", "person1_mail")
        if em1:
            emails.append(em1.strip().lower())
        em2 = pick(r, "person2_email", "person2_mail")
        if em2:
            emails.append(em2.strip().lower())
    emails = list(dict.fromkeys([e for e in emails if e]))

    # Shuffle events to spread registrations
    ev_ids = [e.get('id') for e in events]
    if not ev_ids:
        print("No events to register to; aborting registrations")
        return created_regs

    for r in rows:
        p1 = pick(r, "person1_email", "person1_mail")
        if not p1:
            continue
        event = random.choice(events)
        # login as p1
        token = login_get_token(p1)
        if not token:
            continue
        with auth_client(token) as c:
            # decide team or solo
            if pick(r, "person2_email") and random.random() < prob_team:
                p2 = pick(r, "person2_email")
                payload = {
                    "event_id": event.get('id'),
                    "partner_existing": {"email": p2},
                    "cooking_location": random.choice(["creator", "partner"]),
                    "dietary_preference": None,
                }
                try:
                    _rate_limit()
                    resp = c.post(f"{BASE_URL}/registrations/team", json=payload)
                    if resp.status_code in (200, 201):
                        out = resp.json()
                        created_regs.append(out.get('registration_id'))
                except Exception as e:
                    pass
            else:
                payload = {
                    "event_id": event.get('id'),
                    "dietary_preference": None,
                    "course_preference": None,
                }
                try:
                    _rate_limit()
                    resp = c.post(f"{BASE_URL}/registrations/solo", json=payload)
                    if resp.status_code in (200, 201):
                        created_regs.append(resp.json().get('registration_id') or resp.json().get('registration_ids'))
                except Exception:
                    pass
    print(f"Registrations created: {len(created_regs)}")
    return created_regs


def simulate_cancellations(reg_ids: List[str], fraction: float = 0.08):
    """Randomly cancel some solo registrations (call DELETE /registrations/{id})."""
    to_cancel = [r for r in reg_ids if r]
    k = max(1, int(len(to_cancel) * fraction))
    sample = random.sample(to_cancel, min(k, len(to_cancel)))
    cancelled = 0
    for rid in sample:
        # try to cancel as the registration owner by fetching owner email: we can't easily map reg_id->owner without admin, so attempt to DELETE (will require auth).
        # Instead: perform admin-driven cancellation via admin endpoint if available; this is more reliable here.
        admin_tok = ensure_admin()
        if not admin_tok:
            break
        with auth_client(admin_tok) as c:
            try:
                _rate_limit()
                r = c.delete(f"{BASE_URL}/registrations/{rid}")
                if r.status_code in (200, 201):
                    cancelled += 1
            except Exception:
                pass
    print(f"Cancelled (admin) {cancelled} registrations")


def create_some_chats_and_messages(events: List[dict], rows: List[Dict], max_groups_per_event: int = 2):
    """Create chat groups and post a few messages per group for events that have chat_enabled."""
    admin_tok = ensure_admin()
    if not admin_tok:
        print("No admin token for chat creation; skipping chat seeding")
        return
    # build mapping from email -> token for convenience
    emails = []
    for r in rows:
        em1 = pick(r, "person1_email")
        if em1:
            emails.append(em1.strip().lower())
        em2 = pick(r, "person2_email")
        if em2:
            emails.append(em2.strip().lower())
    emails = list(dict.fromkeys(emails))

    for ev in events:
        if not ev.get('chat_enabled'):
            continue
        groups = random.randint(0, max_groups_per_event)
        for g in range(groups):
            # pick 3-6 participants who are likely registered (best-effort: random emails)
            participants = random.sample(emails, min(len(emails), random.randint(3, 6)))
            creator = participants[0]
            token = login_get_token(creator)
            if not token:
                continue
            with auth_client(token) as c:
                payload = {"event_id": ev.get('id'), "section_ref": f"demo-{g}", "participant_emails": participants}
                try:
                    _rate_limit()
                    resp = c.post(f"{BASE_URL}/chats/groups", json=payload)
                    if resp.status_code in (200, 201):
                        gid = resp.json().get('group_id')
                        # post a few messages
                        for i in range(random.randint(2, 6)):
                            body = random.choice([
                                "Hi everyone! Looking forward to the dinner.",
                                "Can someone bring vegetarian option?",
                                "I'll bring extra plates.",
                                "What's the best arrival time?",
                                "See you there!",
                                "Does anyone have allergies?"
                            ])
                            _rate_limit()
                            mresp = c.post(f"{BASE_URL}/chats/messages", json={"group_id": gid, "body": body})
                except Exception:
                    pass
    print("Chat groups/messages created (best-effort)")


def list_events(admin_token: Optional[str] = None, limit: int = 50) -> List[dict]:
    """Return a list of existing events. If admin_token is provided, use it; otherwise call public GET /events."""
    try:
        if admin_token:
            with auth_client(admin_token) as c:
                _rate_limit()
                r = c.get(f"{BASE_URL}/events", params={"limit": limit})
        else:
            _rate_limit()
            r = get(f"/events/?limit={limit}")
        if r.status_code == 200:
            data = r.json()
            # Expect either {'items':[...]} or list
            if isinstance(data, dict) and data.get('items'):
                return data.get('items')
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def main():
    print("Reading dataset...")
    rows = read_dataset()
    print(f"Dataset rows: {len(rows)}")

    print("Ensuring admin and obtaining token...")
    admin_tok = ensure_admin()
    if not admin_tok:
        print("Admin token unavailable; aborting event creation and some admin-driven operations.")

    # prefer existing events if any
    events = list_events(admin_tok)
    if events:
        print(f"Found {len(events)} existing events; skipping creation.")
    else:
        if admin_tok:
            events = create_events(admin_tok, n=10)

    #print("Registering users from dataset (via /register)")
    #register_users_from_rows(rows)

    print("Registering users to events")
    reg_ids = register_many_to_events(rows, events)

    print("Simulating cancellations")
    simulate_cancellations(reg_ids)

    print("Creating chats and messages")
    create_some_chats_and_messages(events, rows)

    print("Done.")


if __name__ == '__main__':
    main()
