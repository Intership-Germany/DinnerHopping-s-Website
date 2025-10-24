#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Register all users from a JSON file via the public API, then enroll them
into an event as teams or solo participants, respecting API rate limit.

Inputs:
  --event-id <ObjectId>          Target event (required)
  --users-json <path>            Path to users JSON (default: ~/Downloads/dinnerhopping.users.json)
  --teams-tsv <path>             Optional TSV (tab-separated) with columns:
                                 person1_name\tperson2_name\tperson1_email\tperson2_email
                                 If omitted, an embedded list (TEAM_TSV_DEFAULT) is used.

Environment variables:
  BACKEND_BASE_URL (default: http://localhost:8000)
  PASSWORD          (default: Azertyuiop12!)
  RATE_LIMIT_SECONDS(default: 0.25)  # 4 req/s
  LOG_DIR           (default: backend/logs)
  LOGS_ROOT_FILE    (override path to root log file)

Notes:
- Email verification is required for login; this script attempts an automatic
  verification by reading the backend root log (dev mode) after requesting a
  resend of the verification email.
- Cooking location is set to 'creator' by default, course_preference omitted
  unless you adapt below.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

# ---------- Defaults ----------
# Par défaut, utilise le même hôte que le script d'import existant du repo
BASE_URL = os.getenv('BACKEND_BASE_URL', 'http://dinnerhoppings.acrevon.fr/api').rstrip('/')
DEFAULT_PASSWORD = os.getenv('PASSWORD', 'Azertyuiop12!')
RATE_LIMIT_SECONDS = float(os.getenv('RATE_LIMIT_SECONDS', '0.25'))
LOGS_DIR = Path(os.getenv('LOG_DIR', 'backend/logs'))
ROOT_LOG_FILE = os.getenv('LOGS_ROOT_FILE')

USERS_JSON_DEFAULT = Path(os.path.expanduser('~/Downloads/dinnerhopping.users.json'))

TEAM_TSV_DEFAULT = """person1_name	person2_name	person1_email	person2_email
Ben Weber	Clara Fischer	ben.weber@email.de	clara.fischer@email.de
Eva Klein	Felix Lang	eva.klein@email.de	felix.lang@email.de
Hans Zimmer	Ida Schulz	hans.zimmer@email.de	ida.schulz@email.de
Kira Brand	Leo Roth	kira.brand@email.de	leo.roth@email.de
Nora Beck	Otto Hahn	nora.beck@email.de	otto.hahn@email.de
Quintus Voss	Rosa Berg	quintus.voss@email.de	rosa.berg@email.de
Tom Richter	Lina Koch	tom.richter@email.de	lina.koch@email.de
Viktor Bauer	Zoe Schröder	viktor.b@email.de	zoe.schroeder@email.de
Yannick Becker	Amelie Schäfer	yannick.b@email.de	amelie.schaefer@email.de
Moritz Horn	Charlotte Graf	moritz.horn@email.de	charlotte.graf@email.de
Hannah Kraus	Julian Lorenz	hannah.k@email.de	julian.lorenz@email.de
Lara Huber	Finn Pohl	lara.huber@email.de	finn.pohl@email.de
Marie Pohl	Lukas Engel	marie.pohl@email.de	lukas.engel@email.de
Mia Brandt	Leon Herrmann	mia.brandt@email.de	leon.herrmann@email.de
Julia Peters	Tim Kramer	julia.peters@email.de	tim.kramer@email.de
Sarah Berger	David Roth	sarah.berger@email.de	david.roth@email.de
Anton Schubert	Ida Simon	anton.schubert@email.de	ida.simon@email.de
Maximilian Böhm	Lea Otto	max.boehm@email.de	lea.otto@email.de
Oskar Friedrich	Mathilda Vogel	oskar.f@email.de	mathilda.vogel@email.de
Timon Gärtner	Luisa Ernst	timon.g@email.de	luisa.ernst@email.de
Jan Winter	Sophie Sommer	jan.winter@email.de	sophie.sommer@email.de
Florian Grimm	Nele Schreiber	florian.g@email.de	nele.schreiber@email.de
Simon Jäger	Elisa Kurz	simon.j@email.de	elisa.kurz@email.de
Adrian Muth	Isabell Hahn	adrian.m@email.de	isabell.hahn@email.de
Erik Lange	Marie John	erik.l@email.de	marie.john@email.de
Justus Ritter	Hannah Thiel	justus.r@email.de	hannah.thiel@email.de
Fabian Neumann	Lina Beck	fabian.n@email.de	lina.beck@email.de
Jannik Schulz	Lara Simon	jannik.s@email.de	lara.simon@email.de
Emilia Graf	Leo Keller	emilia.g@email.de	leo.keller@email.de
Mila Roth	Ben Kruse	mila.r@email.de	ben.kruse@email.de
Ida Klein	Noah Schreiber	ida.k@email.de	noah.schreiber@email.de
Leni Bauer	Moritz Seidel	leni.b@email.de	moritz.seidel@email.de
Ella Winter	Finn Brandt	ella.w@email.de	finn.brandt@email.de
Frida Peters	Leon Pohl	frida.p@email.de	leon.pohl@email.de
Lilly Hahn	David Engel	lilly.h@email.de	david.engel@email.de
Maja Neumann	Julia Sommer	maja.n@email.de	julia.sommer@email.de
Zoe Friedrich	Tom Berg	zoe.f@email.de	tom.berger@email.de
Hannah Jäger	Elias May	hannah.j@email.de	elias.may@email.de
Sophia Herrmann	Janis Kramer	sophia.h@email.de	janis.kramer@email.de
Lina Busch	Anton Linke	lina.b@email.de	anton.linke@email.de
Jonathan Thiel	Pia Adam	jonathan.t@email.de	pia.adam@email.de
Karl Simon	Nele Gärtner	karl.s@email.de	nele.gaertner@email.de
Ferdinand Winter	Clara Jung	ferdinand.w@email.de	clara.jung@email.de
Arthur Grimm	Leni Ritter	arthur.g@email.de	leni.ritter@email.de
Richard Kurz	Amelie Muth	richard.k@email.de	amelie.muth@email.de
Valentin Hahn	Zoe Busch	valentin.h@email.de	zoe.busch@email.de
Konrad John	Lina König	konrad.j@email.de	lina.koenig@email.de
Johannes Adam	Frida Ritter	johannes.a@email.de	frida.ritter@email.de
Maximilian Weber	Sophie Fischer	max.w@email.de	sophie.fischer@email.de
"""

_last_call = 0.0

def _rate_limit():
    global _last_call
    now = time.time()
    elapsed = now - _last_call
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_call = time.time()

# ---- Helpers ----

def gen_phone_for(email: str, seq: int) -> str:
    base = abs(hash(email)) % 10_000_000
    return f"+4930{seq:02d}{base:07d}"


def find_root_logfile() -> Optional[Path]:
    if ROOT_LOG_FILE:
        p = Path(ROOT_LOG_FILE)
        return p if p.exists() else None
    from datetime import datetime
    today = datetime.utcnow().strftime('%Y-%m-%d')
    candidate = LOGS_DIR / 'root' / f'{today}.log'
    return candidate if candidate.exists() else None


def _extract_verify_token(txt: str) -> Optional[str]:
    m = re.search(r"/verify-email\?token=([A-Za-z0-9_\-\.~]+)", txt)
    return m.group(1) if m else None


def try_auto_verify(client: httpx.Client, email: str) -> bool:
    log_path = find_root_logfile()
    try:
        start_size = log_path.stat().st_size if log_path else 0
    except Exception:
        start_size = 0
    # trigger resend
    try:
        _rate_limit()
        client.post(f"{BASE_URL}/resend-verification", json={"email": email})
    except Exception:
        pass
    time.sleep(0.3)
    token = None
    try:
        if log_path and log_path.exists():
            with log_path.open('r', encoding='utf-8', errors='ignore') as fh:
                fh.seek(start_size)
                tail = fh.read()
                token = _extract_verify_token(tail)
    except Exception:
        token = None
    if not token:
        return False
    try:
        _rate_limit()
        r = client.get(f"{BASE_URL}/verify-email", params={"token": token})
        return r.status_code == 200
    except Exception:
        return False

# ---- API ----

def api_register(client: httpx.Client, u: Dict) -> bool:
    try:
        _rate_limit()
        r = client.post(f"{BASE_URL}/register", json=u)
        if r.status_code == 201:
            return True
        # tolerate conflicts/validation duplicates
        try:
            detail = r.json().get('detail')
        except Exception:
            detail = r.text
        if r.status_code in (400, 409) and isinstance(detail, str) and 'already' in detail.lower():
            return True
        print(f"[WARN] register {u.get('email')} -> {r.status_code} {detail}")
        return False
    except Exception as e:
        print(f"[ERROR] register exception {u.get('email')}: {e}")
        return False


def api_login(client: httpx.Client, email: str, password: str) -> Optional[str]:
    try:
        _rate_limit()
        r = client.post(f"{BASE_URL}/login", json={"username": email, "password": password})
        if r.status_code == 200:
            return r.json().get('access_token')
        _rate_limit()
        r = client.post(f"{BASE_URL}/login", data={"username": email, "password": password})
        if r.status_code == 200:
            return r.json().get('access_token')
        print(f"[WARN] login failed for {email}: {r.status_code} {r.text}")
        return None
    except Exception as e:
        print(f"[ERROR] login exception for {email}: {e}")
        return None


def api_team_register(client: httpx.Client, token: str, payload: Dict, event_id: str) -> bool:
    try:
        _rate_limit()
        r = client.post(f"{BASE_URL}/registrations/team", json=payload, headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            return True
        # Fallbacks: downgrade course, force kitchen
        try:
            detail = r.json().get('detail')
        except Exception:
            detail = r.text
        pay2 = dict(payload)
        if pay2.get('course_preference') == 'main':
            pay2['course_preference'] = 'appetizer'
            _rate_limit()
            r2 = client.post(f"{BASE_URL}/registrations/team", json=pay2, headers={"Authorization": f"Bearer {token}"})
            if r2.status_code == 200:
                return True
            detail = getattr(r2, 'text', detail)
        if isinstance(detail, str) and ('kitchen' in detail.lower() or 'main course requires' in detail.lower()):
            pay3 = dict(pay2)
            pay3['kitchen_available'] = True
            if pay3.get('course_preference') == 'main':
                pay3['course_preference'] = 'appetizer'
            _rate_limit()
            r3 = client.post(f"{BASE_URL}/registrations/team", json=pay3, headers={"Authorization": f"Bearer {token}"})
            return r3.status_code == 200
        print(f"[WARN] team register failed {r.status_code} {detail}")
        # As a fallback for 5xx or timeouts, check if registration exists already
        try:
            if has_registration(client, token, event_id, team=True):
                return True
        except Exception:
            pass
        return False
    except Exception as e:
        print(f"[ERROR] team register exception: {e}")
        try:
            if has_registration(client, token, event_id, team=True):
                return True
        except Exception:
            pass
        return False


def api_solo_register(client: httpx.Client, token: str, payload: Dict, event_id: str) -> bool:
    try:
        _rate_limit()
        r = client.post(f"{BASE_URL}/registrations/solo", json=payload, headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            return True
        # retry appetizer
        if payload.get('course_preference') == 'main':
            pay2 = dict(payload)
            pay2['course_preference'] = 'appetizer'
            _rate_limit()
            r2 = client.post(f"{BASE_URL}/registrations/solo", json=pay2, headers={"Authorization": f"Bearer {token}"})
            return r2.status_code == 200
        print(f"[WARN] solo register failed {r.status_code} {r.text}")
        # Fallback: check registration status in case server processed but client missed response
        try:
            if has_registration(client, token, event_id, team=False):
                return True
        except Exception:
            pass
        return False
    except Exception as e:
        print(f"[ERROR] solo register exception: {e}")
        try:
            if has_registration(client, token, event_id, team=False):
                return True
        except Exception:
            pass
        return False

def has_registration(client: httpx.Client, token: str, event_id: str, team: bool | None) -> bool:
    """Check if the current user already has a registration for the given event.
    team=True -> require team_size>1, team=False -> team_size==1, None -> any.
    """
    try:
        _rate_limit()
        r = client.get(f"{BASE_URL}/registrations/registration-status", headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            return False
        data = r.json() or {}
        regs = data.get('registrations') or []
        for reg in regs:
            if str(reg.get('event_id')) != str(event_id):
                continue
            ts = int(reg.get('team_size') or 1)
            if team is True and ts > 1:
                return True
            if team is False and ts == 1:
                return True
            if team is None:
                return True
        return False
    except Exception:
        return False

def api_search_user(client: httpx.Client, token: str, email: str) -> bool:
    """Return True if user exists (search-user endpoint 200)."""
    try:
        _rate_limit()
        r = client.get(f"{BASE_URL}/registrations/search-user", params={"email": email}, headers={"Authorization": f"Bearer {token}"})
        return r.status_code == 200
    except Exception:
        return False

# ---- Builders ----

def build_register_payload(u: Dict, seq: int) -> Dict:
    addr = u.get('address_struct') or {}
    return {
        'email': (u.get('email') or '').lower(),
        'password': DEFAULT_PASSWORD,
        'password_confirm': DEFAULT_PASSWORD,
        'first_name': u.get('first_name') or '',
        'last_name': u.get('last_name') or '',
        'street': addr.get('street') or u.get('street') or '',
        'street_no': addr.get('street_no') or u.get('street_no') or '',
        'postal_code': addr.get('postal_code') or u.get('postal_code') or '',
        'city': addr.get('city') or u.get('city') or '',
        'gender': (u.get('gender') or 'prefer_not_to_say'),
        'phone_number': gen_phone_for(u.get('email') or '', seq),
        'lat': u.get('lat'),
        'lon': u.get('lon'),
        'allergies': u.get('allergies', []),
    }


def build_team_payload_from_user(event_id: str, u: Dict, partner_email: str, partner_exists: bool, partner_name: Optional[str]) -> Dict:
    payload = {
        'event_id': event_id,
        'cooking_location': 'creator',
        'dietary_preference': (u.get('default_dietary_preference') or None),
        # Forcer la cuisine dispo côté creator pour satisfaire la contrainte "cooking_location must have a kitchen"
        'kitchen_available': True if u.get('kitchen_available') is None else bool(u.get('kitchen_available')) or True,
        'main_course_possible': bool(u.get('main_course_possible')) if u.get('main_course_possible') is not None else None,
        'course_preference': None,
    }
    if partner_exists:
        payload['partner_existing'] = {'email': partner_email}
        payload['partner_external'] = None
    else:
        payload['partner_existing'] = None
        # name requis côté backend pour partner_external
        safe_name = partner_name or partner_email.split('@')[0].replace('.', ' ').title()
        payload['partner_external'] = {
            'name': safe_name,
            'email': partner_email,
            'kitchen_available': False,
            'main_course_possible': False,
        }
    return payload


def build_solo_payload_from_user(event_id: str, u: Dict) -> Dict:
    return {
        'event_id': event_id,
        'dietary_preference': (u.get('default_dietary_preference') or None),
        'kitchen_available': bool(u.get('kitchen_available')) if u.get('kitchen_available') is not None else None,
        'main_course_possible': bool(u.get('main_course_possible')) if u.get('main_course_possible') is not None else None,
        'course_preference': None,
    }

# ---- Teams TSV parsing ----

def parse_teams(tsv_text: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    partners: Dict[str, str] = {}
    partner_names: Dict[str, str] = {}
    lines = [l for l in tsv_text.strip().splitlines() if l.strip()]
    # skip header if present
    start_idx = 1 if lines and 'person1_email' in lines[0] else 0
    for line in lines[start_idx:]:
        cols = line.split('\t')
        if len(cols) < 4:
            continue
        p1_name = cols[0].strip() if len(cols) > 0 else ''
        p2_name = cols[1].strip() if len(cols) > 1 else ''
        p1_email = cols[2].strip().lower()
        p2_email = cols[3].strip().lower()
        if p1_email and p2_email:
            partners[p1_email] = p2_email
            partners[p2_email] = p1_email  # bi-directional for lookup
            partner_names[p1_email] = p2_name
            partner_names[p2_email] = p1_name
    return partners, partner_names


# ---- Main ----

def main():
    ap = argparse.ArgumentParser(description='Register users + enroll teams from JSON')
    ap.add_argument('--event-id', default='68ee61566b9443d59678b5e7', help='Target event ObjectId (default: 68ee61566b9443d59678b5e7)')
    ap.add_argument('--users-json', default=str(USERS_JSON_DEFAULT), help='Path to users JSON file')
    ap.add_argument('--teams-tsv', default='', help='Path to TSV of teams (optional; default uses embedded list)')
    ap.add_argument('--enroll-only', action='store_true', help='Skip account creation and only login+enroll')
    ap.add_argument('--teams-only', action='store_true', help='Process only users that are in a team (skip solos)')
    ap.add_argument('--solo-only', action='store_true', help='Process only solo registrations (ignore teams)')
    args = ap.parse_args()

    users_path = Path(args.users_json)
    if not users_path.exists():
        raise SystemExit(f"Users JSON introuvable: {users_path}")

    if args.teams_tsv:
        tsv_path = Path(args.teams_tsv)
        if not tsv_path.exists():
            raise SystemExit(f"Teams TSV introuvable: {tsv_path}")
        tsv_text = tsv_path.read_text(encoding='utf-8')
    else:
        tsv_text = TEAM_TSV_DEFAULT

    teams_map, partner_names_map = parse_teams(tsv_text)

    with users_path.open('r', encoding='utf-8') as fh:
        users: List[Dict] = json.load(fh)

    created = 0
    verified = 0
    teams_ok = 0
    solos_ok = 0

    # Timeout plus large pour absorber le coût des emails/transactions côté serveur
    with httpx.Client(timeout=httpx.Timeout(10.0, read=90.0, write=30.0, pool=60.0), follow_redirects=True) as client:
        # 1) (Optionnel) Création des comptes
        if not args.enroll_only:
            for i, u in enumerate(users, 1):
                reg_payload = build_register_payload(u, i)
                ok = api_register(client, reg_payload)
                if ok:
                    created += 1
                    # tentative d'auto-vérification pour permettre le login ensuite
                    if try_auto_verify(client, reg_payload['email']):
                        verified += 1
        # 2) Enroll
        for i, u in enumerate(users, 1):
            email = (u.get('email') or '').lower()
            if not email:
                continue
            partner = teams_map.get(email)
            if args.teams_only and not partner:
                continue
            if args.solo_only and partner:
                continue

            token = api_login(client, email, DEFAULT_PASSWORD)
            if not token:
                # retry once after auto-verify
                if try_auto_verify(client, email):
                    verified += 1
                    token = api_login(client, email, DEFAULT_PASSWORD)
            if not token:
                print(f"[WARN] Impossible de se connecter pour {email}, on saute l'inscription.")
                continue
            if partner and (email < partner):
                # Vérifier si le partenaire existe; sinon, basculer en partner_external pour éviter un 500 côté backend
                partner_exists = api_search_user(client, token, partner)
                partner_name = partner_names_map.get(email)
                payload = build_team_payload_from_user(args.event_id, u, partner, partner_exists, partner_name)
                if api_team_register(client, token, payload, args.event_id):
                    teams_ok += 1
            elif not partner:
                payload = build_solo_payload_from_user(args.event_id, u)
                if api_solo_register(client, token, payload, args.event_id):
                    solos_ok += 1
            else:
                # partner and email > partner -> l'autre membre créera l'équipe
                pass

    print('\n--- Summary ---')
    print(f"Users processed: {len(users)}")
    print(f"Registered/Existing: {created}")
    print(f"Auto-verified: {verified}")
    print(f"Teams created: {teams_ok}")
    print(f"Solo registrations: {solos_ok}")


if __name__ == '__main__':
    main()
