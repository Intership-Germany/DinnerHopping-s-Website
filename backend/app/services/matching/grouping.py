from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ...utils import approx_travel_time_minutes as _approx_minutes  # type: ignore
from ...utils import haversine_m as _haversine_m  # type: ignore

from ..routing import route_duration_seconds

from .config import host_candidate_limit, travel_fast_mode, weight_defaults
from .data import user_address_string


def compatible_diet(host_diet: str, guest_diet: str) -> bool:
    host = (host_diet or 'omnivore').lower()
    guest = (guest_diet or 'omnivore').lower()
    if host == 'omnivore' and guest in ('vegan',):
        return False
    if host == 'vegetarian' and guest == 'vegan':
        return False
    return True


def _normalize_allergies(values: Iterable[object]) -> Set[str]:
    normalized: Set[str] = set()
    for value in values or []:  # type: ignore[arg-type]
        if value is None:
            continue
        item = str(value).strip().lower()
        if item:
            normalized.add(item)
    return normalized


def score_group_phase(host: dict, guests: List[dict], meal: str, weights: dict) -> Tuple[float, List[str], Dict[str, Any]]:
    defaults = weight_defaults()
    score = 0.0
    warnings: List[str] = []
    if (host.get('course_preference') or '').lower() == meal:
        score += weights.get('pref', defaults['pref'])
    if meal == 'main' and not host.get('can_host_main'):
        score -= weights.get('cap_penalty', defaults['cap_penalty'])
        warnings.append('host_cannot_main')
    if meal in ('appetizer', 'dessert') and not host.get('can_host_any', True):
        score -= weights.get('cap_penalty', defaults['cap_penalty'])
        warnings.append('host_no_kitchen')
    host_allergies_set = _normalize_allergies(host.get('host_allergies') or host.get('allergies') or [])
    guest_allergies_union: Set[str] = set()
    guest_allergies_map: Dict[str, List[str]] = {}
    for guest in guests:
        if not compatible_diet(host.get('team_diet'), guest.get('team_diet')):
            score -= weights.get('allergy', defaults['allergy'])
            warnings.append('diet_conflict')
        normalized_guest = _normalize_allergies(guest.get('allergies') or [])
        if normalized_guest:
            guest_allergies_map[str(guest.get('unit_id'))] = sorted(normalized_guest)
            guest_allergies_union.update(normalized_guest)
    uncovered = guest_allergies_union.difference(host_allergies_set)
    if uncovered:
        score -= weights.get('allergy', defaults['allergy']) * len(uncovered)
        warnings.append('allergy_uncovered')
    details = {
        'host_allergies': sorted(host_allergies_set),
        'guest_allergies': guest_allergies_map,
        'guest_allergies_union': sorted(guest_allergies_union),
        'uncovered_allergies': sorted(uncovered),
    }
    return score, warnings, details


async def travel_time_for_phase(host: dict, guests: List[dict]) -> float:
    coords: List[Tuple[float, float]] = []
    for guest in guests:
        if guest.get('lat') is None or guest.get('lon') is None or host.get('lat') is None:
            continue
        coords.append((guest['lat'], guest['lon']))
        coords.append((host['lat'], host['lon']))
    if not coords:
        return 0.0
    total = 0.0
    for index in range(0, len(coords), 2):
        segment = coords[index:index + 2]
        if len(segment) != 2:
            continue
        guest_point, host_point = segment
        if travel_fast_mode():
            distance = _haversine_m(float(guest_point[0]), float(guest_point[1]), float(host_point[0]), float(host_point[1]))
            total += _approx_minutes(distance, mode='bike') * 60.0
        else:
            duration = await route_duration_seconds(segment)
            total += float(duration or 0.0)
    return total


async def phase_groups(
    units: List[dict],
    phase: str,
    used_pairs: Set[Tuple[str, str]],
    weights: Dict[str, float],
    last_at_host: Optional[Dict[str, Tuple[Optional[float], Optional[float]]]] = None,
    after_party_point: Optional[Tuple[float, float]] = None,
) -> List[dict]:
    defaults = weight_defaults()
    last_at_host = last_at_host or {}
    remaining = list(units)
    groups: List[dict] = []
    candidate_limit = host_candidate_limit()

    def can_host(unit: dict) -> bool:
        if phase == 'main':
            return bool(unit.get('can_host_main'))
        return bool(unit.get('can_host_any', True))

    def approx_seconds(a: Tuple[Optional[float], Optional[float]], b: Tuple[Optional[float], Optional[float]]) -> float:
        if not a or not b or a[0] is None or a[1] is None or b[0] is None or b[1] is None:
            return 0.0
        distance = _haversine_m(float(a[0]), float(a[1]), float(b[0]), float(b[1]))
        return _approx_minutes(distance, mode='bike') * 60.0

    def seconds_to_party(point: Tuple[Optional[float], Optional[float]]) -> float:
        if after_party_point is None:
            return 0.0
        return approx_seconds(point, (after_party_point[0], after_party_point[1]))

    while len(remaining) >= 3:
        eligible_hosts = [unit for unit in remaining if can_host(unit)]
        if not eligible_hosts:
            eligible_hosts = list(remaining)
        if candidate_limit > 0:
            candidates = eligible_hosts[:min(candidate_limit, len(eligible_hosts))]
        else:
            candidates = eligible_hosts
        best_choice: Optional[Tuple[float, dict, dict, dict, float, List[str], Tuple[Optional[str], Optional[str]], Dict[str, Any]]] = None
        for host in candidates:
            host_point = (host.get('lat'), host.get('lon'))
            others = [unit for unit in remaining if unit is not host]
            host_email = (host.get('host_emails') or [None])[0]
            host_address: Tuple[Optional[str], Optional[str]] = (None, None)
            if host_email:
                try:
                    address = await user_address_string(host_email)
                    if address:
                        host_address = address
                except Exception:
                    pass
            for i in range(len(others)):
                for j in range(i + 1, len(others)):
                    guest_one = others[i]
                    guest_two = others[j]
                    base_score, warnings, allergy_details = score_group_phase(host, [guest_one, guest_two], phase, weights)
                    duplicate_penalty = _duplicate_penalty(host, guest_one, guest_two, used_pairs, weights, defaults)
                    travel = await travel_time_for_phase(host, [guest_one, guest_two])
                    transition_seconds = _transition_seconds(host_point, last_at_host, host, guest_one, guest_two)
                    party_seconds = _party_penalty_seconds(after_party_point, host_point, phase)
                    order_seconds = _phase_order_penalty(after_party_point, host_point, last_at_host, host, guest_one, guest_two, weights, defaults, phase)
                    score = base_score \
                        - weights.get('dist', defaults['dist']) * float(travel) \
                        - weights.get('trans', defaults['trans']) * float(transition_seconds) \
                        - weights.get('final_party', defaults['final_party']) * float(party_seconds) \
                        - weights.get('phase_order', defaults['phase_order']) * float(order_seconds) \
                        - duplicate_penalty
                    if best_choice is None or score > best_choice[0]:
                        best_choice = (score, host, guest_one, guest_two, travel, warnings, host_address, allergy_details)
        if best_choice is None:
            break
        score_value, host, guest_one, guest_two, travel, warnings, host_addr, allergy_details = best_choice
        group = {
            'phase': phase,
            'host_team_id': host['unit_id'],
            'guest_team_ids': [guest_one['unit_id'], guest_two['unit_id']],
            'score': float(score_value),
            'travel_seconds': float(travel),
            'warnings': sorted(set(warnings)) if warnings else [],
        }
        if host_addr and (host_addr[0] or host_addr[1]):
            group['host_address'] = host_addr[0]
            group['host_address_public'] = host_addr[1]
        if allergy_details.get('host_allergies') is not None:
            group['host_allergies'] = allergy_details.get('host_allergies') or []
        if allergy_details.get('guest_allergies') is not None:
            group['guest_allergies'] = allergy_details.get('guest_allergies') or {}
        if allergy_details.get('guest_allergies_union') is not None:
            group['guest_allergies_union'] = allergy_details.get('guest_allergies_union') or []
        if allergy_details.get('uncovered_allergies') is not None:
            group['uncovered_allergies'] = allergy_details.get('uncovered_allergies') or []
        groups.append(group)
        used_pairs.update(_pairings(host, guest_one, guest_two))
        removed_ids = {host['unit_id'], guest_one['unit_id'], guest_two['unit_id']}
        remaining = [unit for unit in remaining if unit['unit_id'] not in removed_ids]
    return groups


def _phase_order_penalty(after_party_point, host_point, last_at_host, host, guest_one, guest_two, weights, defaults, phase):
    if after_party_point is None or phase not in ('main', 'dessert'):
        return 0.0
    penalty_weight = weights.get('phase_order', defaults['phase_order'])
    if penalty_weight <= 0:
        return 0.0
    d_now = _distance_to_party(after_party_point, host_point)
    total = 0.0
    for unit in (host, guest_one, guest_two):
        prev = last_at_host.get(unit['unit_id'])
        if not prev:
            continue
        d_prev = _distance_to_party(after_party_point, prev)
        if d_now > d_prev:
            total += (d_now - d_prev)
    return total


def _distance_to_party(after_party_point, point: Tuple[Optional[float], Optional[float]]) -> float:
    if point is None or point[0] is None or point[1] is None:
        return 0.0
    return _distance_time(point, (after_party_point[0], after_party_point[1]))


def _party_penalty_seconds(after_party_point, host_point, phase):
    if after_party_point is None or phase != 'dessert':
        return 0.0
    return _distance_time(host_point, (after_party_point[0], after_party_point[1]))


def _transition_seconds(host_point, last_at_host, host, guest_one, guest_two):
    total = 0.0
    for unit in (host, guest_one, guest_two):
        prev = last_at_host.get(unit['unit_id'])
        if prev:
            total += _distance_time(prev, host_point)
    return total


def _distance_time(prev, current):
    if not prev or not current:
        return 0.0
    if prev[0] is None or prev[1] is None or current[0] is None or current[1] is None:
        return 0.0
    dist = _haversine_m(float(prev[0]), float(prev[1]), float(current[0]), float(current[1]))
    return _approx_minutes(dist, mode='bike') * 60.0


def _duplicate_penalty(host, guest_one, guest_two, used_pairs, weights, defaults):
    dup_penalty_weight = weights.get('dup', defaults['dup'])
    penalty = 0.0
    for pair in _pairings(host, guest_one, guest_two):
        if pair in used_pairs:
            penalty += dup_penalty_weight
    return penalty


def _pairings(host, guest_one, guest_two):
    def _pair(a: str, b: str) -> Tuple[str, str]:
        return (a, b) if a <= b else (b, a)
    return {
        _pair(host['unit_id'], guest_one['unit_id']),
        _pair(host['unit_id'], guest_two['unit_id']),
        _pair(guest_one['unit_id'], guest_two['unit_id']),
    }
