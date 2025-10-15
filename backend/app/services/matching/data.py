from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple

from bson.objectid import ObjectId

from ... import db as db_mod
from ...enums import CoursePreference, DietaryPreference, normalized_value
from ...utils import anonymize_public_address as _public_addr  # type: ignore

from ..geocoding import geocode_address

from .config import geocode_missing_enabled, geocode_parallelism

logger = logging.getLogger(__name__)

UserCache = Dict[str, dict]


def _normalize_allergies(values: Iterable[object]) -> List[str]:
    seen = set()
    normalized: List[str] = []
    for value in values or []:  # type: ignore[arg-type]
        if value is None:
            continue
        item = str(value).strip().lower()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _normalize_email(email: Optional[str]) -> Optional[str]:
    if email is None:
        return None
    cleaned = str(email).strip().lower()
    return cleaned or None


async def _get_user(email: Optional[str], cache: Optional[UserCache] = None) -> Optional[dict]:
    if email is None:
        return None
    raw = str(email).strip()
    if not raw:
        return None
    normalized = raw.lower()
    if cache is not None and normalized in cache:
        return cache[normalized]
    user = await db_mod.db.users.find_one({'email': raw})
    if not user and normalized != raw:
        user = await db_mod.db.users.find_one({'email': normalized})
    if user and cache is not None:
        cache[normalized] = user
    return user


async def get_event(event_id: str) -> Optional[dict]:
    try:
        oid = ObjectId(event_id)
    except Exception:
        return None
    return await db_mod.db.events.find_one({'_id': oid})


async def load_registrations(event_oid: ObjectId) -> List[dict]:
    regs: List[dict] = []
    async for r in db_mod.db.registrations.find({
        'event_id': event_oid,
        'status': {'$nin': ['cancelled_by_user', 'cancelled_admin', 'refunded', 'expired']},
    }):
        regs.append(r)
    return regs


async def load_teams(event_oid: ObjectId) -> Dict[str, dict]:
    teams: Dict[str, dict] = {}
    async for t in db_mod.db.teams.find({'event_id': event_oid}):
        teams[str(t['_id'])] = t
    return teams


async def user_profile(email: str, cache: Optional[UserCache] = None) -> Optional[dict]:
    return await _get_user(email, cache)


async def team_location(team: dict, cache: Optional[UserCache] = None, geocode_sem: Optional[asyncio.Semaphore] = None) -> Tuple[Optional[float], Optional[float]]:
    """Return representative (lat, lon) for the given team, geocoding when needed."""
    members = team.get('members') or []
    coords: List[Tuple[float, float]] = []
    for member in members:
        email = member.get('email')
        if not email:
            continue
        user = await _get_user(email, cache)
        if not user:
            continue
        lat = user.get('lat')
        lon = user.get('lon')
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            coords.append((float(lat), float(lon)))
            continue
        if not geocode_missing_enabled():
            continue
        address_struct = user.get('address_struct') or {}
        addr_parts = [
            " ".join([str(address_struct.get('street') or ''), str(address_struct.get('street_no') or '')]).strip(),
            " ".join([str(address_struct.get('postal_code') or ''), str(address_struct.get('city') or '')]).strip(),
        ]
        address = ", ".join([p for p in addr_parts if p]).strip()
        if not address:
            continue
        async def _geocode() -> Optional[Tuple[float, float]]:
            return await geocode_address(address)

        if geocode_sem is None:
            latlon = await _geocode()
        else:
            async with geocode_sem:
                latlon = await _geocode()
        if not latlon:
            continue
        g_lat, g_lon = latlon
        coords.append((g_lat, g_lon))
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            await db_mod.db.users.update_one(
                {'_id': user['_id']},
                {'$set': {'lat': float(g_lat), 'lon': float(g_lon), 'geocoded_at': now}},
            )
            if cache is not None:
                cache[_normalize_email(email) or str(email).lower()] = {
                    **user,
                    'lat': float(g_lat),
                    'lon': float(g_lon),
                    'geocoded_at': now,
                }
        except Exception:
            pass
    if coords:
        lat = sum(c[0] for c in coords) / len(coords)
        lon = sum(c[1] for c in coords) / len(coords)
        return (lat, lon)
    return (None, None)


def team_key(registration: dict) -> str:
    team_id = registration.get('team_id')
    if team_id:
        return str(team_id)
    return f"solo:{str(registration.get('_id'))}"


async def build_teams(event_oid: ObjectId) -> List[dict]:
    start = time.perf_counter()
    registrations = await load_registrations(event_oid)
    teams_docs = await load_teams(event_oid)
    event = await db_mod.db.events.find_one({'_id': event_oid})
    allowed_zips = {
        str(z).strip()
        for z in (event.get('valid_zip_codes') or [])
        if str(z).strip()
    } if event else set()

    emails_for_query: Set[str] = set()
    for registration in registrations:
        email = registration.get('user_email_snapshot')
        if email:
            emails_for_query.add(str(email).strip())
    for team_doc in teams_docs.values():
        for member in (team_doc.get('members') or []):
            email = member.get('email')
            if email:
                emails_for_query.add(str(email).strip())

    user_cache: UserCache = {}
    if emails_for_query:
        async for user in db_mod.db.users.find({'email': {'$in': list(emails_for_query)}}):
            key = _normalize_email(user.get('email'))
            if key:
                user_cache[key] = user

    grouped_regs: Dict[str, List[dict]] = {}
    for registration in registrations:
        grouped_regs.setdefault(team_key(registration), []).append(registration)

    teams: List[dict] = []
    for team_id, regs in grouped_regs.items():
        team_doc = teams_docs.get(team_id) if team_id in teams_docs else None
        member_emails: List[str] = []
        if team_doc and isinstance(team_doc.get('members'), list):
            member_emails = [m.get('email') for m in team_doc['members'] if m.get('email')]
        else:
            member_emails = [r.get('user_email_snapshot') for r in regs if r.get('user_email_snapshot')]
        if allowed_zips:
            if not await _any_email_in_zip(member_emails, allowed_zips, user_cache):
                continue
        size = max(reg.get('team_size') or 1 for reg in regs)
        pref = None
        diet = None
        for reg in regs:
            pref = pref or normalized_value(
                CoursePreference,
                (reg.get('preferences') or {}).get('course_preference')
            )
            diet = diet or normalized_value(DietaryPreference, reg.get('diet'))
        pref = pref or None
        diet = diet or 'omnivore'
        team_entry = {
            'team_id': team_id,
            'member_regs': regs,
            'size': size,
            'course_preference': pref,
            'diet': diet,
            'team_doc': team_doc,
        }
        if team_doc:
            team_entry['team_diet'] = normalized_value(
                DietaryPreference,
                team_doc.get('team_diet'),
                default=team_entry['diet'],
            ) or 'omnivore'
            doc_pref = normalized_value(
                CoursePreference,
                team_doc.get('course_preference'),
                default=team_entry['course_preference'],
            )
            if doc_pref:
                team_entry['course_preference'] = doc_pref
            team_entry['cooking_location'] = team_doc.get('cooking_location') or 'creator'
        else:
            team_entry['team_diet'] = team_entry['diet']
            team_entry['cooking_location'] = 'creator'
        teams.append(team_entry)

    teams.sort(key=lambda entry: entry['team_id'])

    geocode_sem = asyncio.Semaphore(geocode_parallelism())
    location_tasks: List[asyncio.Task[Tuple[Optional[float], Optional[float]]]] = []

    def fallback_members(entry: dict) -> List[dict]:
        regs = entry.get('member_regs') or []
        if not regs:
            return []
        first = regs[0].get('user_email_snapshot')
        return [{'email': first}] if first else []
    for team_entry in teams:
        team_doc = team_entry.get('team_doc') or {'members': fallback_members(team_entry)}
        location_tasks.append(asyncio.create_task(team_location(team_doc, user_cache, geocode_sem)))
    locations = await asyncio.gather(*location_tasks) if location_tasks else []
    for team_entry, loc in zip(teams, locations):
        lat, lon = loc
        team_entry['lat'] = lat
        team_entry['lon'] = lon
        await _augment_capabilities(team_entry, user_cache)
        team_doc = team_entry.get('team_doc') or {'members': fallback_members(team_entry)}
        team_entry['allergies'] = await _collect_team_allergies(team_entry, team_doc, user_cache)
        team_entry['host_allergies'] = await _determine_host_allergies(team_entry, team_doc, user_cache)
        team_entry['_user_cache'] = user_cache
    logger.debug('matching.build_teams teams=%d duration=%.3fs', len(teams), time.perf_counter() - start)
    return teams


async def _any_email_in_zip(emails: List[str], allowed_zips: set[str], cache: UserCache) -> bool:
    for email in {e for e in emails if e}:
        user = await _get_user(email, cache)
        if not user:
            continue
        postal = ((user.get('address_struct') or {}).get('postal_code'))
        if postal and str(postal).strip() in allowed_zips:
            return True
    return False


async def _augment_capabilities(team_entry: dict, cache: UserCache) -> None:
    team_doc = team_entry.get('team_doc') or {}
    members = team_doc.get('members') or []
    can_main = False
    if members:
        if team_entry.get('cooking_location') == 'creator':
            can_main = bool(members[0].get('main_course_possible'))
        elif len(members) > 1:
            can_main = bool(members[1].get('main_course_possible'))
    if not can_main:
        can_main = await _fallback_main_course_capability(team_entry, members, cache)
    team_entry['can_host_main'] = bool(can_main)

    has_kitchen = team_doc.get('has_kitchen')
    if has_kitchen is None:
        has_kitchen = await _fallback_has_kitchen(team_entry, members, cache)
    if has_kitchen is None:
        has_kitchen = bool(can_main)
    team_entry['can_host_any'] = bool(has_kitchen)


async def _fallback_main_course_capability(team_entry: dict, members: List[dict], cache: UserCache) -> bool:
    can_main = False
    for registration in team_entry.get('member_regs') or []:
        prefs = (registration.get('preferences') or {})
        if prefs.get('main_course_possible') is True:
            can_main = True
            break
    if not can_main:
        for registration in team_entry.get('member_regs') or []:
            email = registration.get('user_email_snapshot')
            if not email:
                continue
            user = await _get_user(email, cache)
            if user and user.get('main_course_possible') is True:
                can_main = True
                break
    return bool(can_main)


async def _fallback_has_kitchen(team_entry: dict, members: List[dict], cache: UserCache) -> Optional[bool]:
    for member in members:
        if member.get('kitchen_available') is True:
            return True
    for registration in team_entry.get('member_regs') or []:
        prefs = (registration.get('preferences') or {})
        if prefs.get('kitchen_available') is True:
            return True
    for registration in team_entry.get('member_regs') or []:
        email = registration.get('user_email_snapshot')
        if not email:
            continue
        user = await _get_user(email, cache)
        if user and user.get('kitchen_available') is True:
            return True
    return None


async def user_address_string(email: Optional[str], cache: Optional[UserCache] = None) -> Optional[Tuple[str, str]]:
    if not email:
        return None
    user = await _get_user(email, cache)
    if not user:
        return None
    address_struct = user.get('address_struct') or {}
    street = str(address_struct.get('street') or '').strip()
    street_no = str(address_struct.get('street_no') or '').strip()
    postal_code = str(address_struct.get('postal_code') or '').strip()
    city = str(address_struct.get('city') or '').strip()
    parts = []
    if street:
        parts.append(" ".join([street, street_no]).strip())
    right = " ".join([postal_code, city]).strip()
    if right:
        parts.append(right)
    full = ", ".join(part for part in parts if part)
    public = _public_addr(full) if full else None
    return (full or None, public or None)


async def team_emails_map(event_id: str) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = {}
    async for team in db_mod.db.teams.find({'event_id': ObjectId(event_id)}):
        emails = [member.get('email') for member in (team.get('members') or []) if member.get('email')]
        mapping[str(team['_id'])] = emails
    async for registration in db_mod.db.registrations.find({'event_id': ObjectId(event_id)}):
        if registration.get('team_id'):
            continue
        team_id = f"solo:{str(registration.get('_id'))}"
        email = registration.get('user_email_snapshot')
        if email:
            mapping.setdefault(team_id, []).append(email)
    return mapping


def augment_emails_map_with_splits(base: Dict[str, List[str]], groups: List[dict]) -> Dict[str, List[str]]:
    mapping = dict(base)
    for group in groups:
        ids = [group.get('host_team_id'), *(group.get('guest_team_ids') or [])]
        for team_id in ids:
            if not isinstance(team_id, str):
                continue
            if team_id.startswith('split:'):
                email = team_id.split(':', 1)[1]
                mapping.setdefault(team_id, []).append(email)
            elif team_id.startswith('pair:'):
                part = team_id.split(':', 1)[1]
                emails = [e for e in part.split('+') if e]
                if emails:
                    mapping.setdefault(team_id, []).extend(emails)
    return mapping


async def _collect_team_allergies(team_entry: dict, team_doc: dict, cache: UserCache) -> List[str]:
    allergies: List[str] = []

    def extend(values: Iterable[object]) -> None:
        nonlocal allergies
        merged = _normalize_allergies(values)
        if not merged:
            return
        existing = set(allergies)
        for item in merged:
            if item not in existing:
                allergies.append(item)
                existing.add(item)

    members = (team_doc or {}).get('members') or []
    for member in members:
        extend(member.get('allergies') or [])

    if not allergies:
        for registration in team_entry.get('member_regs') or []:
            extend(registration.get('allergies') or [])

    if not allergies:
        for email in _collect_team_emails(team_entry):
            user = await _get_user(email, cache)
            if user:
                extend(user.get('allergies') or [])
    return allergies


async def _determine_host_allergies(team_entry: dict, team_doc: dict, cache: UserCache) -> List[str]:
    members = (team_doc or {}).get('members') or []
    host_values: List[str] = []

    def extend(values: Iterable[object]) -> List[str]:
        return _normalize_allergies(values)

    if members:
        host_index = 0
        cooking_location = (team_entry.get('cooking_location') or 'creator').lower()
        if cooking_location != 'creator' and len(members) > 1:
            host_index = 1
        try:
            host_values = extend(members[host_index].get('allergies') or [])
        except Exception:
            host_values = []

    if host_values:
        return host_values

    host_emails = _collect_team_emails(team_entry)
    host_email = host_emails[0] if host_emails else None
    if (team_entry.get('cooking_location') or 'creator').lower() != 'creator' and len(host_emails) > 1:
        host_email = host_emails[1]
    if host_email:
        user = await _get_user(host_email, cache)
        if user:
            return _normalize_allergies(user.get('allergies') or [])
    return []


def _collect_team_emails(team_entry: dict) -> List[str]:
    emails: List[str] = []
    team_doc = team_entry.get('team_doc') or {}
    members = team_doc.get('members') or []
    if members:
        for member in members:
            email = member.get('email')
            if email:
                emails.append(str(email).strip())
    else:
        for registration in team_entry.get('member_regs') or []:
            email = registration.get('user_email_snapshot')
            if email:
                emails.append(str(email).strip())
    seen = set()
    ordered: List[str] = []
    for email in emails:
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(email)
    return ordered
