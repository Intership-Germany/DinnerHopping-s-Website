from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ...utils import approx_travel_time_minutes as _approx_minutes  # type: ignore
from ...utils import haversine_m as _haversine_m  # type: ignore

from ..routing import route_duration_seconds

from .config import (
    guest_candidate_limit,
    host_candidate_limit,
    routing_parallelism,
    travel_fast_mode,
    weight_defaults,
)
from .data import user_address_string

logger = logging.getLogger(__name__)


class TravelTimeResolver:
    """Cache and parallelize travel time computations for host/guest triads."""

    def __init__(self, *, fast_mode: bool, parallelism: int) -> None:
        self._fast_mode = fast_mode
        self._parallelism = max(1, parallelism)
        self._sem = asyncio.Semaphore(self._parallelism)
        self._cache: Dict[tuple[str, tuple[str, str]], float] = {}

    def make_key(self, host: dict | str, guest_one: dict | str, guest_two: dict | str) -> tuple[str, tuple[str, str]]:
        host_id = _unit_identifier(host)
        guest_a = _unit_identifier(guest_one)
        guest_b = _unit_identifier(guest_two)
        pair = (guest_a, guest_b) if guest_a <= guest_b else (guest_b, guest_a)
        return (host_id, pair)

    async def batch_resolve(self, requests: Iterable[tuple[tuple[str, tuple[str, str]], dict, dict, dict]]) -> None:
        pending: List[asyncio.Task[None]] = []
        for key, host, guest_one, guest_two in requests:
            if key in self._cache:
                continue
            pending.append(asyncio.create_task(self._compute_and_store(key, host, guest_one, guest_two)))
        if pending:
            await asyncio.gather(*pending)

    async def resolve(self, host: dict, guest_one: dict, guest_two: dict) -> float:
        key = self.make_key(host, guest_one, guest_two)
        await self.batch_resolve([(key, host, guest_one, guest_two)])
        return self.get(key)

    def get(self, key: tuple[str, tuple[str, str]]) -> float:
        return float(self._cache.get(key, 0.0))

    async def _compute_and_store(self, key: tuple[str, tuple[str, str]], host: dict, guest_one: dict, guest_two: dict) -> None:
        start = time.perf_counter()
        try:
            value = await _compute_travel_seconds(host, guest_one, guest_two, self._fast_mode, self._sem)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception('matching.travel_time computation failed for key=%s', key)
            value = 0.0
        self._cache[key] = float(value)
        logger.debug('matching.travel_time cached key=%s duration=%.3fs', key, time.perf_counter() - start)


def _unit_identifier(unit: dict | str) -> str:
    if isinstance(unit, str):
        return unit
    return str(unit.get('unit_id'))


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


async def _compute_travel_seconds(
    host: dict,
    guest_one: dict,
    guest_two: dict,
    fast_mode: bool,
    sem: asyncio.Semaphore,
) -> float:
    results = await asyncio.gather(
        _compute_guest_host_seconds(host, guest_one, fast_mode, sem),
        _compute_guest_host_seconds(host, guest_two, fast_mode, sem),
    )
    return float(sum(results))


async def _compute_guest_host_seconds(host: dict, guest: dict, fast_mode: bool, sem: asyncio.Semaphore) -> float:
    guest_lat = guest.get('lat')
    guest_lon = guest.get('lon')
    host_lat = host.get('lat')
    host_lon = host.get('lon')
    if any(value is None for value in (guest_lat, guest_lon, host_lat, host_lon)):
        return 0.0
    if fast_mode:
        distance = _haversine_m(float(guest_lat), float(guest_lon), float(host_lat), float(host_lon))
        return _approx_minutes(distance, mode='bike') * 60.0
    async with sem:
        duration = await route_duration_seconds([(float(guest_lat), float(guest_lon)), (float(host_lat), float(host_lon))])
    return float(duration or 0.0)


async def travel_time_for_phase(host: dict, guests: List[dict]) -> float:
    if not guests:
        return 0.0
    start = time.perf_counter()
    fast = travel_fast_mode()
    sem = asyncio.Semaphore(routing_parallelism())
    tasks = [_compute_guest_host_seconds(host, guest, fast, sem) for guest in guests]
    if not tasks:
        return 0.0
    durations = await asyncio.gather(*tasks)
    total = float(sum(durations))
    logger.debug('matching.travel_time_for_phase guests=%d duration=%.3fs', len(guests), time.perf_counter() - start)
    return total


async def phase_groups(
    units: List[dict],
    phase: str,
    used_pairs: Set[Tuple[str, str]],
    weights: Dict[str, float],
    last_at_host: Optional[Dict[str, Tuple[Optional[float], Optional[float]]]] = None,
    after_party_point: Optional[Tuple[float, float]] = None,
    travel_resolver: Optional[TravelTimeResolver] = None,
    candidate_guest_limit: Optional[int] = None,
    distance_cache: Optional[Dict[Tuple[str, str], float]] = None,
    host_usage: Optional[Dict[str, int]] = None,
    host_limit: Optional[int] = 1,
) -> List[dict]:
    start = time.perf_counter()
    defaults = weight_defaults()
    last_at_host = last_at_host or {}
    remaining = list(units)
    groups: List[dict] = []
    candidate_limit = host_candidate_limit()
    travel_resolver = travel_resolver or TravelTimeResolver(
        fast_mode=travel_fast_mode(),
        parallelism=routing_parallelism(),
    )
    guest_limit = candidate_guest_limit if candidate_guest_limit is not None else guest_candidate_limit()
    distance_cache = distance_cache or {}
    host_usage = host_usage or {}
    split_groups: Dict[str, Set[str]] = {}
    for unit in remaining:
        origin = unit.get('split_origin')
        if not origin:
            continue
        split_groups.setdefault(origin, set()).add(unit['unit_id'])
    split_groups = {key: value for key, value in split_groups.items() if len(value) > 1}

    def violates_split(selection: Iterable[dict]) -> bool:
        if not split_groups:
            return False
        seen: Dict[str, Set[str]] = {}
        for unit in selection:
            origin = unit.get('split_origin')
            if not origin:
                continue
            seen.setdefault(origin, set()).add(unit['unit_id'])
        for origin, chosen in seen.items():
            if chosen != split_groups.get(origin, set()):
                return True
        return False

    def can_host(unit: dict) -> bool:
        if phase == 'main':
            allowed = bool(unit.get('can_host_main'))
        else:
            allowed = bool(unit.get('can_host_any', True))
        if not allowed:
            return False
        if host_limit is not None and host_limit >= 0:
            if host_usage.get(unit['unit_id'], 0) >= host_limit:
                return False
        return True

    def base_can_host(unit: dict) -> bool:
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
        fallback_mode = False
        if not eligible_hosts:
            fallback_candidates = [unit for unit in remaining if base_can_host(unit)]
            if fallback_candidates:
                fallback_mode = True
                fallback_candidates.sort(key=lambda unit: host_usage.get(unit['unit_id'], 0))
                eligible_hosts = fallback_candidates
            else:
                eligible_hosts = list(remaining)
        if candidate_limit > 0:
            candidates = eligible_hosts[:min(candidate_limit, len(eligible_hosts))]
        else:
            candidates = eligible_hosts
        combo_entries: List[Dict[str, Any]] = []
        travel_requests: List[tuple[tuple[str, tuple[str, str]], dict, dict, dict]] = []
        for host in candidates:
            host_point = (host.get('lat'), host.get('lon'))
            others = [unit for unit in remaining if unit is not host]
            if guest_limit and guest_limit > 0 and len(others) > guest_limit:
                others = _limit_guests_by_distance(host, others, guest_limit, distance_cache)
            if len(others) < 2:
                continue
            host_address = await _host_address_for_unit(host)
            for i in range(len(others)):
                for j in range(i + 1, len(others)):
                    guest_one = others[i]
                    guest_two = others[j]
                    if violates_split([host, guest_one, guest_two]):
                        continue
                    base_score, warnings, allergy_details = score_group_phase(host, [guest_one, guest_two], phase, weights)
                    duplicate_penalty = _duplicate_penalty(host, guest_one, guest_two, used_pairs, weights, defaults)
                    transition_seconds = _transition_seconds(host_point, last_at_host, host, guest_one, guest_two)
                    party_seconds = _party_penalty_seconds(after_party_point, host_point, phase)
                    order_seconds = _phase_order_penalty(after_party_point, host_point, last_at_host, host, guest_one, guest_two, weights, defaults, phase)
                    key = travel_resolver.make_key(host, guest_one, guest_two)
                    combo_warnings = list(warnings)
                    if fallback_mode or (host_limit is not None and host_usage.get(host['unit_id'], 0) >= host_limit):
                        combo_warnings.append('host_reuse')
                    combo_entries.append({
                        'host': host,
                        'guest_one': guest_one,
                        'guest_two': guest_two,
                        'base_score': base_score,
                        'warnings': warnings,
                        'warnings_extended': combo_warnings,
                        'allergy_details': allergy_details,
                        'duplicate_penalty': duplicate_penalty,
                        'transition_seconds': float(transition_seconds),
                        'party_seconds': float(party_seconds),
                        'order_seconds': float(order_seconds),
                        'host_address': host_address,
                        'key': key,
                    })
                    travel_requests.append((key, host, guest_one, guest_two))
        if not combo_entries:
            break
        await travel_resolver.batch_resolve(travel_requests)
        best_choice = await asyncio.to_thread(
            _select_best_candidate,
            combo_entries,
            weights,
            defaults,
            travel_resolver,
        )
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
    logger.debug('matching.phase_groups[%s] groups=%d duration=%.3fs', phase, len(groups), time.perf_counter() - start)
    return groups


def _limit_guests_by_distance(host: dict, candidates: List[dict], limit: int, cache: Dict[Tuple[str, str], float]) -> List[dict]:
    if limit <= 0 or len(candidates) <= limit:
        return candidates
    scored: List[Tuple[float, dict]] = []
    for other in candidates:
        distance = _cached_distance_seconds(host, other, cache)
        scored.append((distance, other))
    scored.sort(key=lambda item: item[0])
    return [unit for _, unit in scored[:limit]]


def _cached_distance_seconds(host: dict, other: dict, cache: Dict[Tuple[str, str], float]) -> float:
    key = _distance_cache_key(host, other)
    if key not in cache:
        host_point = (host.get('lat'), host.get('lon'))
        other_point = (other.get('lat'), other.get('lon'))
        if any(value is None for value in host_point + other_point):
            cache[key] = float('inf')
        else:
            cache[key] = _distance_time(host_point, other_point)
    return cache[key]


def _distance_cache_key(host: dict, other: dict) -> Tuple[str, str]:
    a = _unit_identifier(host)
    b = _unit_identifier(other)
    return (a, b) if a <= b else (b, a)


async def _host_address_for_unit(unit: dict) -> Tuple[Optional[str], Optional[str]]:
    full = unit.get('host_address_full')
    public = unit.get('host_address_public')
    if full or public:
        return (full, public)
    host_email = (unit.get('host_emails') or [None])[0]
    if not host_email:
        return (None, None)
    try:
        user_cache = unit.get('user_cache_ref') if isinstance(unit.get('user_cache_ref'), dict) else None
        address = await user_address_string(host_email, cache=user_cache)
    except Exception:
        logger.debug('matching.host_address lookup failed for %s', host_email, exc_info=True)
        return (None, None)
    if not address:
        return (None, None)
    unit['host_address_full'] = address[0]
    unit['host_address_public'] = address[1]
    return address[0], address[1]


def _select_best_candidate(
    entries: List[Dict[str, Any]],
    weights: Dict[str, float],
    defaults: Dict[str, float],
    travel_resolver: TravelTimeResolver,
) -> Optional[Tuple[float, dict, dict, dict, float, List[str], Tuple[Optional[str], Optional[str]], Dict[str, Any]]]:
    best: Optional[Tuple[float, dict, dict, dict, float, List[str], Tuple[Optional[str], Optional[str]], Dict[str, Any]]] = None
    for entry in entries:
        travel = travel_resolver.get(entry['key'])
        score = entry['base_score'] \
            - weights.get('dist', defaults['dist']) * float(travel) \
            - weights.get('trans', defaults['trans']) * float(entry['transition_seconds']) \
            - weights.get('final_party', defaults['final_party']) * float(entry['party_seconds']) \
            - weights.get('phase_order', defaults['phase_order']) * float(entry['order_seconds']) \
            - float(entry['duplicate_penalty'])
        if best is None or score > best[0]:
            best = (
                float(score),
                entry['host'],
                entry['guest_one'],
                entry['guest_two'],
                float(travel),
                list(entry.get('warnings_extended') or entry.get('warnings') or []),
                entry.get('host_address') or (None, None),
                entry.get('allergy_details') or {},
            )
    return best


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
