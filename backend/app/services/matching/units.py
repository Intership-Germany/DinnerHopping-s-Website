from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ... import db as db_mod
from ...utils import haversine_m as _haversine_m
from .data import user_address_string


def _normalize_email(email: Optional[str]) -> Optional[str]:
    if email is None:
        return None
    cleaned = str(email).strip().lower()
    return cleaned or None


async def build_units_from_teams(teams: List[dict]) -> Tuple[List[dict], Dict[str, List[str]]]:
    units: List[dict] = []
    unit_emails: Dict[str, List[str]] = {}
    address_cache: Dict[str, Optional[Tuple[str, str]]] = {}
    for team in teams:
        team_id = str(team['team_id'])
        emails = _collect_team_emails(team)
        host_emails = _select_host_emails(team, emails)
        user_cache = team.get('_user_cache') if isinstance(team.get('_user_cache'), dict) else None
        host_address_full, host_address_public = await _resolve_host_address(host_emails, user_cache, address_cache)
        unit = {
            'unit_id': team_id,
            'size': int(team.get('size') or max(1, len(emails) or 1)),
            'lat': team.get('lat'),
            'lon': team.get('lon'),
            'team_diet': (team.get('team_diet') or team.get('diet') or 'omnivore'),
            'can_host_main': bool(team.get('can_host_main')),
            'can_host_any': bool(team.get('can_host_any', True)),
            'course_preference': team.get('course_preference'),
            'host_emails': host_emails,
            'allergies': list(team.get('allergies') or []),
            'host_allergies': list(team.get('host_allergies') or team.get('allergies') or []),
            'host_address_full': host_address_full,
            'host_address_public': host_address_public,
            'user_cache_ref': user_cache,
            'member_profiles': list(team.get('member_profiles') or []),
            'gender_mix': list(team.get('gender_mix') or []),
        }
        units.append(unit)
        unit_emails[team_id] = emails
    return units, unit_emails


def _collect_team_emails(team: dict) -> List[str]:
    emails: List[str] = []
    team_doc = team.get('team_doc') or {}
    members = team_doc.get('members') or []
    if members:
        for member in members:
            email = member.get('email')
            if email:
                emails.append(email)
    else:
        for registration in team.get('member_regs') or []:
            email = registration.get('user_email_snapshot')
            if email:
                emails.append(email)
    # deduplicate while preserving order
    seen = set()
    result: List[str] = []
    for email in emails:
        if email in seen:
            continue
        seen.add(email)
        result.append(email)
    return result


def _select_host_emails(team: dict, emails: List[str]) -> List[str]:
    team_doc = team.get('team_doc') or {}
    members = team_doc.get('members') or []
    host_emails: List[str] = []
    try:
        if members:
            if (team.get('cooking_location') or 'creator') == 'creator':
                primary = (members[0] or {}).get('email')
            else:
                primary = (members[1] or {}).get('email') if len(members) > 1 else None
            if primary:
                host_emails.append(primary)
    except Exception:
        host_emails = []
    if not host_emails and emails:
        host_emails = [emails[0]]
    return host_emails


async def _resolve_host_address(
    host_emails: List[str],
    user_cache: Optional[Dict[str, dict]],
    address_cache: Dict[str, Optional[Tuple[str, str]]],
) -> Tuple[Optional[str], Optional[str]]:
    if not host_emails:
        return (None, None)
    for email in host_emails:
        if not email:
            continue
        key = email.strip().lower()
        if key not in address_cache:
            address_cache[key] = await user_address_string(email, cache=user_cache)
        cached = address_cache.get(key)
        if cached:
            return cached
    return (None, None)


def emails_to_unit_index(units: List[dict], unit_emails: Dict[str, List[str]]) -> Dict[str, List[str]]:
    index: Dict[str, List[str]] = {}
    for unit in units:
        unit_id = unit['unit_id']
        for email in unit_emails.get(unit_id, []):
            key = str(email).lower()
            index.setdefault(key, []).append(unit_id)
    return index


def _diet_merge(a: Optional[str], b: Optional[str]) -> str:
    order = {'vegan': 2, 'vegetarian': 1, 'omnivore': 0}
    rev = {v: k for k, v in order.items()}
    a_val = order.get((a or 'omnivore').lower(), 0)
    b_val = order.get((b or 'omnivore').lower(), 0)
    return rev[max(a_val, b_val)]


def merge_two_solos(ua: dict, ub: dict, emails: Tuple[str, str]) -> dict:
    email_a = _normalize_email(emails[0])
    email_b = _normalize_email(emails[1])
    participants = [value for value in [email_a, email_b] if value]
    lat: Optional[float]
    lon: Optional[float]
    if all(isinstance(ua.get(key), (int, float)) for key in ('lat', 'lon')) and all(isinstance(ub.get(key), (int, float)) for key in ('lat', 'lon')):
        lat = (float(ua['lat']) + float(ub['lat'])) / 2.0
        lon = (float(ua['lon']) + float(ub['lon'])) / 2.0
    else:
        lat = ua.get('lat') or ub.get('lat')
        lon = ua.get('lon') or ub.get('lon')
    gender_mix = sorted(set(list(ua.get('gender_mix') or []) + list(ub.get('gender_mix') or [])))
    member_profiles = list(ua.get('member_profiles') or []) + list(ub.get('member_profiles') or [])

    can_host_any = bool(ua.get('can_host_any') or ub.get('can_host_any'))
    can_host_main = bool(ua.get('can_host_main') or ub.get('can_host_main')) if can_host_any else False

    host_priority: List[str] = []
    for unit in (ua, ub):
        if not unit.get('can_host_any'):
            continue
        for candidate in unit.get('host_emails') or []:
            normalized = _normalize_email(candidate)
            if normalized and normalized not in host_priority:
                host_priority.append(normalized)
    for participant in participants:
        if participant not in host_priority:
            host_priority.append(participant)
    if not host_priority:
        host_priority = participants

    unit_id = f"pair:{'+'.join(sorted(participants))}" if len(participants) == 2 else f"pair:{participants[0]}"

    return {
        'unit_id': unit_id,
        'size': 2,
        'lat': lat,
        'lon': lon,
        'team_diet': _diet_merge(ua.get('team_diet'), ub.get('team_diet')),
        'can_host_main': can_host_main,
        'can_host_any': can_host_any,
        'course_preference': ua.get('course_preference') or ub.get('course_preference'),
        'host_emails': host_priority,
        'allergies': sorted(set(list(ua.get('allergies') or []) + list(ub.get('allergies') or []))),
        'host_allergies': sorted(set(list(ua.get('host_allergies') or []) + list(ub.get('host_allergies') or []))),
        'gender_mix': gender_mix,
        'member_profiles': member_profiles,
        'paired_from': sorted({str(ua.get('unit_id')), str(ub.get('unit_id'))}),
    }


def _is_pair_candidate(unit: dict, unit_emails: Dict[str, List[str]]) -> bool:
    uid = unit.get('unit_id')
    if not isinstance(uid, str):
        return False
    if uid.startswith('split:') or uid.startswith('pair:'):
        return False
    if not uid.startswith('solo:'):
        return False
    try:
        if int(unit.get('size') or 0) != 1:
            return False
    except Exception:
        return False
    emails = unit_emails.get(uid, [])
    return len(emails) == 1


def _primary_gender(unit: dict) -> Optional[str]:
    for profile in unit.get('member_profiles') or []:
        gender = profile.get('gender')
        if gender:
            normalized = str(gender).strip().lower()
            if normalized:
                return normalized
    for entry in unit.get('gender_mix') or []:
        gender = str(entry).strip().lower()
        if gender:
            return gender
    return None


def _course_key(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _diet_rank(value: Optional[str]) -> int:
    mapping = {'omnivore': 0, 'vegetarian': 1, 'vegan': 2}
    return mapping.get(str(value or '').strip().lower(), 0)


def _collect_allergies(unit: dict) -> set[str]:
    collected: set[str] = set()
    for source in ('allergies', 'host_allergies'):
        for item in unit.get(source) or []:
            if item is None:
                continue
            val = str(item).strip().lower()
            if val:
                collected.add(val)
    for profile in unit.get('member_profiles') or []:
        for item in profile.get('allergies') or []:
            if item is None:
                continue
            val = str(item).strip().lower()
            if val:
                collected.add(val)
    return collected


def _distance_between_units(ua: dict, ub: dict) -> Optional[float]:
    try:
        lat_a = ua.get('lat')
        lon_a = ua.get('lon')
        lat_b = ub.get('lat')
        lon_b = ub.get('lon')
        if None in (lat_a, lon_a, lat_b, lon_b):
            return None
        return float(_haversine_m(float(lat_a), float(lon_a), float(lat_b), float(lon_b)))
    except Exception:
        return None


def _score_pairing(ua: dict, ub: dict) -> float:
    if not (ua.get('can_host_any') or ub.get('can_host_any')):
        return float('-inf')

    score = 10.0

    if ua.get('can_host_any') and ub.get('can_host_any'):
        score += 1.0
    if ua.get('can_host_main') or ub.get('can_host_main'):
        score += 3.0

    gender_a = _primary_gender(ua)
    gender_b = _primary_gender(ub)
    if gender_a and gender_b:
        if gender_a != gender_b:
            score += 2.0
        else:
            score += 0.5
    else:
        score += 0.2

    course_a = _course_key(ua.get('course_preference'))
    course_b = _course_key(ub.get('course_preference'))
    if course_a and course_b:
        if course_a == course_b:
            score += 0.8
        else:
            score += 0.2
    else:
        score += 0.1

    diet_penalty = abs(_diet_rank(ua.get('team_diet')) - _diet_rank(ub.get('team_diet'))) * 0.5
    score -= diet_penalty

    allergies_union = _collect_allergies(ua) | _collect_allergies(ub)
    score -= 0.15 * len(allergies_union)

    distance = _distance_between_units(ua, ub)
    if distance is not None:
        score -= min(distance / 1000.0, 40.0) * 0.1

    return float(score)


def auto_pair_solos(
    units: List[dict],
    unit_emails: Dict[str, List[str]],
    *,
    min_score: Optional[float] = None,
) -> Tuple[List[dict], Dict[str, List[str]], List[dict]]:
    candidates = [unit for unit in units if _is_pair_candidate(unit, unit_emails)]
    if len(candidates) < 2:
        return units, dict(unit_emails), []

    candidates.sort(key=lambda item: str(item.get('unit_id')))
    pair_options: List[Tuple[float, dict, dict]] = []
    for idx in range(len(candidates)):
        ua = candidates[idx]
        email_a = unit_emails.get(str(ua.get('unit_id')), [])
        if len(email_a) != 1:
            continue
        for jdx in range(idx + 1, len(candidates)):
            ub = candidates[jdx]
            email_b = unit_emails.get(str(ub.get('unit_id')), [])
            if len(email_b) != 1:
                continue
            score = _score_pairing(ua, ub)
            if score == float('-inf'):
                continue
            pair_options.append((score, ua, ub))

    if not pair_options:
        return units, dict(unit_emails), []

    pair_options.sort(key=lambda item: (item[0], str(item[1].get('unit_id')), str(item[2].get('unit_id'))), reverse=True)

    used: set[str] = set()
    additions: List[dict] = []
    details: List[dict] = []
    threshold = min_score if min_score is not None else float('-inf')

    for score, ua, ub in pair_options:
        uid_a = str(ua.get('unit_id'))
        uid_b = str(ub.get('unit_id'))
        if uid_a in used or uid_b in used:
            continue
        if score < threshold:
            continue
        emails = (
            unit_emails.get(uid_a, [None])[0],
            unit_emails.get(uid_b, [None])[0],
        )
        merged = merge_two_solos(ua, ub, emails)  # type: ignore[arg-type]
        additions.append(merged)
        details.append({
            'unit_id': merged['unit_id'],
            'source_units': sorted([uid_a, uid_b]),
            'score': round(float(score), 3),
            'can_host_any': bool(merged.get('can_host_any')),
            'can_host_main': bool(merged.get('can_host_main')),
        })
        used.add(uid_a)
        used.add(uid_b)

    if not additions:
        return units, dict(unit_emails), []

    updated_units: List[dict] = []
    for unit in units:
        uid = str(unit.get('unit_id'))
        if uid in used:
            continue
        updated_units.append(unit)
    updated_units.extend(additions)

    updated_map: Dict[str, List[str]] = {}
    for unit in updated_units:
        uid = str(unit['unit_id'])
        if uid.startswith('pair:'):
            part = uid.split(':', 1)[1]
            emails = [value for value in part.split('+') if value]
            updated_map[uid] = emails
        else:
            updated_map[uid] = list(unit_emails.get(uid, []))

    return updated_units, updated_map, details


def apply_forced_pairs(units: List[dict], unit_emails: Dict[str, List[str]], forced_pairs: List[dict]) -> Tuple[List[dict], Dict[str, List[str]]]:
    if not forced_pairs:
        return units, unit_emails
    email_index = emails_to_unit_index(units, unit_emails)
    by_id = {unit['unit_id']: unit for unit in units}
    removed = set()
    additions: List[dict] = []
    for pair in forced_pairs:
        a = (pair.get('a_email') or '').lower()
        b = (pair.get('b_email') or '').lower()
        if not a or not b or a == b:
            continue
        unit_ids_a = [uid for uid in email_index.get(a, []) if by_id.get(uid, {}).get('size') == 1]
        unit_ids_b = [uid for uid in email_index.get(b, []) if by_id.get(uid, {}).get('size') == 1]
        if not unit_ids_a or not unit_ids_b:
            continue
        ua = by_id.get(unit_ids_a[0])
        ub = by_id.get(unit_ids_b[0])
        if not ua or not ub or ua['unit_id'] in removed or ub['unit_id'] in removed:
            continue
        additions.append(merge_two_solos(ua, ub, (a, b)))
        removed.add(ua['unit_id'])
        removed.add(ub['unit_id'])
    if not additions and not removed:
        return units, unit_emails
    updated_units = [unit for unit in units if unit['unit_id'] not in removed] + additions
    updated_map: Dict[str, List[str]] = {}
    for unit in updated_units:
        uid = unit['unit_id']
        if uid.startswith('pair:'):
            part = uid.split(':', 1)[1]
            emails = [email for email in part.split('+') if email]
            updated_map[uid] = emails
        else:
            updated_map[uid] = list(unit_emails.get(uid, []))
    return updated_units, updated_map


def apply_required_splits(units: List[dict], unit_emails: Dict[str, List[str]], split_team_ids: List[str]) -> Tuple[List[dict], Dict[str, List[str]]]:
    if not split_team_ids:
        return units, unit_emails
    by_id = {unit['unit_id']: unit for unit in units}
    removed = set()
    new_units: List[dict] = []
    mapping = dict(unit_emails)
    for team_id in split_team_ids:
        unit = by_id.get(str(team_id))
        if not unit:
            continue
        emails = list(unit_emails.get(unit['unit_id'], []))
        if len(emails) <= 1:
            continue
        removed.add(unit['unit_id'])
        mapping.pop(unit['unit_id'], None)
        origin_id = unit['unit_id']
        for email in emails:
            unit_id = f'split:{email.lower()}'
            new_unit = {
                'unit_id': unit_id,
                'size': 1,
                'lat': unit.get('lat'),
                'lon': unit.get('lon'),
                'team_diet': unit.get('team_diet') or 'omnivore',
                # Split members cannot host alone; keep them as guests only.
                'can_host_main': False,
                'can_host_any': False,
                'course_preference': None,
                'host_emails': [email],
                'allergies': list(unit.get('allergies') or []),
                'host_allergies': list(unit.get('host_allergies') or unit.get('allergies') or []),
                'split_origin': origin_id,
            }
            new_units.append(new_unit)
            mapping[unit_id] = [email]
    kept = [unit for unit in units if unit['unit_id'] not in removed]
    return kept + new_units, mapping


async def apply_minimal_splits(units: List[dict], unit_emails: Dict[str, List[str]]) -> Tuple[List[dict], Dict[str, List[str]]]:
    remainder = len(units) % 3
    if remainder == 0:
        return units, unit_emails
    needed = (3 - remainder) % 3
    if needed == 0:
        return units, unit_emails

    base_units = list(units)
    base_mapping = dict(unit_emails)

    candidates: List[Tuple[dict, List[str]]] = []
    for unit in units:
        unit_id = unit['unit_id']
        if isinstance(unit_id, str) and (unit_id.startswith('split:') or unit_id.startswith('pair:')):
            continue
        emails = list(unit_emails.get(unit_id, []))
        # Only split teams of exactly two members to keep duos together later.
        if len(emails) == 2:
            candidates.append((unit, emails))
    candidates.sort(key=lambda item: str(item[0]['unit_id']))

    new_units: List[dict] = []
    removed = set()
    mapping = dict(unit_emails)
    splits_applied = 0

    for unit, emails in candidates:
        if splits_applied >= needed:
            break
        unit_id = unit['unit_id']
        if unit_id in removed:
            continue
        removed.add(unit_id)
        mapping.pop(unit_id, None)
        origin_id = unit_id
        for email in emails:
            lat, lon = await _resolve_coords_from_user(email, unit)
            new_unit_id = f'split:{email.lower()}'
            new_unit = {
                'unit_id': new_unit_id,
                'size': 1,
                'lat': lat,
                'lon': lon,
                'team_diet': unit.get('team_diet') or 'omnivore',
                # Split members should not host separately.
                'can_host_main': False,
                'can_host_any': False,
                'course_preference': None,
                'host_emails': [email],
                'allergies': list(unit.get('allergies') or []),
                'host_allergies': list(unit.get('host_allergies') or unit.get('allergies') or []),
                'split_origin': origin_id,
            }
            new_units.append(new_unit)
            mapping[new_unit_id] = [email]
        splits_applied += 1

    if splits_applied < needed:
        # Revert to original units if we cannot reach the required count.
        return base_units, base_mapping

    kept = [unit for unit in units if unit['unit_id'] not in removed]
    return kept + new_units, mapping


async def _resolve_coords_from_user(email: str, unit: dict) -> Tuple[Optional[float], Optional[float]]:
    lat = unit.get('lat')
    lon = unit.get('lon')
    try:
        user = await db_mod.db.users.find_one({'email': email})
    except Exception:
        user = None
    if user and isinstance(user.get('lat'), (int, float)) and isinstance(user.get('lon'), (int, float)):
        lat = float(user['lat'])
        lon = float(user['lon'])
    return lat, lon


def group_units_in_triads(units: List[dict]) -> List[List[dict]]:
    duos = [unit for unit in units if int(unit.get('size') or 1) >= 2]
    solos = [unit for unit in units if int(unit.get('size') or 1) == 1]
    groups: List[List[dict]] = []
    while len(duos) >= 3:
        groups.append([duos.pop(0), duos.pop(0), duos.pop(0)])
    while len(duos) >= 1 and len(solos) >= 2:
        groups.append([duos.pop(0), solos.pop(0), solos.pop(0)])
    while len(duos) >= 2 and len(solos) >= 1:
        groups.append([duos.pop(0), duos.pop(0), solos.pop(0)])
    while len(solos) >= 3:
        groups.append([solos.pop(0), solos.pop(0), solos.pop(0)])
    remaining = duos + solos
    if remaining:
        while remaining:
            group: List[dict] = []
            for _ in range(3):
                if remaining:
                    group.append(remaining.pop(0))
            if group:
                groups.append(group)
    return groups
