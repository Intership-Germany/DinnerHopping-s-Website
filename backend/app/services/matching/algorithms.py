from __future__ import annotations

import random
from typing import Dict, List, Optional, Set, Tuple

from bson.objectid import ObjectId

from ... import db as db_mod

from .config import algorithm_seed, phases, weight_defaults
from .data import build_teams, get_event
from .grouping import phase_groups
from .metrics import compute_metrics
from .units import (
    apply_forced_pairs,
    apply_minimal_splits,
    apply_required_splits,
    build_units_from_teams,
)


async def algo_greedy(event_oid: ObjectId, weights: Dict[str, float], seed: Optional[int] = None) -> dict:
    teams = await build_teams(event_oid)
    units, unit_emails = await build_units_from_teams(teams)
    event = await db_mod.db.events.find_one({'_id': event_oid})
    event_id_str = str(event.get('_id')) if event else None
    if event_id_str:
        constraints = await _load_constraints(event_id_str)
        units, unit_emails = apply_forced_pairs(units, unit_emails, constraints.get('forced_pairs') or [])
        units, unit_emails = apply_required_splits(units, unit_emails, constraints.get('split_team_ids') or [])
    units, unit_emails = await apply_minimal_splits(units, unit_emails)
    random_instance = random.Random(seed if seed is not None else algorithm_seed('greedy', 42))
    random_instance.shuffle(units)
    used_pairs: Set[Tuple[str, str]] = set()
    all_groups: List[dict] = []
    last_at: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    party_point = _after_party_point(event)
    for index, phase in enumerate(phases()[:3]):
        if index > 0:
            units = units[1:] + units[:1]
        groups = await phase_groups(
            units,
            phase,
            used_pairs,
            weights,
            last_at_host=last_at,
            after_party_point=(party_point if phase == 'dessert' else None),
        )
        all_groups.extend(groups)
        _update_last_locations(groups, last_at, units)
    metrics = compute_metrics(all_groups, weights)
    return {'algorithm': 'greedy', 'groups': all_groups, 'metrics': metrics}


async def algo_random(event_oid: ObjectId, weights: Dict[str, float], seed: Optional[int] = None) -> dict:
    teams = await build_teams(event_oid)
    units, unit_emails = await build_units_from_teams(teams)
    event = await db_mod.db.events.find_one({'_id': event_oid})
    event_id_str = str(event.get('_id')) if event else None
    if event_id_str:
        constraints = await _load_constraints(event_id_str)
        units, unit_emails = apply_forced_pairs(units, unit_emails, constraints.get('forced_pairs') or [])
        units, unit_emails = apply_required_splits(units, unit_emails, constraints.get('split_team_ids') or [])
    units, unit_emails = await apply_minimal_splits(units, unit_emails)
    random_instance = random.Random(seed if seed is not None else algorithm_seed('random', 99))
    used_pairs: Set[Tuple[str, str]] = set()
    all_groups: List[dict] = []
    last_at: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    party_point = _after_party_point(event)
    for phase in phases()[:3]:
        random_instance.shuffle(units)
        groups = await phase_groups(
            units,
            phase,
            used_pairs,
            weights,
            last_at_host=last_at,
            after_party_point=(party_point if phase == 'dessert' else None),
        )
        all_groups.extend(groups)
        _update_last_locations(groups, last_at, units)
    metrics = compute_metrics(all_groups, weights)
    return {'algorithm': 'random', 'groups': all_groups, 'metrics': metrics}


async def algo_local_search(event_oid: ObjectId, weights: Dict[str, float], seed: Optional[int] = None) -> dict:
    base = await algo_greedy(event_oid, weights, seed if seed is not None else algorithm_seed('local_search', 7))
    groups = base['groups'][:]
    metrics = compute_metrics(groups, weights)
    return {'algorithm': 'local_search', 'groups': groups, 'metrics': metrics}


ALGORITHMS = {
    'greedy': algo_greedy,
    'random': algo_random,
    'local_search': algo_local_search,
}


async def run_algorithms(event_id: str, *, algorithms: List[str], weights: Optional[Dict[str, float]] = None) -> List[dict]:
    event = await get_event(event_id)
    if not event:
        raise ValueError('event not found')
    oid = event['_id']
    weights = weights or {}
    results: List[dict] = []
    for name in algorithms:
        fn = ALGORITHMS.get(name)
        if not fn:
            continue
        res = await fn(oid, weights)
        res['event_id'] = str(event['_id'])
        results.append(res)
    return results


async def _load_constraints(event_id: str) -> dict:
    doc = await db_mod.db.matching_constraints.find_one({'event_id': event_id})
    if not doc:
        return {'forced_pairs': [], 'split_team_ids': []}
    return {
        'forced_pairs': [
            {
                'a_email': (pair.get('a_email') or '').lower(),
                'b_email': (pair.get('b_email') or '').lower(),
            }
            for pair in (doc.get('forced_pairs') or [])
            if isinstance(pair, dict)
        ],
        'split_team_ids': [str(value) for value in (doc.get('split_team_ids') or [])],
    }


def _after_party_point(event: Optional[dict]) -> Optional[Tuple[float, float]]:
    if not event:
        return None
    try:
        coords = (((event or {}).get('after_party_location') or {}).get('point') or {}).get('coordinates')
        if isinstance(coords, list) and len(coords) == 2 and all(isinstance(val, (int, float)) for val in coords):
            return (float(coords[1]), float(coords[0]))
    except Exception:
        return None
    return None


def _update_last_locations(groups: List[dict], last_at: Dict[str, Tuple[Optional[float], Optional[float]]], units: List[dict]) -> None:
    units_by_id = {unit['unit_id']: unit for unit in units}
    for group in groups:
        host_id = group.get('host_team_id')
        host = units_by_id.get(host_id)
        if host is None:
            continue
        point = (host.get('lat'), host.get('lon'))
        for unit_id in [host_id, *(group.get('guest_team_ids') or [])]:
            last_at[unit_id] = point
