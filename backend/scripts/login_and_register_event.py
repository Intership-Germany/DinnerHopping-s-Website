#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Login users from dataset and register them to the target event (solo or team),
obeying a global 1 request/second pacing to avoid rate limits/bans.

- Reads backend/app/data/dataset_for_event.csv
- For each row, logs in person1 (and person2 if present)
- Sets optional profile fields (kitchen_available, main_course_possible, default_dietary_preference, field_of_study)
- Registers solo or as a team to the given event id
- Uses Authorization: Bearer token so CSRF middleware doesn't apply
- Auto-verifies accounts if necessary by parsing backend logs for the verification link

Env:
- BACKEND_BASE_URL: default http://localhost:8000
- EVENT_ID: default "68d401896e923fec74e0b57b"
- RATE_LIMIT_SECONDS: default 1.0
- PASSWORD: default "Azertyuiop12!"
- LOG_DIR / LOGS_ROOT_FILE for verification log parsing (see register script)

Usage:
    python backend/scripts/login_and_register_event.py
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

# ---------- Config ----------
DEFAULT_PASSWORD = os.getenv("PASSWORD", "Azertyuiop12!")
BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
EVENT_ID = os.getenv("EVENT_ID", "68d401896e923fec74e0b57b")
DATASET_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "dataset_for_event.csv"
LOGS_DIR = Path(os.getenv("LOG_DIR", Path(__file__).resolve().parents[1] / "logs"))
ROOT_LOG_FILE = os.getenv("LOGS_ROOT_FILE")
RATE_LIMIT_SECONDS = float(os.getenv("RATE_LIMIT_SECONDS", "1.0"))

_last_call_ts: float = 0.0

def _rate_limit():
    global _last_call_ts
    now = time.time()
    elapsed = now - _last_call_ts
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_call_ts = time.time()

# ---------- CSV helpers ----------

def map_gender(g: str) -> str:
    g = (g or "").strip().lower()
    if g in {"w", "f", "female", "femme"}:
        return "female"
    if g in {"m", "male", "homme"}:
        return "male"
    if g in {"d", "diverse", "divers", "non-binary", "non binaire"}:
        return "diverse"
    return "prefer_not_to_say"


def map_food_pref(val: str) -> str:
    v = (val or "").strip().lower()
    if v.startswith("veg") and "vegan" in v:
        return "vegan"
    if v.startswith("veg") or v in {"vegetarisch", "vegetarian"}:
        return "vegetarian"
    if v in {"vegan"}:
        return "vegan"
    return "omnivore"


def map_course(val: str) -> Optional[str]:
    v = (val or "").strip().lower()
    if v.startswith("vor") or "appetizer" in v or "entr" in v:
        return "appetizer"
    if v.startswith("haupt") or v == "main" or "principal" in v:
        return "main"
    if v.startswith("dess"):
        return "dessert"
    return None


def map_bool(val: str) -> Optional[bool]:
    v = (val or "").strip().lower()
    if v in {"ja", "yes", "true", "1", "y", "oui"}:
        return True
    if v in {"nein", "no", "false", "0", "n", "non"}:
        return False
    return None


def ensure_valid_solo_course(course: Optional[str], main_possible: Optional[bool]) -> Optional[str]:
    """Solo endpoint forbids course=='main' when main_course_possible is False; drop course in that case."""
    if course == "main" and (main_possible is False):
        return None
    return course

# ---------- Email verification helpers (dev log scan) ----------

def find_root_logfile() -> Optional[Path]:
    if ROOT_LOG_FILE:
        p = Path(ROOT_LOG_FILE)
        return p if p.exists() else None
    today = dt.datetime.utcnow().strftime("%Y-%m-%d")
    candidate = LOGS_DIR / "root" / f"{today}.log"
    return candidate if candidate.exists() else None


def extract_token_from_text(txt: str) -> Optional[str]:
    m = re.search(r"/verify-email\?token=([A-Za-z0-9_\-\.~]+)", txt)
    if m:
        return m.group(1)
    return None


def try_auto_verify(email: str) -> bool:
    log_path = find_root_logfile()
    try:
        start_size = log_path.stat().st_size if log_path else 0
    except Exception:
        start_size = 0
    # trigger resend
    try:
        _rate_limit()
        with httpx.Client(timeout=15.0) as c:
            c.post(f"{BASE_URL}/resend-verification", json={"email": email})
    except Exception:
        pass
    time.sleep(0.3)
    token = None
    try:
        log_path = find_root_logfile()
        if log_path and log_path.exists():
            with log_path.open("r", encoding="utf-8", errors="ignore") as fh:
                fh.seek(start_size)
                tail = fh.read()
                if email in tail and "/verify-email?token=" in tail:
                    token = extract_token_from_text(tail)
                elif "/verify-email?token=" in tail:
                    token = extract_token_from_text(tail)
    except Exception:
        token = None
    if not token:
        return False
    try:
        _rate_limit()
        with httpx.Client(timeout=15.0) as c:
            vres = c.get(f"{BASE_URL}/verify-email", params={"token": token})
            return vres.status_code == 200
    except Exception:
        return False

# ---------- API helpers ----------

def api_login(email: str, password: str = DEFAULT_PASSWORD) -> Optional[str]:
    """Return access_token or None."""
    try:
        _rate_limit()
        with httpx.Client(timeout=20.0) as c:
            r = c.post(f"{BASE_URL}/login", json={"username": email, "password": password})
            if r.status_code == 200:
                return r.json().get("access_token")
            # If unverified, try auto-verify then retry once
            try:
                detail = r.json().get("detail")
            except Exception:
                detail = r.text
            if r.status_code == 401 and isinstance(detail, str) and "verify" in detail.lower():
                if try_auto_verify(email):
                    _rate_limit()
                    r2 = c.post(f"{BASE_URL}/login", json={"username": email, "password": password})
                    if r2.status_code == 200:
                        return r2.json().get("access_token")
    except Exception as e:
        print(f"ERROR: login {email}: {e}")
    return None


def api_patch_optional_profile(token: str, payload: Dict) -> bool:
    try:
        _rate_limit()
        with httpx.Client(timeout=20.0, headers={"Authorization": f"Bearer {token}"}) as c:
            r = c.patch(f"{BASE_URL}/profile/optional", json=payload)
            return r.status_code in (200, 201)
    except Exception as e:
        print(f"WARN: optional profile update failed: {e}")
        return False


def api_put_profile_preferences(token: str, preferences: Dict) -> bool:
    if not preferences:
        return True
    try:
        _rate_limit()
        with httpx.Client(timeout=20.0, headers={"Authorization": f"Bearer {token}"}) as c:
            r = c.put(f"{BASE_URL}/profile", json={"preferences": preferences})
            return r.status_code in (200, 201)
    except Exception as e:
        print(f"WARN: profile preferences update failed: {e}")
        return False


def api_register_solo(token: str, payload: Dict) -> bool:
    try:
        _rate_limit()
        with httpx.Client(timeout=30.0, headers={"Authorization": f"Bearer {token}"}) as c:
            r = c.post(f"{BASE_URL}/registrations/solo", json=payload)
            if r.status_code in (200, 201):
                return True
            try:
                print(f"WARN: solo reg failed {r.status_code} {r.json()}")
            except Exception:
                print(f"WARN: solo reg failed {r.status_code} {r.text}")
            return False
    except Exception as e:
        print(f"ERROR: solo registration exception: {e}")
        return False


def api_register_team(token: str, payload: Dict) -> bool:
    try:
        _rate_limit()
        with httpx.Client(timeout=30.0, headers={"Authorization": f"Bearer {token}"}) as c:
            r = c.post(f"{BASE_URL}/registrations/team", json=payload)
            if r.status_code in (200, 201):
                return True
            try:
                print(f"WARN: team reg failed {r.status_code} {r.json()}")
            except Exception:
                print(f"WARN: team reg failed {r.status_code} {r.text}")
            return False
    except Exception as e:
        print(f"ERROR: team registration exception: {e}")
        return False

# ---------- Main flow ----------

def main():
    if not DATASET_PATH.exists():
        print(f"Dataset not found at {DATASET_PATH}")
        sys.exit(1)

    processed = 0
    solo_ok = 0
    team_ok = 0

    # Pre-scan to build set of all creator emails (person1) to reduce team conflicts
    creator_emails = set()
    with DATASET_PATH.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            em = (row.get("person1_email") or "").strip().lower()
            if em:
                creator_emails.add(em)

    with DATASET_PATH.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            processed += 1
            p1_email = (row.get("person1_email") or "").strip().lower()
            p2_email = (row.get("person2_email") or "").strip().lower()

            if not p1_email:
                continue

            # Map dataset fields
            food_pref = map_food_pref(row.get("food_preference") or "")
            course = map_course(row.get("desired_course") or "")
            kitchen = map_bool(row.get("kitchen_available") or "")
            main_possible_raw = row.get("main_course_pssible") or row.get("main_course_possible") or ""
            main_possible = map_bool(main_possible_raw)
            allergies = (row.get("allergies") or "").strip()
            study1 = (row.get("person1_study_program") or "").strip()
            study2 = (row.get("person2_study_program") or "").strip()

            # -------- Creator (person1) login --------
            token1 = api_login(p1_email)
            if not token1:
                print(f"WARN: cannot login {p1_email}, skipping row")
                continue
            # Update optional profile for person1
            api_patch_optional_profile(token1, {
                "kitchen_available": bool(kitchen) if kitchen is not None else None,
                "main_course_possible": bool(main_possible) if main_possible is not None else None,
                "default_dietary_preference": food_pref,
                "field_of_study": study1 or None,
            })
            # Preserve allergies in preferences (if any)
            prefs_extra = {"allergies": allergies} if allergies else {}
            api_put_profile_preferences(token1, prefs_extra)

            did_team = False
            if p2_email:
                # Avoid trying team if partner is also a creator elsewhere (likely to cause duplicate registration conflicts)
                partner_is_creator = p2_email in creator_emails

                token2 = None
                if not partner_is_creator:
                    # Try to login partner to enrich optional profile (not strictly required for team endpoint)
                    token2 = api_login(p2_email)
                    if token2:
                        food_pref2 = food_pref  # reuse shared field from CSV
                        api_patch_optional_profile(token2, {
                            "default_dietary_preference": food_pref2,
                            "field_of_study": study2 or None,
                        })
                        if allergies:
                            api_put_profile_preferences(token2, {"allergies": allergies})

                    # Decide cooking location
                    cooking_loc = "creator"
                    if kitchen is False:
                        cooking_loc = "partner"
                    if course == "main" and (main_possible is False):
                        cooking_loc = "partner"

                    team_payload = {
                        "event_id": EVENT_ID,
                        "partner_existing": {"email": p2_email},
                        "cooking_location": cooking_loc,
                        # creator overrides
                        "dietary_preference": food_pref,
                        "kitchen_available": bool(kitchen) if kitchen is not None else None,
                        "main_course_possible": bool(main_possible) if main_possible is not None else None,
                        "course_preference": course,
                    }
                    if api_register_team(token1, team_payload):
                        team_ok += 1
                        did_team = True
                    else:
                        # Likely duplicate registration or constraints -> fall back to solo to ensure creator is registered
                        solo_payload = {
                            "event_id": EVENT_ID,
                            "dietary_preference": food_pref,
                            "kitchen_available": bool(kitchen) if kitchen is not None else None,
                            "main_course_possible": bool(main_possible) if main_possible is not None else None,
                            "course_preference": course,
                        }
                        if api_register_solo(token1, solo_payload):
                            solo_ok += 1
                # else: partner is also a creator; skip team to avoid duplicate conflicts
                if partner_is_creator:
                    # Register creator solo to ensure participation (idempotent on server)
                    solo_payload = {
                        "event_id": EVENT_ID,
                        "dietary_preference": food_pref,
                        "kitchen_available": bool(kitchen) if kitchen is not None else None,
                        "main_course_possible": bool(main_possible) if main_possible is not None else None,
                        "course_preference": ensure_valid_solo_course(course, main_possible),
                    }
                    if api_register_solo(token1, solo_payload):
                        solo_ok += 1
                    else:
                        print(f"WARN: solo registration failed for creator {p1_email} (partner is a creator)")

            if not did_team and not p2_email:
                # Solo registration (no partner in row)
                solo_payload = {
                    "event_id": EVENT_ID,
                    "dietary_preference": food_pref,
                    "kitchen_available": bool(kitchen) if kitchen is not None else None,
                    "main_course_possible": bool(main_possible) if main_possible is not None else None,
                    "course_preference": ensure_valid_solo_course(course, main_possible),
                }
                if api_register_solo(token1, solo_payload):
                    solo_ok += 1

    print(f"Processed rows: {processed}")
    print(f"Solo registrations OK: {solo_ok}")
    print(f"Team registrations OK: {team_ok}")


if __name__ == "__main__":
    main()
