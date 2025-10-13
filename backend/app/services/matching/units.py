from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ... import db as db_mod


async def build_units_from_teams(teams: List[dict]) -> Tuple[List[dict], Dict[str, List[str]]]:
    units: List[dict] = []
    unit_emails: Dict[str, List[str]] = {}
    for team in teams:
        team_id = str(team['team_id'])
        emails = _collect_team_emails(team)
        host_emails = _select_host_emails(team, emails)
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
    a, b = sorted([emails[0].lower(), emails[1].lower()])
    lat = None
    lon = None
    if all(isinstance(ua.get(key), (int, float)) for key in ('lat', 'lon')) and all(isinstance(ub.get(key), (int, float)) for key in ('lat', 'lon')):
        lat = (float(ua['lat']) + float(ub['lat'])) / 2.0
        lon = (float(ua['lon']) + float(ub['lon'])) / 2.0
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
        'allergies': sorted(set(list(ua.get('allergies') or []) + list(ub.get('allergies') or []))),
        'host_allergies': sorted(set(list(ua.get('host_allergies') or []) + list(ub.get('host_allergies') or []))),
    }


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
        for email in emails:
            unit_id = f'split:{email.lower()}'
            new_unit = {
                'unit_id': unit_id,
                'size': 1,
                'lat': unit.get('lat'),
                'lon': unit.get('lon'),
                'team_diet': unit.get('team_diet') or 'omnivore',
                'can_host_main': bool(unit.get('can_host_main')),
                'can_host_any': bool(unit.get('can_host_any')),
                'course_preference': None,
                'host_emails': [email],
                'allergies': list(unit.get('allergies') or []),
                'host_allergies': list(unit.get('host_allergies') or unit.get('allergies') or []),
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
    candidates: List[Tuple[dict, List[str]]] = []
    for unit in units:
        unit_id = unit['unit_id']
        if isinstance(unit_id, str) and (unit_id.startswith('split:') or unit_id.startswith('pair:')):
            continue
        emails = list(unit_emails.get(unit_id, []))
        if len(emails) >= 2:
            candidates.append((unit, emails))
    candidates.sort(key=lambda item: len(item[1]))
    new_units: List[dict] = []
    removed = set()
    mapping = dict(unit_emails)
    for unit, emails in candidates:
        if needed <= 0:
            break
        unit_id = unit['unit_id']
        if unit_id in removed:
            continue
        if needed >= 2 and len(emails) >= 3:
            split_count = 3
            reduction = 2
        else:
            split_count = 2
            reduction = 1
        removed.add(unit_id)
        mapping.pop(unit_id, None)
        for email in emails[:split_count]:
            lat, lon = await _resolve_coords_from_user(email, unit)
            new_unit = {
                'unit_id': f'split:{email.lower()}',
                'size': 1,
                'lat': lat,
                'lon': lon,
                'team_diet': unit.get('team_diet') or 'omnivore',
                'can_host_main': bool(unit.get('can_host_main')),
                'can_host_any': bool(unit.get('can_host_any')),
                'course_preference': None,
                'host_emails': [email],
                'allergies': list(unit.get('allergies') or []),
                'host_allergies': list(unit.get('host_allergies') or unit.get('allergies') or []),
            }
            new_units.append(new_unit)
            mapping[new_unit['unit_id']] = [email]
        needed -= reduction
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
