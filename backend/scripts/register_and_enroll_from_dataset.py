#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Importer et inscrire des participant·e·s depuis data_db/dataset_for_event_geocoded_geocoded.csv
via l'API backend, en respectant une limite globale de 4 requêtes/seconde.

Fonctions clés:
- Crée les comptes via POST /register (password = Azertyuiop12!)
- Tente une auto-vérification des emails en lisant les logs (si LOG_TO_FILES activé)
- Se connecte via POST /login pour le créateur de l'équipe (email vérifié requis)
- Inscrit par équipe via POST /registrations/team quand person2_* présent, sinon /registrations/solo

Pré-requis:
- Backend en cours d'exécution (BACKEND_BASE_URL)
- Accès aux logs si auto-verify (par défaut backend/logs/root/YYYY-MM-DD.log)

Usage:
  python backend/scripts/register_and_enroll_from_dataset.py --event-id <EVENT_ID>

Variables d'env utiles:
- BACKEND_BASE_URL (def: http://localhost:8000)
- RATE_LIMIT_SECONDS (def: 0.25)  # 4 req/s max
- PASSWORD (def: Azertyuiop12!)
- DATASET (def: data_db/dataset_for_event_geocoded_geocoded.csv)
- LOGS_ROOT_FILE (optionnel chemin exact du log root)
- LOG_DIR (def: backend/logs)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import httpx


# ------- Config -------
BASE_URL = os.getenv("BACKEND_BASE_URL", "http://dinnerhoppings.acrevon.fr/api").rstrip("/")
DEFAULT_PASSWORD = os.getenv("PASSWORD", "Azertyuiop12!")
# 4 req/s max -> 0.25s entre appels
RATE_LIMIT_SECONDS = float(os.getenv("RATE_LIMIT_SECONDS", "0.25"))

DEFAULT_DATASET = Path(os.getenv("DATASET", "/Users/loan/Documents/GitHub/DinnerHopping-s-Website/data_db/dataset_for_event_geocoded_geocoded.csv"))
LOGS_DIR = Path(os.getenv("LOG_DIR", Path("backend/logs")))
ROOT_LOG_FILE = os.getenv("LOGS_ROOT_FILE")

_last_call_ts: float = 0.0


def _rate_limit():
    global _last_call_ts
    now = time.time()
    elapsed = now - _last_call_ts
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_call_ts = time.time()


# ------- Helpers (mapping) -------

_ADDR_LEFT_RE = re.compile(r"^(.*?)[\s,]+(\d+[A-Za-z]?)\s*$")
_POST_CITY_RE = re.compile(r"^\s*(\d{4,5})\s+(.+?)\s*$")


def split_name(full: str) -> Tuple[str, str]:
    full = (full or "").strip()
    if not full:
        return "", ""
    parts = full.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def map_gender(g: str) -> str:
    g = (g or "").strip().lower()
    if g in {"w", "f", "female", "femme"}:
        return "female"
    if g in {"m", "male", "homme"}:
        return "male"
    if g in {"d", "diverse", "divers", "non-binary", "non binaire"}:
        return "diverse"
    return "prefer_not_to_say"


def parse_address(addr: str) -> Tuple[str, str, str, str]:
    """Retourne (street, street_no, postal_code, city)."""
    street = street_no = postal = city = ""
    if not addr:
        return street, street_no, postal, city
    parts = [p.strip() for p in str(addr).split(",")]
    left = parts[0] if parts else ""
    right = parts[1] if len(parts) > 1 else ""
    m = _ADDR_LEFT_RE.match(left)
    if m:
        street, street_no = m.group(1).strip(), m.group(2).strip()
    else:
        street = left.strip()
        street_no = ""
    m2 = _POST_CITY_RE.match(right)
    if m2:
        postal, city = m2.group(1).strip(), m2.group(2).strip()
    else:
        city = right.strip()
        postal = ""
    return street, street_no, postal, city


def map_food_pref(v: str) -> Optional[str]:
    v = (v or "").strip().lower()
    if v in {"vegan", "veganer", "vegane"}:
        return "vegan"
    if v in {"vegetarisch", "vegetarian", "vegetarien"}:
        return "vegetarian"
    if v in {"alles", "omnivore", "tout"}:
        return "omnivore"
    # heuristique
    if v.startswith("veg") and "vegan" in v:
        return "vegan"
    if v.startswith("veg"):
        return "vegetarian"
    return "omnivore" if v else None


def map_course(v: str) -> Optional[str]:
    v = (v or "").strip().lower()
    if v.startswith("vor") or "appetizer" in v or "entr" in v:
        return "appetizer"
    if v.startswith("haupt") or v == "main" or "principal" in v:
        return "main"
    if v.startswith("dess"):
        return "dessert"
    if v == "egal" or not v:
        return None
    return None


def map_bool(v: str) -> Optional[bool]:
    t = (v or "").strip().lower()
    if t in {"ja", "yes", "true", "1", "y", "oui"}:
        return True
    if t in {"nein", "no", "false", "0", "n", "non"}:
        return False
    return None


def parse_allergies(v: str) -> list[str]:
    if not v:
        return []
    # Autorise valeurs libres (API les acceptera)
    items = [x.strip() for x in str(v).replace("/", ",").split(",")]
    return [x for x in items if x]


def gen_phone_for(email: str, fallback_index: int) -> str:
    """Génère un numéro E.164 plausible pour l'Allemagne (Berlin) afin de passer la validation.

    Format: +49 30 XXXXXXXX (030 = Berlin). Longueur nationale raisonnable.
    """
    base = abs(hash(email)) % 10_000_000  # 7 chiffres
    return f"+4930{fallback_index:02d}{base:07d}"  # ex: +4930012345678


# ------- Logs helpers (auto verify) -------

def find_root_logfile() -> Optional[Path]:
    if ROOT_LOG_FILE:
        p = Path(ROOT_LOG_FILE)
        return p if p.exists() else None
    today = dt.datetime.utcnow().strftime("%Y-%m-%d")
    candidate = LOGS_DIR / "root" / f"{today}.log"
    return candidate if candidate.exists() else None


def extract_token_from_text(txt: str) -> Optional[str]:
    m = re.search(r"/verify-email\?token=([A-Za-z0-9_\-\.~]+)", txt)
    return m.group(1) if m else None


def try_auto_verify(client: httpx.Client, email: str) -> bool:
    """Déclenche resend et tente de valider via lien dans les logs."""
    log_path = find_root_logfile()
    try:
        start_size = log_path.stat().st_size if log_path else 0
    except Exception:
        start_size = 0

    # 1) resend
    try:
        _rate_limit()
        client.post(f"{BASE_URL}/resend-verification", json={"email": email})
    except Exception:
        pass

    time.sleep(0.3)

    # 2) lire logs
    token = None
    try:
        log_path = find_root_logfile()
        if log_path and log_path.exists():
            with log_path.open("r", encoding="utf-8", errors="ignore") as fh:
                fh.seek(start_size)
                tail = fh.read()
                if "/verify-email?token=" in tail:
                    # si email présent, mieux; sinon, on prend le dernier vu
                    if email in tail:
                        token = extract_token_from_text(tail)
                    else:
                        token = extract_token_from_text(tail)
    except Exception:
        token = None

    if not token:
        return False

    # 3) GET /verify-email
    try:
        _rate_limit()
        vres = client.get(f"{BASE_URL}/verify-email", params={"token": token})
        return vres.status_code == 200
    except Exception:
        return False


# ------- API helpers -------

def register_user(client: httpx.Client, payload: Dict) -> Tuple[bool, Optional[str]]:
    try:
        _rate_limit()
        r = client.post(f"{BASE_URL}/register", json=payload)
        if r.status_code == 201:
            return True, r.json().get("id")
        else:
            # Conflit/validation -> tolérance best-effort
            try:
                detail = r.json().get("detail")
            except Exception:
                detail = r.text
            if r.status_code in (400, 409) and isinstance(detail, str) and "already" in detail.lower():
                return True, None
            print(f"[WARN] register {payload.get('email')} -> {r.status_code} {detail}")
            return False, None
    except Exception as e:
        print(f"[ERROR] register exception: {e}")
        return False, None


def login(client: httpx.Client, email: str, password: str) -> Optional[str]:
    try:
        _rate_limit()
        r = client.post(f"{BASE_URL}/login", json={"username": email, "password": password})
        if r.status_code == 200:
            return r.json().get("access_token")
        # fallback OAuth2 form-encoded
        _rate_limit()
        r = client.post(f"{BASE_URL}/login", data={"username": email, "password": password})
        if r.status_code == 200:
            return r.json().get("access_token")
        try:
            print(f"[WARN] login failed for {email}: {r.status_code} {r.text}")
        except Exception:
            pass
        return None
    except Exception as e:
        print(f"[ERROR] login exception for {email}: {e}")
        return None


def enroll_team(client: httpx.Client, token: str, payload: Dict) -> bool:
    try:
        _rate_limit()
        r = client.post(f"{BASE_URL}/registrations/team", json=payload, headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            return True
        # Retenter en downgrade si l'erreur est liée au main course/kitchen
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text
        print(f"[WARN] team enroll failed {r.status_code} {detail}")
        # 1) si main impossible -> downgrader en appetizer
        payload_try = dict(payload)
        if isinstance(payload_try.get("course_preference"), str) and payload_try["course_preference"] == "main":
            payload_try["course_preference"] = "appetizer"
            _rate_limit()
            r2 = client.post(f"{BASE_URL}/registrations/team", json=payload_try, headers={"Authorization": f"Bearer {token}"})
            if r2.status_code == 200:
                return True
            try:
                detail2 = r2.json().get("detail")
            except Exception:
                detail2 = r2.text
            print(f"[WARN] retry team (course=appetizer) failed {r2.status_code} {detail2}")
            detail = detail2 or detail

        # 2) si kitchen requis côté creator -> forcer kitchen_available=True et réessayer
        if isinstance(detail, str) and ("kitchen" in detail.lower() or "main course requires" in detail.lower() or "cooking location" in detail.lower()):
            payload_try2 = dict(payload)
            payload_try2["kitchen_available"] = True
            # si besoin, mettre course à appetizer
            if payload_try2.get("course_preference") == "main":
                payload_try2["course_preference"] = "appetizer"
            _rate_limit()
            r3 = client.post(f"{BASE_URL}/registrations/team", json=payload_try2, headers={"Authorization": f"Bearer {token}"})
            if r3.status_code == 200:
                return True
            try:
                print(f"[WARN] retry team (kitchen_available=True) failed {r3.status_code} {r3.text}")
            except Exception:
                pass
        return False
    except Exception as e:
        print(f"[ERROR] enroll_team exception: {e}")
        return False


def enroll_solo(client: httpx.Client, token: str, payload: Dict) -> bool:
    try:
        _rate_limit()
        r = client.post(f"{BASE_URL}/registrations/solo", json=payload, headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            return True
        try:
            print(f"[WARN] solo enroll failed {r.status_code} {r.text}")
        except Exception:
            pass
        # downgrade main->appetizer
        if isinstance(payload.get("course_preference"), str) and payload["course_preference"] == "main":
            payload = dict(payload)
            payload["course_preference"] = "appetizer"
            _rate_limit()
            r2 = client.post(f"{BASE_URL}/registrations/solo", json=payload, headers={"Authorization": f"Bearer {token}"})
            return r2.status_code == 200
        return False
    except Exception as e:
        print(f"[ERROR] enroll_solo exception: {e}")
        return False


# ------- Payload builders -------

def build_user_payload(email: str, name: str, gender: str, address: str, study: str, food_pref: str, allergies: str, lat: str, lon: str, idx: int) -> Dict:
    first, last = split_name(name)
    street, street_no, postal, city = parse_address(address)
    diet = map_food_pref(food_pref)
    alls = parse_allergies(allergies)
    payload: Dict = {
        "email": (email or "").lower().strip(),
        "password": DEFAULT_PASSWORD,
        "password_confirm": DEFAULT_PASSWORD,
        "first_name": first,
        "last_name": last,
        "street": street,
        "street_no": street_no,
        "postal_code": postal,
        "city": city,
        "gender": map_gender(gender),
        # Fournir un numéro E.164 plausible car /register le valide toujours
        "phone_number": gen_phone_for(email, idx),
        # Champs optionnels
        "lat": float(lat) if str(lat).strip() not in ("", "None") else None,
        "lon": float(lon) if str(lon).strip() not in ("", "None") else None,
        "allergies": alls,
    }
    # La préférence alimentaire par défaut peut être complétée ensuite via /profile, mais on la garde brute dans preferences si besoin
    if diet:
        payload.setdefault("preferences", {})["food_preference_raw"] = diet
    if study:
        payload.setdefault("preferences", {})["field_of_study"] = study
    return payload


def build_team_payload(event_id: str, creator_row: dict, partner_email: str) -> Dict:
    # Map préférences/cuisine
    dietary = map_food_pref(creator_row.get("essenspraeferenz"))
    kitchen_av = map_bool(creator_row.get("kueche_vorhanden"))
    main_possible = map_bool(creator_row.get("hauptspeise_moeglich"))
    course = map_course(creator_row.get("gang_wunsch"))
    # Si main choisi mais pas possible, le backend renverra 400; on fait un fallback au moment de l'appel
    return {
        "event_id": event_id,
        "partner_existing": {"email": partner_email},
        "partner_external": None,
        "cooking_location": "creator",
        "dietary_preference": dietary,
        "kitchen_available": kitchen_av,
        "main_course_possible": main_possible,
        "course_preference": course,
    }


def build_solo_payload(event_id: str, creator_row: dict) -> Dict:
    dietary = map_food_pref(creator_row.get("essenspraeferenz"))
    kitchen_av = map_bool(creator_row.get("kueche_vorhanden"))
    main_possible = map_bool(creator_row.get("hauptspeise_moeglich"))
    course = map_course(creator_row.get("gang_wunsch"))
    return {
        "event_id": event_id,
        "dietary_preference": dietary,
        "kitchen_available": kitchen_av,
        "main_course_possible": main_possible,
        "course_preference": course,
    }


# ------- Main -------

def main():
    parser = argparse.ArgumentParser(description="Register + enroll depuis dataset")
    parser.add_argument("--event-id", required=True, help="ID de l'événement cible")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="Chemin du CSV (def: data_db/dataset_for_event_geocoded_geocoded.csv)")
    args = parser.parse_args()

    dataset = Path(args.dataset)
    if not dataset.exists():
        print(f"[ERROR] Dataset introuvable: {dataset}")
        sys.exit(1)

    total_rows = 0
    accounts_created = 0
    accounts_verified = 0
    teams_ok = 0
    solos_ok = 0

    with httpx.Client(timeout=25.0, follow_redirects=True) as client:
        with dataset.open("r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                total_rows += 1
                idx = int(row.get("event_register_id") or total_rows)

                p1_email = (row.get("person1_email") or "").strip().lower()
                p2_email = (row.get("person2_email") or "").strip().lower()

                # 1) Créer compte Person1
                if p1_email:
                    u1 = build_user_payload(
                        email=p1_email,
                        name=row.get("person1_name") or "",
                        gender=row.get("person1_geschlecht") or "",
                        address=row.get("adresse") or "",
                        study=row.get("studiengang") or "",
                        food_pref=row.get("essenspraeferenz") or "",
                        allergies=row.get("unvertraeglichkeiten") or "",
                        lat=row.get("latitude") or "",
                        lon=row.get("longitude") or "",
                        idx=idx,
                    )
                    ok, _ = register_user(client, u1)
                    if ok:
                        accounts_created += 1
                        if try_auto_verify(client, p1_email):
                            accounts_verified += 1

                # 2) Créer compte Person2 (si présent)
                if p2_email:
                    u2 = build_user_payload(
                        email=p2_email,
                        name=row.get("person2_name") or "",
                        gender=row.get("person2_geschlecht") or "",
                        address=row.get("adresse") or "",
                        study=row.get("person2_studiengang") or "",
                        food_pref=row.get("essenspraeferenz") or "",
                        allergies=row.get("unvertraeglichkeiten") or "",
                        lat=row.get("latitude") or "",
                        lon=row.get("longitude") or "",
                        idx=idx + 1000,
                    )
                    ok, _ = register_user(client, u2)
                    if ok:
                        accounts_created += 1
                        if try_auto_verify(client, p2_email):
                            accounts_verified += 1

                # 3) Login du créateur
                token = None
                if p1_email:
                    token = login(client, p1_email, DEFAULT_PASSWORD)
                    if not token:
                        # dernier essai: tenter encore une fois l'auto-verify puis login
                        if try_auto_verify(client, p1_email):
                            accounts_verified += 1
                            token = login(client, p1_email, DEFAULT_PASSWORD)

                if not token:
                    print(f"[WARN] Impossible de se connecter pour {p1_email}, on saute la ligne {idx}.")
                    continue

                # 4) Inscription
                if p2_email:
                    team_payload = build_team_payload(args.event_id, row, p2_email)
                    ok = enroll_team(client, token, team_payload)
                    if ok:
                        teams_ok += 1
                else:
                    solo_payload = build_solo_payload(args.event_id, row)
                    ok = enroll_solo(client, token, solo_payload)
                    if ok:
                        solos_ok += 1

    print("")
    print(f"Rows lus: {total_rows}")
    print(f"Comptes créés/existants: {accounts_created}")
    print(f"Comptes vérifiés (auto): {accounts_verified}")
    print(f"Inscriptions équipe OK: {teams_ok}")
    print(f"Inscriptions solo OK: {solos_ok}")


if __name__ == "__main__":
    main()
