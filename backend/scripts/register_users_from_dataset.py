#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Register users from the CSV dataset via the backend API, at max 1 request/second.

- Reads backend/app/data/dataset_for_event.csv
- Creates user accounts for person1 and (if present) person2 per row
- Fills all available fields for /register
- Optionally auto-verifies accounts by parsing backend logs for verification links
  (works if LOG_TO_FILES=true on the backend, writing logs/root/YYYY-MM-DD.log).

Environment variables / CLI args:
- BACKEND_BASE_URL: default http://localhost:8000
- LOGS_ROOT_FILE: override path to root log file if needed
- RATE_LIMIT_SECONDS: default 1.0 (min seconds between ANY API call)
- PASSWORD: override default password (default "Azertyuiop12!")

Usage (from repo root or backend/):
    python backend/scripts/register_users_from_dataset.py
"""
from __future__ import annotations

import csv
import os
import re
import sys
import time
import datetime as dt
from pathlib import Path
from typing import Dict, Optional, Tuple

import httpx

DEFAULT_PASSWORD = os.getenv("PASSWORD", "Azertyuiop12!")
BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
DATASET_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "dataset_for_event.csv"
# Attempt to detect backend logs root/DATE.log (default location used by logging_config.py when LOG_TO_FILES=true)
LOGS_DIR = Path(os.getenv("LOG_DIR", Path(__file__).resolve().parents[1] / "logs"))
ROOT_LOG_FILE = os.getenv("LOGS_ROOT_FILE")  # if provided, use exact file
RATE_LIMIT_SECONDS = float(os.getenv("RATE_LIMIT_SECONDS", "1.0"))

# Simple global rate limiter (ensures >= RATE_LIMIT_SECONDS between ANY API calls)
_last_call_ts: float = 0.0

def _rate_limit():
    global _last_call_ts
    now = time.time()
    elapsed = now - _last_call_ts
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_call_ts = time.time()


def split_name(full_name: str) -> Tuple[str, str]:
    full_name = (full_name or "").strip()
    if not full_name:
        return ("", "")
    parts = full_name.split()
    if len(parts) == 1:
        return (parts[0], "")
    return (parts[0], " ".join(parts[1:]))


def map_gender(g: str) -> str:
    g = (g or "").strip().lower()
    if g in {"w", "f", "female", "femme"}:
        return "female"
    if g in {"m", "male", "homme"}:
        return "male"
    if g in {"d", "diverse", "divers", "non-binary", "non binaire"}:
        return "diverse"
    return "prefer_not_to_say"


_addr_left_re = re.compile(r"^(.*?)[\s,]+(\d+[A-Za-z]?)\s*$")
_post_city_re = re.compile(r"^\s*(\d{4,5})\s+(.+?)\s*$")

def parse_address(addr: str) -> Tuple[str, str, str, str]:
    """Return (street, street_no, postal_code, city). Best-effort parser."""
    street = street_no = postal = city = ""
    if not addr:
        return street, street_no, postal, city
    parts = [p.strip() for p in str(addr).split(",")]
    left = parts[0] if parts else ""
    right = parts[1] if len(parts) > 1 else ""
    m = _addr_left_re.match(left)
    if m:
        street, street_no = m.group(1).strip(), m.group(2).strip()
    else:
        street = left.strip()
        street_no = ""
    m2 = _post_city_re.match(right)
    if m2:
        postal, city = m2.group(1).strip(), m2.group(2).strip()
    else:
        # try to salvage if city-only
        city = right.strip()
        postal = ""
    return street, street_no, postal, city


def map_food_pref(val: str) -> str:
    v = (val or "").strip().lower()
    if v.startswith("veg") and "vegan" in v:
        return "vegan"
    if v.startswith("veg") or v in {"vegetarisch", "vegetarian"}:
        return "vegetarian"
    if v in {"vegan"}:
        return "vegan"
    # Alles / omnivore
    return "omnivore"


def map_course(val: str) -> Optional[str]:
    v = (val or "").strip().lower()
    if v.startswith("vor") or "appetizer" in v or "entr" in v:
        return "appetizer"
    if v.startswith("haupt") or v == "main" or "principal" in v:
        return "main"
    if v.startswith("dess"):
        return "dessert"
    # Egal or unknown
    return None


def map_bool(val: str) -> Optional[bool]:
    v = (val or "").strip().lower()
    if v in {"ja", "yes", "true", "1", "y", "oui"}:
        return True
    if v in {"nein", "no", "false", "0", "n", "non"}:
        return False
    return None


def find_root_logfile() -> Optional[Path]:
    if ROOT_LOG_FILE:
        p = Path(ROOT_LOG_FILE)
        return p if p.exists() else None
    today = dt.datetime.utcnow().strftime("%Y-%m-%d")
    candidate = LOGS_DIR / "root" / f"{today}.log"
    return candidate if candidate.exists() else None


def extract_token_from_text(txt: str) -> Optional[str]:
    # find .../verify-email?token=XXXXX (token may contain URL-safe chars)
    m = re.search(r"/verify-email\?token=([A-Za-z0-9_\-\.~]+)", txt)
    if m:
        return m.group(1)
    return None


def try_auto_verify(email: str) -> bool:
    """Attempt to auto-verify by triggering resend and parsing the backend root log file.

    Returns True if verification call succeeded, False otherwise.
    """
    log_path = find_root_logfile()
    try:
        # mark current size to only parse new content
        start_size = log_path.stat().st_size if log_path else 0
    except Exception:
        start_size = 0

    # 1) request resend (will generate a new token and print link in dev)
    try:
        _rate_limit()
        with httpx.Client(timeout=15.0) as client:
            res = client.post(f"{BASE_URL}/resend-verification", json={"email": email})
            _ = res.json()
    except Exception:
        pass

    # 2) small wait to allow backend to write log
    time.sleep(0.3)

    # 3) read logs and extract latest token
    token = None
    try:
        log_path = find_root_logfile()
        if log_path and log_path.exists():
            with log_path.open("r", encoding="utf-8", errors="ignore") as fh:
                fh.seek(start_size)
                tail = fh.read()
                # Filter block for this email (best-effort)
                if email in tail and "/verify-email?token=" in tail:
                    token = extract_token_from_text(tail)
                elif "/verify-email?token=" in tail:
                    token = extract_token_from_text(tail)
    except Exception:
        token = None

    if not token:
        return False

    # 4) call verify endpoint
    try:
        _rate_limit()
        with httpx.Client(timeout=15.0) as client:
            vres = client.get(f"{BASE_URL}/verify-email", params={"token": token})
            if vres.status_code == 200:
                return True
    except Exception:
        return False
    return False


def register_user(client: httpx.Client, payload: Dict) -> Tuple[bool, Optional[str]]:
    try:
        _rate_limit()
        r = client.post(f"{BASE_URL}/register", json=payload)
        if r.status_code == 201:
            data = r.json()
            return True, data.get("id")
        else:
            # if already registered -> treat as success
            try:
                msg = r.json().get("detail")
            except Exception:
                msg = r.text
            if r.status_code == 400 and isinstance(msg, str) and "already" in msg.lower():
                return True, None
            print(f"WARN: register failed {r.status_code} {msg}")
            return False, None
    except Exception as e:
        print(f"ERROR: register exception: {e}")
        return False, None


def build_user_payload(email: str, name: str, gender: str, address: str, study: str, food_pref: str, allergies: str, desired_course: str, kitchen_available: str, main_course_possible: str) -> Dict:
    first, last = split_name(name)
    street, street_no, postal, city = parse_address(address)
    gender_api = map_gender(gender)
    prefs = {
        "field_of_study": (study or "").strip() or None,
        "food_preference_raw": (food_pref or "").strip() or None,
        "allergies": (allergies or "").strip() or None,
        "desired_course_raw": (desired_course or "").strip() or None,
        "kitchen_available_raw": (kitchen_available or "").strip() or None,
        "main_course_possible_raw": (main_course_possible or "").strip() or None,
    }
    payload = {
        "email": (email or "").strip().lower(),
        "password": DEFAULT_PASSWORD,
        "password_confirm": DEFAULT_PASSWORD,
        "first_name": first,
        "last_name": last,
        "street": street,
        "street_no": street_no,
        "postal_code": postal,
        "city": city,
        "gender": gender_api,
        # pass raw extras in preferences so we don't lose data
        "preferences": {k: v for k, v in prefs.items() if v},
    }
    return payload


def main():
    if not DATASET_PATH.exists():
        print(f"Dataset not found at {DATASET_PATH}")
        sys.exit(1)

    total_created = 0
    total_verified = 0
    total_rows = 0

    with httpx.Client(timeout=20.0) as client:
        with DATASET_PATH.open("r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                total_rows += 1
                # person1
                p1_email = (row.get("person1_email") or "").strip()
                if p1_email:
                    p1_payload = build_user_payload(
                        p1_email,
                        row.get("person1_name") or "",
                        row.get("person1_gender") or "",
                        row.get("address") or "",
                        row.get("person1_study_program") or "",
                        row.get("food_preference") or "",
                        row.get("allergies") or "",
                        row.get("desired_course") or "",
                        row.get("kitchen_available") or "",
                        row.get("main_course_pssible") or row.get("main_course_possible") or "",
                    )
                    ok, _id = register_user(client, p1_payload)
                    if ok:
                        total_created += 1
                        if try_auto_verify(p1_payload["email"]):
                            total_verified += 1
                # person2 (optional)
                p2_email = (row.get("person2_email") or "").strip()
                if p2_email:
                    p2_payload = build_user_payload(
                        p2_email,
                        row.get("person2_name") or "",
                        row.get("person2_gender") or "",
                        row.get("address") or "",
                        row.get("person2_study_program") or "",
                        row.get("food_preference") or "",
                        row.get("allergies") or "",
                        row.get("desired_course") or "",
                        row.get("kitchen_available") or "",
                        row.get("main_course_pssible") or row.get("main_course_possible") or "",
                    )
                    ok, _id = register_user(client, p2_payload)
                    if ok:
                        total_created += 1
                        if try_auto_verify(p2_payload["email"]):
                            total_verified += 1

    print(f"Rows processed: {total_rows}")
    print(f"Users created/exists: {total_created}")
    print(f"Users verified (auto): {total_verified}")


if __name__ == "__main__":
    main()

