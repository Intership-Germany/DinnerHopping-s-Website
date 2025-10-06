from __future__ import annotations
import os
import random
from typing import Any, Dict, List, Optional, Tuple, Set
from bson.objectid import ObjectId
from datetime import datetime, timezone
from bson.objectid import ObjectId
from ..notifications import send_refund_processed

from .. import db as db_mod
from ..enums import CoursePreference, DietaryPreference, normalized_value
from .geocoding import geocode_address
from .routing import route_duration_seconds
from ..utils import send_email  # reuse notification helper
from .. import datetime_utils

# Weights default from env with sensible fallbacks
_DEF = lambda name, d: float(os.getenv(name, d))
W_DUP = _DEF('MATCH_W_DUP', '1000')           # penalty for duplicate pair meeting (reserved)
W_DIST = _DEF('MATCH_W_DIST', '1')            # weight for travel time seconds (guest->host)
W_PREF = _DEF('MATCH_W_PREF', '5')            # reward for course preference satisfied
W_ALL = _DEF('MATCH_W_ALLERGY', '3')          # penalty for allergy/diet conflict
W_HOST = _DEF('MATCH_W_DESIRED_HOST', '10')   # reward if team hosts their desired course
W_TRANS = _DEF('MATCH_W_TRANS', '0.5')        # penalty weight for between-phase transition time to next host
W_PARTY = _DEF('MATCH_W_FINAL_PARTY', '0.5')  # penalty weight for distance from dessert host to final party
# New: encourage monotonic convergence to final party phase-by-phase (main/dessert)
W_ORDER = _DEF('MATCH_W_PHASE_ORDER', '0.0')  # penalty weight for being farther from final party than previous phase

# Host selection breadth (evaluate multiple host candidates per phase). 0 or <=0 means all eligible
try:
    _HOST_CAND = int(os.getenv('MATCH_HOST_CANDIDATES', '0') or '0')
except (TypeError, ValueError):
    _HOST_CAND = 0

# Fast travel estimation helpers (fallback / fast mode)
from ..utils import haversine_m as _haversine_m  # type: ignore
from ..utils import approx_travel_time_minutes as _approx_minutes  # type: ignore
from ..utils import anonymize_public_address as _public_addr  # type: ignore

# Environment flags to control performance characteristics
_MATCH_GEOCODE = os.getenv('MATCH_GEOCODE_ON_MISSING', 'false').lower() in ('1','true','yes')
_MATCH_TRAVEL_FAST = os.getenv('MATCH_TRAVEL_FAST', 'true').lower() in ('1','true','yes')


async def _get_event(event_id: str) -> Optional[dict]:
    try:
        oid = ObjectId(event_id)
    except Exception:
        return None
    return await db_mod.db.events.find_one({'_id': oid})


async def _load_registrations(event_oid) -> List[dict]:
    regs = []
    async for r in db_mod.db.registrations.find({'event_id': event_oid, 'status': {'$nin': ['cancelled_by_user','cancelled_admin','refunded','expired']}}):
        regs.append(r)
    return regs

async def _load_teams(event_oid) -> Dict[str, dict]:
    """Return map team_id(str) -> team_doc for the event."""
    teams: Dict[str, dict] = {}
    async for t in db_mod.db.teams.find({'event_id': event_oid}):
        teams[str(t['_id'])] = t
    return teams

async def _user_profile(email: str) -> Optional[dict]:
    return await db_mod.db.users.find_one({'email': email})

async def _team_location(team: dict) -> Tuple[Optional[float], Optional[float]]:
    """Determine representative lat/lon for a team; try explicit member coords then geocode their address_struct.

    If a user's coordinates are missing and we successfully geocode their address, persist the lat/lon back to the user document.

    Returns (lat, lon) or (None, None) if unresolved.
    """
    members = team.get('members') or []
    coords: List[Tuple[float,float]] = []
    for m in members:
        email = m.get('email')
        if not email:
            continue
        u = await _user_profile(email)
        if not u:
            continue
        lat = u.get('lat'); lon = u.get('lon')
        if isinstance(lat,(int,float)) and isinstance(lon,(int,float)):
            coords.append((float(lat), float(lon)))
            continue
        # try geocode
        st = ((u.get('address_struct') or {}).get('street') or '')
        no = ((u.get('address_struct') or {}).get('street_no') or '')
        pc = ((u.get('address_struct') or {}).get('postal_code') or '')
        city = ((u.get('address_struct') or {}).get('city') or '')
        parts = " ".join([st, no]).strip()
        right = " ".join([pc, city]).strip()
        addr = f"{parts}, {right}".strip(', ')
        if addr and _MATCH_GEOCODE:
            latlon = await geocode_address(addr)
            if latlon:
                glat, glon = latlon
                coords.append((glat, glon))
                # persist to user document for future runs
                try:
                    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
                    await db_mod.db.users.update_one({'_id': u['_id']}, {'$set': {'lat': float(glat), 'lon': float(glon), 'geocoded_at': now}})
                except Exception:
                    pass
    if coords:
        # average
        lat = sum(c[0] for c in coords)/len(coords)
        lon = sum(c[1] for c in coords)/len(coords)
        return (lat, lon)
    return (None, None)


def _team_key(reg: dict) -> str:
    tid = reg.get('team_id')
    if tid:
        return str(tid)
    # solo registration -> own pseudo-team id based on registration id
    return f"solo:{str(reg.get('_id'))}"

async def _build_teams(event_oid) -> List[dict]:
    regs = await _load_registrations(event_oid)
    teams_docs = await _load_teams(event_oid)
    # Load event to access valid_zip_codes if any
    ev = await db_mod.db.events.find_one({'_id': event_oid})
    allowed_zips = set([str(z).strip() for z in (ev.get('valid_zip_codes') or [])]) if ev else set()
    groups: Dict[str, List[dict]] = {}
    for r in regs:
        key = _team_key(r)
        groups.setdefault(key, []).append(r)
    teams: List[dict] = []
    for tid, members_regs in groups.items():
        # try to enrich from teams collection if exists
        team_doc = teams_docs.get(tid) if tid in teams_docs else None
        # If zip restrictions apply, check members' postal codes
        if allowed_zips:
            member_emails = []
            if team_doc and isinstance(team_doc.get('members'), list):
                for m in team_doc['members']:
                    em = m.get('email')
                    if em:
                        member_emails.append(em)
            else:
                for r in members_regs:
                    em = r.get('user_email_snapshot')
                    if em:
                        member_emails.append(em)
            ok = False
            for em in set(member_emails):
                u = await db_mod.db.users.find_one({'email': em})
                if u:
                    pc = ((u.get('address_struct') or {}).get('postal_code'))
                    if pc and str(pc).strip() in allowed_zips:
                        ok = True
                        break
            if not ok:
                continue
        size = max(r.get('team_size') or 1 for r in members_regs)
        pref = None
        diet = None
        for r in members_regs:
            pref = pref or normalized_value(CoursePreference, (r.get('preferences') or {}).get('course_preference'))
            diet = diet or normalized_value(DietaryPreference, r.get('diet'))
        pref = pref or None
        diet = diet or 'omnivore'
        t = {
            'team_id': tid,
            'member_regs': members_regs,
            'size': size,
            'course_preference': pref,
            'diet': diet,
            'team_doc': team_doc,
        }
        if team_doc:
            t['team_diet'] = normalized_value(DietaryPreference, team_doc.get('team_diet'), default=t['diet']) or 'omnivore'
            team_course_pref = normalized_value(CoursePreference, team_doc.get('course_preference'), default=t['course_preference'])
            if team_course_pref:
                t['course_preference'] = team_course_pref
            t['cooking_location'] = team_doc.get('cooking_location') or 'creator'
        else:
            t['team_diet'] = t['diet']
            t['cooking_location'] = 'creator'
        teams.append(t)
    # sort for stable behavior
    teams.sort(key=lambda x: x['team_id'])
    # attach coordinates
    for t in teams:
        lat, lon = await _team_location(t.get('team_doc') or {'members': [{'email': (t['member_regs'][0].get('user_email_snapshot'))}]})
        t['lat'] = lat; t['lon'] = lon
        # basic capability flags
        # main_course_possible if any member has main_course_possible at cooking location
        can_main = False
        team_doc = t.get('team_doc') or {}
        members = (team_doc.get('members') or [])
        if members:
            if t['cooking_location'] == 'creator':
                can_main = bool(members[0].get('main_course_possible'))
            elif len(members) > 1:
                can_main = bool(members[1].get('main_course_possible'))
        # Fallback: registration preferences then user profile attributes if team_doc absent or did not yield True
        if not can_main:
            try:
                # Check registration preferences
                for r in t.get('member_regs') or []:
                    prefs = (r.get('preferences') or {})
                    if prefs.get('main_course_possible') is True:
                        can_main = True
                        break
                # Check user documents if still false
                if not can_main:
                    for r in t.get('member_regs') or []:
                        em = r.get('user_email_snapshot')
                        if not em:
                            continue
                        u = await db_mod.db.users.find_one({'email': em})
                        if u and u.get('main_course_possible') is True:
                            can_main = True
                            break
            except Exception:
                pass
        t['can_host_main'] = can_main
        # broader kitchen capability (avoid hosting anyone without kitchen for any course)
        has_kitchen = team_doc.get('has_kitchen')
        # Fallback only if not explicitly set on team_doc
        if has_kitchen is None:
            # Start with any explicit member flag
            try:
                for m in members:
                    if m.get('kitchen_available') is True:
                        has_kitchen = True
                        break
                # Registration preferences
                if has_kitchen is None:
                    for r in t.get('member_regs') or []:
                        prefs = (r.get('preferences') or {})
                        if prefs.get('kitchen_available') is True:
                            has_kitchen = True
                            break
                # User documents
                if has_kitchen is None:
                    for r in t.get('member_regs') or []:
                        em = r.get('user_email_snapshot')
                        if not em:
                            continue
                        u = await db_mod.db.users.find_one({'email': em})
                        if u and u.get('kitchen_available') is True:
                            has_kitchen = True
                            break
            except Exception:
                pass
        if has_kitchen is None:
            # Fallback heuristic: if can main, assume kitchen exists
            has_kitchen = bool(can_main)
        t['can_host_any'] = bool(has_kitchen)
    return teams


def _compatible_diet(host_diet: str, guest_diet: str) -> bool:
    # vegan host is compatible with everyone (they cook vegan)
    # omnivore host should not host vegan guest ideally => mark as penalty
    host = (host_diet or 'omnivore').lower()
    guest = (guest_diet or 'omnivore').lower()
    if host == 'omnivore' and guest in ('vegan',):
        return False
    if host == 'vegetarian' and guest == 'vegan':
        return False
    return True


def _score_group_phase(host: dict, guests: List[dict], meal: str, weights: dict) -> Tuple[float, List[str]]:
    score = 0.0
    warnings: List[str] = []
    # Preference: reward if host prefers this course
    if (host.get('course_preference') or '').lower() == meal:
        score += weights.get('pref', W_PREF)
    # Host capability for main
    if meal == 'main' and not host.get('can_host_main'):
        score -= weights.get('cap_penalty', W_PREF)
        warnings.append('host_cannot_main')
    # Host capability for any course (kitchen availability)
    if meal in ('appetizer', 'dessert') and not host.get('can_host_any', True):
        score -= weights.get('cap_penalty', W_PREF)
        warnings.append('host_no_kitchen')
    # Diet compatibility
    for g in guests:
        if not _compatible_diet(host.get('team_diet'), g.get('team_diet')):
            score -= weights.get('allergy', W_ALL)
            warnings.append('diet_conflict')
    return score, warnings


async def _travel_time_for_phase(host: dict, guests: List[dict]) -> float:
    # guests travel from their home to host
    coords: List[Tuple[float,float]] = []
    for g in guests:
        if g.get('lat') is None or g.get('lon') is None or host.get('lat') is None:
            continue
        coords.append( (g['lat'], g['lon']) )
        coords.append( (host['lat'], host['lon']) )
    if not coords:
        return 0.0
    # compute pairwise trips guest->host for each guest
    total = 0.0
    for i in range(0, len(coords), 2):
        seg = coords[i:i+2]
        if len(seg) == 2:
            if _MATCH_TRAVEL_FAST:
                d = _haversine_m(float(seg[0][0]), float(seg[0][1]), float(seg[1][0]), float(seg[1][1]))
                total += _approx_minutes(d, mode='bike') * 60.0
            else:
                d = await route_duration_seconds(seg)
                total += (d or 0.0)
    return total


# ---- Constraints (admin) ----------------------------------------------------
async def _load_constraints(event_id: str) -> dict:
    """Load matching constraints for the event from collection 'matching_constraints'.

    Document shape:
    { event_id: str, forced_pairs: [ {a_email, b_email}... ], split_team_ids: [str,...] }
    """
    doc = await db_mod.db.matching_constraints.find_one({'event_id': event_id})
    if not doc:
        return {'forced_pairs': [], 'split_team_ids': []}
    return {
        'forced_pairs': [
            {
                'a_email': (p.get('a_email') or '').lower(),
                'b_email': (p.get('b_email') or '').lower(),
            }
            for p in (doc.get('forced_pairs') or [])
            if isinstance(p, dict)
        ],
        'split_team_ids': [str(x) for x in (doc.get('split_team_ids') or [])],
    }


def _apply_forced_pairs(units: List[dict], unit_emails: Dict[str, List[str]], forced_pairs: List[dict]) -> Tuple[List[dict], Dict[str, List[str]]]:
    """Merge two solo units (by emails) into a synthetic pair unit for each forced pair.

    - Only solos are eligible for pairing.
    - New unit_id format: 'pair:<a_email>+<b_email>' with emails sorted.
    - coordinates averaged when available; copies capabilities conservatively.
    - Returns (new_units, new_unit_emails)
    """
    if not forced_pairs:
        return units, unit_emails
    email_to_units = _emails_to_unit_index(units, unit_emails)
    by_id = {u['unit_id']: u for u in units}
    removed: Set[str] = set()
    additions: List[dict] = []
    for p in forced_pairs:
        a = (p.get('a_email') or '').lower(); b = (p.get('b_email') or '').lower()
        if not a or not b or a == b:
            continue
        ua_ids = [uid for uid in email_to_units.get(a, []) if by_id.get(uid, {}).get('size') == 1]
        ub_ids = [uid for uid in email_to_units.get(b, []) if by_id.get(uid, {}).get('size') == 1]
        if not ua_ids or not ub_ids:
            continue
        ua = by_id.get(ua_ids[0]); ub = by_id.get(ub_ids[0])
        if not ua or not ub or ua['unit_id'] in removed or ub['unit_id'] in removed:
            continue
        pair_unit = _merge_two_solos_into_pair(ua, ub, (a, b))
        additions.append(pair_unit)
        removed.add(ua['unit_id']); removed.add(ub['unit_id'])
    if not additions and not removed:
        return units, unit_emails
    new_units = [u for u in units if u['unit_id'] not in removed] + additions
    # rebuild unit_emails mapping comprehensively
    new_u2e: Dict[str, List[str]] = {}
    for u in new_units:
        uid = u['unit_id']
        if uid.startswith('pair:'):
            part = uid.split(':',1)[1]
            ems = part.split('+') if '+' in part else []
            new_u2e[uid] = [e for e in ems if e]
        else:
            new_u2e[uid] = list(unit_emails.get(uid, []))
    return new_units, new_u2e


# ---- Units building and splitting ------------------------------------------
async def _build_units_from_teams(teams: List[dict]) -> Tuple[List[dict], Dict[str, List[str]]]:
    """Transform team dicts into units and unit->emails mapping.

    Unit shape:
    { unit_id: str, size: int, lat: float|None, lon: float|None, team_diet: str,
      can_host_main: bool, can_host_any: bool, course_preference: Optional[str] }
    """
    units: List[dict] = []
    unit_emails: Dict[str, List[str]] = {}
    for t in teams:
        uid = str(t['team_id'])
        # collect emails from team_doc or registrations
        emails: List[str] = []
        team_doc = t.get('team_doc') or {}
        if team_doc.get('members'):
            for m in team_doc['members']:
                em = m.get('email')
                if em:
                    emails.append(em)
        else:
            for r in t.get('member_regs') or []:
                em = r.get('user_email_snapshot')
                if em:
                    emails.append(em)
        emails = list(dict.fromkeys(emails))
        # determine preferred host email based on cooking_location
        host_emails: List[str] = []
        if team_doc.get('members'):
            try:
                if (t.get('cooking_location') or 'creator') == 'creator':
                    emh = (team_doc['members'][0] or {}).get('email')
                else:
                    emh = (team_doc['members'][1] or {}).get('email') if len(team_doc['members']) > 1 else None
                if emh:
                    host_emails = [emh]
            except Exception:
                host_emails = []
        if not host_emails and emails:
            host_emails = [emails[0]]
        u = {
            'unit_id': uid,
            'size': int(t.get('size') or max(1, len(emails) or 1)),
            'lat': t.get('lat'),
            'lon': t.get('lon'),
            'team_diet': (t.get('team_diet') or t.get('diet') or 'omnivore'),
            'can_host_main': bool(t.get('can_host_main')),
            'can_host_any': bool(t.get('can_host_any', True)),
            'course_preference': t.get('course_preference'),
            'host_emails': host_emails,
        }
        units.append(u)
        unit_emails[uid] = emails
    return units, unit_emails


def _emails_to_unit_index(units: List[dict], unit_emails: Dict[str, List[str]]) -> Dict[str, List[str]]:
    idx: Dict[str, List[str]] = {}
    for u in units:
        uid = u['unit_id']
        for em in unit_emails.get(uid, []):
            idx.setdefault(em.lower(), []).append(uid)
    return idx


def _diet_merge(a: Optional[str], b: Optional[str]) -> str:
    order = {'vegan': 2, 'vegetarian': 1, 'omnivore': 0}
    sa = (a or 'omnivore').lower(); sb = (b or 'omnivore').lower()
    # choose stricter diet (max order)
    rev = {v:k for k,v in order.items()}
    return rev[max(order.get(sa, 0), order.get(sb, 0))]


def _merge_two_solos_into_pair(ua: dict, ub: dict, emails: Tuple[str, str]) -> dict:
    a, b = sorted([emails[0].lower(), emails[1].lower()])
    lat = None; lon = None
    if isinstance(ua.get('lat'), (int,float)) and isinstance(ub.get('lat'), (int,float)) and isinstance(ua.get('lon'), (int,float)) and isinstance(ub.get('lon'), (int,float)):
        lat = (float(ua['lat']) + float(ub['lat']))/2.0
        lon = (float(ua['lon']) + float(ub['lon']))/2.0
    else:
        lat = ua.get('lat') or ub.get('lat')
        lon = ua.get('lon') or ub.get('lon')
    return {
        'unit_id': f'pair:{a}+{b}',
        'size': 2,
        'lat': lat,
        'lon': lon,
        'team_diet': _diet_merge(ua.get('team_diet'), ub.get('team_diet')),
        'can_host_main': bool(ua.get('can_host_main') and ub.get('can_host_main')),
        'can_host_any': bool(ua.get('can_host_any') and ub.get('can_host_any')),
        'course_preference': None,
        'host_emails': [a, b],
    }


def _apply_required_splits(units: List[dict], unit_emails: Dict[str, List[str]], split_team_ids: List[str]) -> Tuple[List[dict], Dict[str, List[str]]]:
    if not split_team_ids:
        return units, unit_emails
    by_id = {u['unit_id']: u for u in units}
    new_units: List[dict] = []
    removed: Set[str] = set()
    new_map: Dict[str, List[str]] = dict(unit_emails)
    for tid in split_team_ids:
        t = by_id.get(str(tid))
        if not t:
            continue
        emails = list(unit_emails.get(t['unit_id'], []))
        if len(emails) <= 1:
            continue
        # remove team
        removed.add(t['unit_id'])
        new_map.pop(t['unit_id'], None)
        # create split units
        for em in emails:
            u: dict = {
                'unit_id': f'split:{em.lower()}',
                'size': 1,
                'lat': t.get('lat'),
                'lon': t.get('lon'),
                'team_diet': (t.get('team_diet') or 'omnivore'),
                'can_host_main': bool(t.get('can_host_main')),
                'can_host_any': bool(t.get('can_host_any')),
                'course_preference': None,
                'host_emails': [em],
            }
            new_units.append(u)
            new_map[u['unit_id']] = [em]
    kept = [u for u in units if u['unit_id'] not in removed]
    return kept + new_units, new_map


async def _apply_minimal_splits(units: List[dict], unit_emails: Dict[str, List[str]]) -> Tuple[List[dict], Dict[str, List[str]]]:
    """Ensure total unit count divisible by 3 by splitting minimal number of teams into solo units.

    Preference: split duo teams first; if need 2 units, split two duos or one trio.
    """
    n = len(units)
    rem = n % 3
    if rem == 0:
        return units, unit_emails
    needed = (3 - rem) % 3  # 1 or 2
    # candidates: units that are not already split/pair and have >=2 emails
    candidates: List[Tuple[dict, List[str]]] = []
    for u in units:
        uid = u['unit_id']
        if isinstance(uid, str) and (uid.startswith('split:') or uid.startswith('pair:')):
            continue
        emails = list(unit_emails.get(uid, []))
        if len(emails) >= 2:
            candidates.append((u, emails))
    # Sort: prefer duos first, then trios
    candidates.sort(key=lambda x: len(x[1]))
    new_units: List[dict] = []
    removed: Set[str] = set()
    new_map: Dict[str, List[str]] = dict(unit_emails)
    for u, emails in candidates:
        if needed <= 0:
            break
        uid = u['unit_id']
        if uid in removed:
            continue
        delta = 0
        create_count = 0
        if needed >= 2 and len(emails) >= 3:
            create_count = 3
            delta = 2
        else:
            create_count = 2
            delta = 1
        # remove team
        removed.add(uid)
        new_map.pop(uid, None)
        # build split units for first create_count emails
        for em in emails[:create_count]:
            # try to use per-user coords if available
            lat = u.get('lat'); lon = u.get('lon')
            try:
                user = await db_mod.db.users.find_one({'email': em})
                if user and isinstance(user.get('lat'), (int,float)) and isinstance(user.get('lon'), (int,float)):
                    lat = float(user['lat']); lon = float(user['lon'])
            except Exception:
                pass
            su = {
                'unit_id': f'split:{em.lower()}',
                'size': 1,
                'lat': lat,
                'lon': lon,
                'team_diet': (u.get('team_diet') or 'omnivore'),
                'can_host_main': bool(u.get('can_host_main')),
                'can_host_any': bool(u.get('can_host_any')),
                'course_preference': None,
                'host_emails': [em],
            }
            new_units.append(su)
            new_map[su['unit_id']] = [em]
        needed -= delta
    kept = [x for x in units if x['unit_id'] not in removed]
    return kept + new_units, new_map


async def _user_address_string(email: Optional[str]) -> Optional[Tuple[str, str]]:
    """Return (full_address, public_address) best-effort for a user email."""
    if not email:
        return None
    u = await db_mod.db.users.find_one({'email': email})
    if not u:
        return None
    st = ((u.get('address_struct') or {}).get('street') or '').strip()
    no = ((u.get('address_struct') or {}).get('street_no') or '').strip()
    pc = ((u.get('address_struct') or {}).get('postal_code') or '').strip()
    city = ((u.get('address_struct') or {}).get('city') or '').strip()
    parts = []
    if st:
        parts.append(st + (f" {no}" if no else ""))
    right = " ".join([pc, city]).strip()
    if right:
        parts.append(right)
    full = ", ".join([p for p in parts if p])
    public = _public_addr(full) if full else None
    return (full or None, public or None)


async def _phase_groups(units: List[dict], phase: str, used_pairs: Set[Tuple[str,str]], weights: dict, last_at_host: Optional[Dict[str, Tuple[Optional[float], Optional[float]]]] = None, after_party_point: Optional[Tuple[float, float]] = None) -> List[dict]:
    """Form groups of 3 units for a given phase, greedily optimizing score and distance while avoiding duplicate pairs across phases.

    Adds penalties for between-phase transitions (from last host to this host for all members), and for dessert phase optionally penalizes distance to final party location.
    Also: applies a phase-order penalty so participants get closer to the final party as phases progress (when configured).
    """
    last_at_host = last_at_host or {}
    remaining = units[:]
    groups: List[dict] = []
    # helper for eligibility
    def can_host(u: dict) -> bool:
        return bool(u.get('can_host_main')) if phase == 'main' else bool(u.get('can_host_any', True))
    # helper to approx seconds between two points
    def approx_secs(a: Tuple[Optional[float], Optional[float]], b: Tuple[Optional[float], Optional[float]]) -> float:
        if not a or not b or a[0] is None or a[1] is None or b[0] is None or b[1] is None:
            return 0.0
        d = _haversine_m(float(a[0]), float(a[1]), float(b[0]), float(b[1]))
        return _approx_minutes(d, mode='bike') * 60.0
    # helper distance from a point to final party (seconds proxy)
    def secs_to_party(pt: Tuple[Optional[float], Optional[float]]) -> float:
        if after_party_point is None:
            return 0.0
        return approx_secs(pt, (after_party_point[0], after_party_point[1]))

    while len(remaining) >= 3:
        eligible_hosts = [u for u in remaining if can_host(u)]
        if not eligible_hosts:
            eligible_hosts = remaining[:]
        # choose candidate host set (breadth)
        if _HOST_CAND and _HOST_CAND > 0:
            candidates = eligible_hosts[:min(_HOST_CAND, len(eligible_hosts))]
        else:
            candidates = eligible_hosts
        best_overall: Optional[Tuple[float, dict, dict, dict, float, List[str], Tuple[Optional[str], Optional[str]]]] = None  # (score, host, g1, g2, travel, warnings, host_addr)
        phases = ['appetizer','main','dessert']
        L = len(remaining)
        for host in candidates:
            host_pt = (host.get('lat'), host.get('lon'))
            # build list of other units (not host)
            others = [u for u in remaining if u is not host]
            # prefetch host address once
            host_email = (host.get('host_emails') or [None])[0]
            host_addr: Tuple[Optional[str], Optional[str]] = (None, None)
            if host_email:
                try:
                    addr = await _user_address_string(host_email)
                    if addr:
                        host_addr = addr
                except Exception:
                    pass
            # evaluate all guest pairs for this host
            for i in range(len(others)):
                for j in range(i+1, len(others)):
                    g1 = others[i]; g2 = others[j]
                    base_score, warnings = _score_group_phase(host, [g1, g2], phase, weights)
                    # duplicate pair penalties
                    dup_penalty = 0.0
                    def _pair(a: str, b: str) -> Tuple[str,str]:
                        return (a, b) if a <= b else (b, a)
                    pair_list = [_pair(host['unit_id'], g1['unit_id']), _pair(host['unit_id'], g2['unit_id']), _pair(g1['unit_id'], g2['unit_id'])]
                    for p in pair_list:
                        if p in used_pairs:
                            dup_penalty += weights.get('dup', W_DUP)
                            warnings = warnings + ['duplicate_pair']
                    travel = await _travel_time_for_phase(host, [g1, g2])
                    # between-phase transition penalties
                    trans_seconds = 0.0
                    for u in (host, g1, g2):
                        prev = last_at_host.get(u['unit_id'])
                        if prev:
                            trans_seconds += approx_secs(prev, host_pt)
                    # dessert -> final party penalty (absolute distance at dessert)
                    party_seconds = 0.0
                    if after_party_point is not None and phase == 'dessert':
                        for _ in (host, g1, g2):
                            party_seconds += secs_to_party(host_pt)
                    # phase-order (monotonic) penalty relative to previous host location for each participant
                    order_seconds = 0.0
                    if after_party_point is not None and phase in ('main','dessert') and weights.get('phase_order', W_ORDER) > 0.0:
                        d_now = secs_to_party(host_pt)
                        for u in (host, g1, g2):
                            prev = last_at_host.get(u['unit_id'])
                            if prev:
                                d_prev = secs_to_party(prev)
                                if d_now > d_prev:
                                    order_seconds += (d_now - d_prev)
                    score = base_score \
                            - weights.get('dist', W_DIST) * float(travel) \
                            - weights.get('trans', W_TRANS) * float(trans_seconds) \
                            - (weights.get('final_party', W_PARTY) * float(party_seconds) if (after_party_point is not None and phase=='dessert') else 0.0) \
                            - (weights.get('phase_order', W_ORDER) * float(order_seconds) if (after_party_point is not None and phase in ('main','dessert')) else 0.0) \
                            - dup_penalty
                    if (best_overall is None) or (score > best_overall[0]):
                        best_overall = (score, host, g1, g2, travel, warnings, host_addr)
        if best_overall is None:
            break
        _, host, g1, g2, travel, warnings, host_addr = best_overall
        grp = {
            'phase': phase,
            'host_team_id': host['unit_id'],
            'guest_team_ids': [g1['unit_id'], g2['unit_id']],
            'score': float(_),
            'travel_seconds': float(travel),
            'warnings': list(sorted(set(warnings))) if warnings else [],
        }
        if host_addr and (host_addr[0] or host_addr[1]):
            grp['host_address'] = host_addr[0]
            grp['host_address_public'] = host_addr[1]
        groups.append(grp)
        # update used pairs (unordered)
        def _pair(a: str, b: str) -> Tuple[str,str]:
            return (a, b) if a <= b else (b, a)
        used_pairs.add(_pair(host['unit_id'], g1['unit_id']))
        used_pairs.add(_pair(host['unit_id'], g2['unit_id']))
        used_pairs.add(_pair(g1['unit_id'], g2['unit_id']))
        # remove chosen from remaining
        ids = {host['unit_id'], g1['unit_id'], g2['unit_id']}
        remaining = [u for u in remaining if u['unit_id'] not in ids]
    return groups


async def algo_greedy(event_oid, weights: dict, seed: int = 42) -> dict:
    teams = await _build_teams(event_oid)
    # Convert to units and split if needed to make count divisible by 3
    units, unit_emails = await _build_units_from_teams(teams)
    # Apply admin constraints
    ev = await db_mod.db.events.find_one({'_id': event_oid})
    event_id_str = str(ev.get('_id')) if ev else None
    if event_id_str:
        cons = await _load_constraints(event_id_str)
        units, unit_emails = _apply_forced_pairs(units, unit_emails, cons.get('forced_pairs') or [])
        units, unit_emails = _apply_required_splits(units, unit_emails, cons.get('split_team_ids') or [])
    units, unit_emails = await _apply_minimal_splits(units, unit_emails)
    # Shuffle base ordering
    rnd = random.Random(seed)
    rnd.shuffle(units)
    phases = ['appetizer','main','dessert']
    used_pairs: Set[Tuple[str,str]] = set()
    all_groups: List[dict] = []
    last_at: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    # final party point from event
    party_point: Optional[Tuple[float, float]] = None
    try:
        pt = (((ev or {}).get('after_party_location') or {}).get('point') or {}).get('coordinates')
        if isinstance(pt, list) and len(pt) == 2 and all(isinstance(x, (int,float)) for x in pt):
            party_point = (float(pt[1]), float(pt[0]))  # [lon,lat] -> (lat,lon)
    except Exception:
        party_point = None
    for idx, phase in enumerate(phases):
        # rotate units between phases to diversify
        if idx > 0:
            units = units[1:] + units[:1]
        groups = await _phase_groups(units, phase, used_pairs, weights, last_at_host=last_at, after_party_point=(party_point if phase=='dessert' else None))
        all_groups.extend(groups)
        # update last_at for next phase
        for g in groups:
            host_id = g.get('host_team_id')
            host = next((u for u in units if u['unit_id'] == host_id), None)
            if host is not None:
                pt = (host.get('lat'), host.get('lon'))
                for uid in [host_id] + [str(x) for x in (g.get('guest_team_ids') or [])]:
                    last_at[uid] = pt
    metrics = _compute_metrics(all_groups, weights)
    return { 'algorithm': 'greedy', 'groups': all_groups, 'metrics': metrics }


async def algo_random(event_oid, weights: dict, seed: int = 99) -> dict:
    teams = await _build_teams(event_oid)
    units, unit_emails = await _build_units_from_teams(teams)
    # Apply admin constraints
    ev = await db_mod.db.events.find_one({'_id': event_oid})
    event_id_str = str(ev.get('_id')) if ev else None
    if event_id_str:
        cons = await _load_constraints(event_id_str)
        units, unit_emails = _apply_forced_pairs(units, unit_emails, cons.get('forced_pairs') or [])
        units, unit_emails = _apply_required_splits(units, unit_emails, cons.get('split_team_ids') or [])
    units, unit_emails = await _apply_minimal_splits(units, unit_emails)
    rnd = random.Random(seed)
    rnd.shuffle(units)
    phases = ['appetizer','main','dessert']
    used_pairs: Set[Tuple[str,str]] = set()
    all_groups: List[dict] = []
    last_at: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    party_point: Optional[Tuple[float, float]] = None
    try:
        pt = (((ev or {}).get('after_party_location') or {}).get('point') or {}).get('coordinates')
        if isinstance(pt, list) and len(pt) == 2 and all(isinstance(x, (int,float)) for x in pt):
            party_point = (float(pt[1]), float(pt[0]))
    except Exception:
        party_point = None
    for phase in phases:
        rnd.shuffle(units)
        groups = await _phase_groups(units, phase, used_pairs, weights, last_at_host=last_at, after_party_point=(party_point if phase=='dessert' else None))
        all_groups.extend(groups)
        for g in groups:
            host_id = g.get('host_team_id')
            host = next((u for u in units if u['unit_id'] == host_id), None)
            if host is not None:
                pt = (host.get('lat'), host.get('lon'))
                for uid in [host_id] + [str(x) for x in (g.get('guest_team_ids') or [])]:
                    last_at[uid] = pt
    metrics = _compute_metrics(all_groups, weights)
    return { 'algorithm': 'random', 'groups': all_groups, 'metrics': metrics }


async def algo_local_search(event_oid, weights: dict, seed: int = 7) -> dict:
    # Start from greedy then (placeholder) keep same grouping; real local search can be added iteratively
    base = await algo_greedy(event_oid, weights, seed)
    groups = base['groups'][:]
    metrics = _compute_metrics(groups, weights)
    return { 'algorithm': 'local_search', 'groups': groups, 'metrics': metrics }


def _compute_metrics(groups: List[dict], weights: dict) -> dict:
    total_travel = sum(float(g.get('travel_seconds') or 0.0) for g in groups)
    total_score = sum(float(g.get('score') or 0.0) for g in groups)
    issues = sum(1 for g in groups if g.get('warnings'))
    return {
        'total_travel_seconds': total_travel,
        'aggregate_group_score': total_score,
        'groups_with_warnings': issues,
    }


ALGORITHMS = {
    'greedy': algo_greedy,
    'random': algo_random,
    'local_search': algo_local_search,
}


def _group_units_in_triads(units: List[dict]) -> List[List[dict]]:
    """Group units into triads, preferring triads composed entirely of duo-or-larger units.

    Strategy:
    - Form as many [duo, duo, duo] groups as possible (size >= 2 considered duo).
    - Then form as many [duo, solo, solo] groups as possible.
    - Then fallback to [duo, duo, solo] if needed.
    - Finally, group remaining solos into [solo, solo, solo].
    Preserves the relative order within each bucket for deterministic behavior.
    """
    duos = [u for u in units if int(u.get('size') or 1) >= 2]
    solos = [u for u in units if int(u.get('size') or 1) == 1]
    groups: List[List[dict]] = []
    # 3 duos
    while len(duos) >= 3:
        groups.append([duos.pop(0), duos.pop(0), duos.pop(0)])
    # 1 duo + 2 solos
    while len(duos) >= 1 and len(solos) >= 2:
        groups.append([duos.pop(0), solos.pop(0), solos.pop(0)])
    # 2 duos + 1 solo (fallback)
    while len(duos) >= 2 and len(solos) >= 1:
        groups.append([duos.pop(0), duos.pop(0), solos.pop(0)])
    # 3 solos
    while len(solos) >= 3:
        groups.append([solos.pop(0), solos.pop(0), solos.pop(0)])
    # If anything remains (rare: e.g., 2 duos left or 2 solos left), just pack in last group
    rest = duos + solos
    if rest:
        # Fill to triads by reusing last items (best-effort). Tests don't cover this path but keeps function total.
        while rest:
            grp = []
            for _ in range(3):
                if rest:
                    grp.append(rest.pop(0))
            if grp:
                groups.append(grp)
    return groups


async def run_algorithms(event_id: str, *, algorithms: List[str], weights: Optional[Dict[str, float]] = None) -> List[dict]:
    ev = await _get_event(event_id)
    if not ev:
        raise ValueError('event not found')
    oid = ev['_id']
    weights = weights or {}
    out: List[dict] = []
    for name in algorithms:
        fn = ALGORITHMS.get(name)
        if not fn:
            continue
        res = await fn(oid, weights)
        res['event_id'] = str(ev['_id'])
        out.append(res)
    return out


async def persist_match_proposal(event_id: str, proposal: dict) -> dict:
    # versioning: latest version +1
    latest = await db_mod.db.matches.find_one({'event_id': event_id}, sort=[('version', -1)])
    version = 1 + int(latest.get('version') or 0) if latest else 1
    doc = {
        'event_id': event_id,
        'groups': proposal.get('groups') or [],
        'metrics': proposal.get('metrics') or {},
        'status': 'proposed',
        'version': version,
        'algorithm': proposal.get('algorithm') or 'unknown',
    'created_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc),
    }
    res = await db_mod.db.matches.insert_one(doc)
    doc['id'] = str(res.inserted_id)
    return doc


async def mark_finalized(event_id: str, version: int, finalized_by: Optional[str]) -> dict:
    rec = await db_mod.db.matches.find_one({'event_id': event_id, 'version': int(version)})
    if not rec:
        raise ValueError('match version not found')
    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
    await db_mod.db.matches.update_one({'_id': rec['_id']}, {'$set': {'status': 'finalized', 'finalized_by': finalized_by, 'finalized_at': now}})
    await db_mod.db.events.update_one({'_id': ObjectId(event_id)}, {'$set': {'matching_status': 'finalized', 'updated_at': now}})
    rec = await db_mod.db.matches.find_one({'_id': rec['_id']})
    return rec


async def list_issues(event_id: str, version: Optional[int] = None) -> dict:
    # load match doc
    q: Dict[str, Any] = {'event_id': event_id}
    if version is not None:
        q['version'] = int(version)
    m = await db_mod.db.matches.find_one(q, sort=[('version', -1)])
    if not m:
        return {'groups': [], 'issues': []}
    groups = m.get('groups') or []
    issues: List[dict] = []
    # Build team status map
    team_cancelled: Set[str] = set()
    team_incomplete: Set[str] = set()
    # Build mapping team_id -> reg statuses
    reg_by_team: Dict[str, List[dict]] = {}
    async for r in db_mod.db.registrations.find({'event_id': ObjectId(event_id)}):
        tid = _team_key(r)
        reg_by_team.setdefault(tid, []).append(r)
    async for t in db_mod.db.teams.find({'event_id': ObjectId(event_id)}):
        if (t.get('status') or '').lower() == 'cancelled':
            team_cancelled.add(str(t['_id']))
    for tid, regs in reg_by_team.items():
        cancelled = [r for r in regs if r.get('status') in ('cancelled_by_user','cancelled_admin','refunded')]
        if len(regs) >= 2:
            if len(cancelled) == len(regs):
                team_cancelled.add(tid)
            elif len(cancelled) == 1:
                team_incomplete.add(tid)
        else:
            if cancelled:
                team_cancelled.add(tid)
    # --- Payment issues (optional) ---
    ev = await _get_event(event_id)
    include_payment_checks = False
    if ev and int(ev.get('fee_cents') or 0) > 0:
        pddl = ev.get('payment_deadline') or ev.get('registration_deadline')
        # Normalize to aware datetime
        if isinstance(pddl, str):
            try:
                pddl_dt = datetime_utils.parse_iso(pddl)
            except Exception:
                pddl_dt = None
        else:
            pddl_dt = pddl
        if pddl is None:
            # If no deadline defined, still allow checks (set include true) but only if event status is not draft
            include_payment_checks = (ev.get('status') or '').lower() not in ('draft','coming_soon')
        else:
            try:
                if pddl_dt and pddl_dt.tzinfo is None:
                    pddl_dt = pddl_dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass
            now = datetime.now(timezone.utc)
            include_payment_checks = bool(pddl_dt and now >= pddl_dt)
    team_payment_missing: Set[str] = set()
    team_payment_partial: Set[str] = set()
    if include_payment_checks:
        # Collect all registration ids for single scan of payments
        all_reg_ids: List[ObjectId] = []
        for regs in reg_by_team.values():
            for r in regs:
                rid = r.get('_id')
                if rid is not None:
                    all_reg_ids.append(rid)
        payments_by_reg: Dict[str, dict] = {}
        if all_reg_ids:
            async for p in db_mod.db.payments.find({'registration_id': {'$in': all_reg_ids}}):
                rid = p.get('registration_id')
                if rid is not None:
                    payments_by_reg[str(rid)] = p
        cancelled_statuses = {'cancelled_by_user','cancelled_admin','refunded','expired'}
        for tid, regs in reg_by_team.items():
            active = [r for r in regs if r.get('status') not in cancelled_statuses]
            if not active:
                continue
            paid_count = 0
            for r in active:
                rid = r.get('_id')
                pr = payments_by_reg.get(str(rid)) if rid is not None else None
                # Consider both legacy 'paid' and current 'succeeded' statuses as fully paid
                if pr and pr.get('status') in ('paid', 'succeeded'):
                    paid_count += 1
            if paid_count == 0:
                team_payment_missing.add(tid)
            elif paid_count < len(active):
                team_payment_partial.add(tid)
    # Flag groups
    for g in groups:
        g_issues: List[str] = []
        for tid in [g.get('host_team_id'), *(g.get('guest_team_ids') or [])]:
            if tid in team_cancelled:
                g_issues.append('faulty_team_cancelled')
            if tid in team_incomplete:
                g_issues.append('team_incomplete')
            if tid in team_payment_missing:
                g_issues.append('payment_missing')
            if tid in team_payment_partial:
                g_issues.append('payment_partial')
        if g_issues:
            issues.append({'group': g, 'issues': list(sorted(set(g_issues)))})
    return {'groups': groups, 'issues': issues}


async def refunds_overview(event_id: str) -> dict:
    ev = await _get_event(event_id)
    if not ev:
        return {'enabled': False, 'total_refund_cents': 0, 'items': []}
    enabled = bool(ev.get('refund_on_cancellation'))
    items: List[dict] = []
    total = 0
    if not enabled:
        return {'enabled': False, 'total_refund_cents': 0, 'items': []}
    fee = int(ev.get('fee_cents') or 0)
    async for r in db_mod.db.registrations.find({'event_id': ev['_id'], 'status': {'$in': ['cancelled_by_user','cancelled_admin']}}):
        amount = fee * int(r.get('team_size') or 1)
        # find payment
        p = await db_mod.db.payments.find_one({'registration_id': r.get('_id')})
        refunded = 0
        if p and (p.get('status') == 'refunded' or p.get('refunded') is True):
            refunded = amount
        due = max(0, amount - refunded)
        if due > 0:
            items.append({'registration_id': str(r.get('_id')), 'user_email': r.get('user_email_snapshot'), 'amount_cents': due})
            total += due
    return {'enabled': True, 'total_refund_cents': total, 'items': items}


async def _team_emails_map(event_id: str) -> Dict[str, List[str]]:
    """Return mapping team_id(str)->list of member emails; include pseudo-team for solo regs.
    Also supports split units: keys of the form 'split:<email>' map to [email].
    """
    out: Dict[str, List[str]] = {}
    # real teams
    async for t in db_mod.db.teams.find({'event_id': ObjectId(event_id)}):
        emails = [m.get('email') for m in (t.get('members') or []) if m.get('email')]
        out[str(t['_id'])] = emails
    # solo registrations
    async for r in db_mod.db.registrations.find({'event_id': ObjectId(event_id)}):
        if not r.get('team_id'):
            tid = f"solo:{str(r.get('_id'))}"
            em = r.get('user_email_snapshot')
            if em:
                out.setdefault(tid, []).append(em)
    return out


def _augment_emails_map_with_splits(base: Dict[str, List[str]], groups: List[dict]) -> Dict[str, List[str]]:
    """Extend mapping with any split:<email> and pair:<a+b> ids seen in groups."""
    out = dict(base)
    for g in groups:
        ids = [g.get('host_team_id'), *(g.get('guest_team_ids') or [])]
        for tid in ids:
            if not isinstance(tid, str):
                continue
            if tid.startswith('split:'):
                email = tid.split(':', 1)[1]
                out.setdefault(tid, []).append(email)
            elif tid.startswith('pair:'):
                part = tid.split(':', 1)[1]
                ems = part.split('+') if '+' in part else []
                if ems:
                    out.setdefault(tid, []).extend([e for e in ems if e])
    return out


async def generate_plans_from_matches(event_id: str, version: int) -> int:
    """Generate per-user plans documents from a proposed/finalized match version.

    Overwrites existing plans for (event_id, user_email). Returns number of plans written.
    """
    m = await db_mod.db.matches.find_one({'event_id': event_id, 'version': int(version)})
    if not m:
        return 0
    base_map = await _team_emails_map(event_id)
    groups = m.get('groups') or []
    team_to_emails = _augment_emails_map_with_splits(base_map, groups)
    # per user sections
    sections_by_email: Dict[str, List[dict]] = {}
    def _meal_time(meal: str) -> str:
        return '20:00' if meal=='main' else ('18:00' if meal=='appetizer' else '22:00')
    for g in groups:
        meal = g.get('phase')
        host = g.get('host_team_id')
        guests = g.get('guest_team_ids') or []
        host_emails = team_to_emails.get(str(host), [])
        guest_emails: List[str] = []
        for tid in guests:
            guest_emails.extend(team_to_emails.get(str(tid), []))
        host_email = host_emails[0] if host_emails else None
        sec = {
            'meal': meal,
            'time': _meal_time(meal),
            'host': {'email': host_email, 'emails': host_emails},  # include all host emails for duo transparency
            'guests': guest_emails,
        }
        for em in set((host_emails or []) + guest_emails):
            sections_by_email.setdefault(em, []).append(sec)
    # write plans
    written = 0
    for em, secs in sections_by_email.items():
        await db_mod.db.plans.delete_many({'event_id': ObjectId(event_id), 'user_email': em})
        doc = {
            'event_id': ObjectId(event_id),
            'user_email': em,
            'sections': secs,
            'created_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc),
        }
        await db_mod.db.plans.insert_one(doc)
        written += 1
    return written


async def finalize_and_generate_plans(event_id: str, version: int, finalized_by: Optional[str]) -> dict:
    rec = await mark_finalized(event_id, version, finalized_by)
    count = await generate_plans_from_matches(event_id, version)
    # create chats: per-dinner groups and ensure general chat has all participants
    try:
        from ..utils import ensure_chats_from_matches, ensure_general_chat_full
        _ = await ensure_chats_from_matches(event_id, version)
        await ensure_general_chat_full(event_id)
    except Exception:
        pass
    # notify participants (best-effort)
    sent = 0
    async for p in db_mod.db.plans.find({'event_id': ObjectId(event_id)}):
        em = p.get('user_email')
        if not em:
            continue
        title = 'Your DinnerHopping schedule is ready'
        lines = [
            'Hello,',
            'Your schedule for the event is ready. Log in to see details.',
            'Have a great time!',
            '— DinnerHopping Team',
        ]
        try:
            # include event title if available so template can use it
            ev = await db_mod.db.events.find_one({'_id': ObjectId(event_id)})
            ev_title = ev.get('title') if ev else None
            ok = await send_email(to=em, subject=title, body='\n'.join(lines), category='final_plan', template_vars={'event_title': ev_title, 'email': em})
            sent += 1 if ok else 0
        except Exception:
            pass
    return {'finalized_version': rec.get('version'), 'plans_written': count, 'emails_attempted': sent}


# ---------------- Travel paths for map (admin) -------------------------------

async def compute_team_paths(event_id: str, version: Optional[int] = None, ids: Optional[Set[str]] = None, fast: bool = True) -> dict:
    """Compute per-team travel paths across appetizer->main->dessert for a match version.

    Returns:
    { 'team_paths': { team_id: { 'points': [ {phase, lat, lon}... ], 'leg_seconds': [..], 'leg_minutes': [..] } },
      'bounds': { 'min_lat':..., 'min_lon':..., 'max_lat':..., 'max_lon':... },
      'after_party': { 'lat': float, 'lon': float } | None }
    """
    # Load match
    q: Dict[str, Any] = {'event_id': event_id}
    if version is not None:
        q['version'] = int(version)
    m = await db_mod.db.matches.find_one(q, sort=[('version', -1)])
    if not m:
        return {'team_paths': {}, 'bounds': None, 'after_party': None}
    groups = m.get('groups') or []
    # Apply ids-based filtering early to avoid unnecessary work
    id_filter: Optional[Set[str]] = set(ids) if ids else None
    if id_filter:
        def _involves_requested(g: dict) -> bool:
            host = str(g.get('host_team_id')) if g.get('host_team_id') is not None else None
            guests = [str(x) for x in (g.get('guest_team_ids') or [])]
            if host and host in id_filter:
                return True
            for t in guests:
                if t in id_filter:
                    return True
            return False
        groups = [g for g in groups if _involves_requested(g)]
        if not groups:
            return {'team_paths': {}, 'bounds': None, 'after_party': None}
    # Build mapping team_id -> lat/lon from teams (restrict to relevant ids when provided)
    ev = await _get_event(event_id)
    if not ev:
        return {'team_paths': {}, 'bounds': None, 'after_party': None}
    teams = await _build_teams(ev['_id'])
    needed_ids: Optional[Set[str]] = None
    if id_filter:
        needed_ids = set()
        for g in groups:
            h = g.get('host_team_id')
            if h is not None:
                needed_ids.add(str(h))
            for t in (g.get('guest_team_ids') or []):
                needed_ids.add(str(t))
    coord_map: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    for t in teams:
        tid = str(t['team_id'])
        if needed_ids and tid not in needed_ids:
            continue
        coord_map[tid] = (t.get('lat'), t.get('lon'))
    # Helper to resolve split/pair and missing ids
    async def _resolve_coords(tid: str) -> Tuple[Optional[float], Optional[float]]:
        if tid in coord_map:
            return coord_map[tid]
        if tid.startswith('split:'):
            em = tid.split(':',1)[1]
            u = await db_mod.db.users.find_one({'email': em})
            if u and isinstance(u.get('lat'), (int,float)) and isinstance(u.get('lon'), (int,float)):
                return (float(u['lat']), float(u['lon']))
        if tid.startswith('pair:'):
            part = tid.split(':',1)[1]
            ems = [e for e in part.split('+') if e]
            pts = []
            for em in ems:
                u = await db_mod.db.users.find_one({'email': em})
                if u and isinstance(u.get('lat'), (int,float)) and isinstance(u.get('lon'), (int,float)):
                    pts.append((float(u['lat']), float(u['lon'])))
            if pts:
                lat = sum(p[0] for p in pts)/len(pts)
                lon = sum(p[1] for p in pts)/len(pts)
                return (lat, lon)
        return (None, None)
    # Build per-phase host map
    phases = ['appetizer','main','dessert']
    path_points: Dict[str, List[Tuple[str, Optional[float], Optional[float]]]] = {}
    for phase in phases:
        for g in groups:
            if g.get('phase') != phase:
                continue
            host = str(g.get('host_team_id')) if g.get('host_team_id') is not None else None
            guests = [str(x) for x in (g.get('guest_team_ids') or [])]
            if not host:
                continue
            for tid in [host] + guests:
                if id_filter and tid not in id_filter:
                    continue
                lat, lon = await _resolve_coords(host)
                path_points.setdefault(tid, []).append((phase, lat, lon))
    # compute bounds and legs
    bounds = None
    min_lat = min_lon = float('inf')
    max_lat = max_lon = float('-inf')
    out: Dict[str, dict] = {}
    for tid, pts in path_points.items():
        # Ensure phases order
        pts_sorted = sorted(pts, key=lambda x: phases.index(x[0]) if x[0] in phases else 0)
        for _, lat, lon in pts_sorted:
            if isinstance(lat, (int,float)) and isinstance(lon, (int,float)):
                min_lat = min(min_lat, float(lat)); max_lat = max(max_lat, float(lat))
                min_lon = min(min_lon, float(lon)); max_lon = max(max_lon, float(lon))
        # legs between consecutive points
        leg_seconds: List[float] = []
        leg_minutes: List[float] = []
        for i in range(len(pts_sorted)-1):
            a = pts_sorted[i]; b = pts_sorted[i+1]
            if not (isinstance(a[1], (int,float)) and isinstance(a[2], (int,float)) and isinstance(b[1], (int,float)) and isinstance(b[2], (int,float))):
                leg_seconds.append(0.0); leg_minutes.append(0.0); continue
            if fast:
                d = _haversine_m(float(a[1]), float(a[2]), float(b[1]), float(b[2]))
                minutes = _approx_minutes(d, mode='bike')
                leg_minutes.append(minutes)
                leg_seconds.append(minutes * 60.0)
            else:
                secs = await route_duration_seconds([(float(a[1]), float(a[2])), (float(b[1]), float(b[2]))])
                s = float(secs or 0.0)
                leg_seconds.append(s)
                leg_minutes.append(s / 60.0)
        out[tid] = {
            'points': [ {'phase': ph, 'lat': lat, 'lon': lon} for (ph, lat, lon) in pts_sorted ],
            'leg_seconds': leg_seconds,
            'leg_minutes': leg_minutes,
        }
    if min_lat != float('inf'):
        bounds = {'min_lat': min_lat, 'min_lon': min_lon, 'max_lat': max_lat, 'max_lon': max_lon}
    # include final party location if set on event
    after_party = None
    try:
        pt = (((ev or {}).get('after_party_location') or {}).get('point') or {}).get('coordinates')
        if isinstance(pt, list) and len(pt) == 2 and all(isinstance(x, (int,float)) for x in pt):
            after_party = { 'lat': float(pt[1]), 'lon': float(pt[0]) }
    except Exception:
        after_party = None
    return {'team_paths': out, 'bounds': bounds, 'after_party': after_party}


async def process_refunds(event_id: str, registration_ids: list[str] | None = None) -> dict:
    """Process refunds for the given event.

    If registration_ids is provided, only those registrations are considered.
    Otherwise all cancellable registrations with due refund are processed.
    """
    ev = await _get_event(event_id)
    if not ev or not ev.get('refund_on_cancellation'):
        return {'processed': 0, 'items': [], 'reason': 'refunds_disabled_or_event_missing'}
    fee = int(ev.get('fee_cents') or 0)
    if fee <= 0:
        return {'processed': 0, 'items': [], 'reason': 'no_fee_configured'}
    q: dict = {'event_id': ev['_id'], 'status': {'$in': ['cancelled_by_user','cancelled_admin']}}
    if registration_ids:
        # Lors d'un traitement ciblé inclure aussi les enregistrements déjà marqués 'refunded' pour renvoyer un statut idempotent.
        if 'refunded' not in q['status']['$in']:
            q['status']['$in'].append('refunded')
        oids = []
        for rid in registration_ids:
            try:
                oids.append(ObjectId(rid))
            except Exception:
                continue
        if not oids:
            return {'processed': 0, 'items': [], 'reason': 'invalid_registration_ids'}
        q['_id'] = {'$in': oids}
    # Collect candidates
    candidates: list[dict] = []
    async for r in db_mod.db.registrations.find(q):
        candidates.append(r)
    processed_items: list[dict] = []
    if not candidates:
        return {'processed': 0, 'items': []}
    from . import matching as _self  # self import for helpers if needed
    ev_title = ev.get('title') or 'DinnerHopping Event'
    for reg in candidates:
        reg_id = reg.get('_id')
        team_size = int(reg.get('team_size') or 1)
        amount_cents = fee * team_size
        # locate payment
        pay = await db_mod.db.payments.find_one({'registration_id': reg_id})
        payment_status = (pay or {}).get('status')
        already_refunded = payment_status == 'refunded' or (pay and pay.get('refunded') is True)
        if already_refunded:
            # Ensure registration status is refunded
            if reg.get('status') != 'refunded':
                try:
                    await db_mod.db.registrations.update_one({'_id': reg_id}, {'$set': {'status': 'refunded', 'updated_at': datetime.now(timezone.utc)}})
                except Exception:
                    pass
            processed_items.append({'registration_id': str(reg_id), 'status': 'already_refunded', 'amount_cents': 0})
            continue
        # Compute due
        if not pay:
            processed_items.append({'registration_id': str(reg_id), 'status': 'no_payment_record', 'amount_cents': 0})
            continue
        # Mark payment refunded (simulate provider refund)
        try:
            await db_mod.db.payments.update_one({'_id': pay.get('_id')}, {'$set': {'status': 'refunded', 'refunded': True, 'refund_amount_cents': amount_cents, 'refund_at': datetime.now(timezone.utc)}})
        except Exception:
            processed_items.append({'registration_id': str(reg_id), 'status': 'payment_update_failed', 'amount_cents': 0})
            continue
        # Update registration status
        try:
            await db_mod.db.registrations.update_one({'_id': reg_id}, {'$set': {'status': 'refunded', 'updated_at': datetime.now(timezone.utc)}})
        except Exception:
            pass
        # Send email best-effort
        try:
            email = reg.get('user_email_snapshot')
            if email:
                await send_refund_processed(email, ev_title, amount_cents)
        except Exception:
            pass
        processed_items.append({'registration_id': str(reg_id), 'status': 'refunded', 'amount_cents': amount_cents})
    return {'processed': sum(1 for it in processed_items if it['status'] == 'refunded'), 'items': processed_items}
