from __future__ import annotations

import asyncio
import contextlib
import random
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple, TypeVar

from bson.objectid import ObjectId

from ... import db as db_mod

from .config import (
    allow_team_splits,
    algorithm_seed,
    guest_candidate_limit,
    phases,
    routing_parallelism,
    travel_fast_mode,
)
from .data import build_teams, get_event
from .grouping import TravelTimeResolver, phase_groups
from .metrics import compute_metrics
from .units import (
    apply_forced_pairs,
    apply_minimal_splits,
    apply_required_splits,
    build_units_from_teams,
)

AlgorithmProgressCallback = Callable[[float, Optional[str]], Awaitable[None]]
T = TypeVar('T')


async def algo_greedy(
    event_oid: ObjectId,
    weights: Dict[str, float],
    seed: Optional[int] = None,
    progress_cb: Optional[AlgorithmProgressCallback] = None,
) -> dict:
    await _emit_progress(progress_cb, 0.02, 'Loading teams...')
    teams = await build_teams(event_oid)
    units, unit_emails = await build_units_from_teams(teams)
    await _emit_progress(progress_cb, 0.06, 'Preparing units...')
    event = await db_mod.db.events.find_one({'_id': event_oid})
    event_id_str = str(event.get('_id')) if event else None
    if event_id_str:
        constraints = await _load_constraints(event_id_str)
        forced_pairs = constraints.get('forced_pairs') or []
        split_ids = constraints.get('split_team_ids') or []
        if forced_pairs:
            units, unit_emails = apply_forced_pairs(units, unit_emails, forced_pairs)
        if split_ids:
            units, unit_emails = apply_required_splits(units, unit_emails, split_ids)
        await _emit_progress(progress_cb, 0.09, 'Applying constraints...')
    if allow_team_splits():
        units, unit_emails = await apply_minimal_splits(units, unit_emails)
        await _emit_progress(progress_cb, 0.11, 'Splitting oversized teams...')

    random_instance = random.Random(seed if seed is not None else algorithm_seed('greedy', 42))
    random_instance.shuffle(units)
    used_pairs: Set[Tuple[str, str]] = set()
    all_groups: List[dict] = []
    last_at: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    party_point = _after_party_point(event)
    travel_resolver = TravelTimeResolver(
        fast_mode=travel_fast_mode(),
        parallelism=routing_parallelism(),
    )
    distance_cache: Dict[Tuple[str, str], float] = {}
    guest_limit = guest_candidate_limit()
    host_usage: Dict[str, int] = {}
    unit_lookup = {str(unit['unit_id']): unit for unit in units if unit.get('unit_id') is not None}
    unit_ids: Set[str] = set(unit_lookup.keys())
    def _unit_size(unit_id: Optional[str], info: Optional[dict] = None) -> int:
        if not unit_id:
            return 0
        candidate = info if info is not None else unit_lookup.get(str(unit_id))
        if candidate is None:
            candidate = unit_lookup.get(str(unit_id))
        try:
            return int(candidate.get('size') or 0) if candidate else 0
        except (TypeError, ValueError):
            return 0
    expected_participants_total = sum(_unit_size(uid) for uid in unit_ids)
    global_assigned: Set[str] = set()
    unmatched_tracker: Dict[str, dict] = {}
    phase_summary: Dict[str, dict] = {}

    phase_sequence = list(phases()[:3])
    phase_count = len(phase_sequence) or 1
    phase_ratio_start = 0.12
    phase_ratio_end = 0.92
    phase_ratio_span = max(0.0, phase_ratio_end - phase_ratio_start)

    for index, phase in enumerate(phase_sequence):
        if index > 0:
            units = units[1:] + units[:1]
        display_name = str(phase).replace('_', ' ').title()
        start_ratio = phase_ratio_start + phase_ratio_span * (index / phase_count)
        end_ratio = phase_ratio_start + phase_ratio_span * ((index + 1) / phase_count)
        await _emit_progress(progress_cb, start_ratio, f'{display_name} matching started')
        phase_coro = phase_groups(
            units,
            phase,
            used_pairs,
            weights,
            last_at_host=last_at,
            after_party_point=(party_point if phase == 'dessert' else None),
            travel_resolver=travel_resolver,
            candidate_guest_limit=guest_limit,
            distance_cache=distance_cache,
            host_usage=host_usage,
            host_limit=1,
        )
        groups, leftovers = await _await_with_progress(
            phase_coro,
            progress_cb=progress_cb,
            start_ratio=start_ratio,
            end_ratio=end_ratio,
            message=f'{display_name} matching in progress',
        )
        all_groups.extend(groups)
        phase_assigned: Set[str] = set()
        for group in groups:
            host_id = group.get('host_team_id')
            if host_id is not None:
                host_key = str(host_id)
                host_usage[host_key] = host_usage.get(host_key, 0) + 1
                phase_assigned.add(host_key)
                global_assigned.add(host_key)
            for guest_id in group.get('guest_team_ids') or []:
                if guest_id is None:
                    continue
                guest_key = str(guest_id)
                phase_assigned.add(guest_key)
                global_assigned.add(guest_key)

        def mark_unmatched(team_id_str: str, unit_info: Optional[dict]) -> None:
            details = unit_info or unit_lookup.get(team_id_str) or {}
            entry = unmatched_tracker.setdefault(team_id_str, {
                'team_id': team_id_str,
                'phases': set(),
                'can_host_any': True,
                'can_host_main': True,
                'size': 0,
            })
            entry['phases'].add(str(phase))
            entry['can_host_any'] = entry['can_host_any'] and bool(details.get('can_host_any', True))
            entry['can_host_main'] = entry['can_host_main'] and bool(details.get('can_host_main', True))
            size_value = _unit_size(team_id_str, details)
            entry['size'] = max(entry.get('size', 0), size_value)

        leftover_ids: Set[str] = set()
        for leftover in leftovers:
            team_id = leftover.get('unit_id')
            if team_id is None:
                continue
            team_key = str(team_id)
            leftover_ids.add(team_key)
            mark_unmatched(team_key, leftover)

        missing_ids = unit_ids.difference(phase_assigned)
        for team_key in missing_ids:
            if team_key not in leftover_ids:
                mark_unmatched(team_key, unit_lookup.get(team_key))

        phase_summary[str(phase)] = {
            'group_count': len(groups),
            'assigned_units': len(phase_assigned),
            'missing_units': len(missing_ids),
            'expected_units': len(unit_ids),
            'assigned_participants': sum(_unit_size(team_id) for team_id in phase_assigned),
            'missing_participants': sum(_unit_size(team_id) for team_id in missing_ids),
            'expected_participants': expected_participants_total,
        }
        _update_last_locations(groups, last_at, units)
        await _emit_progress(progress_cb, end_ratio, f'{display_name} matching complete')

    await _emit_progress(progress_cb, 0.95, 'Scoring results...')
    metrics = compute_metrics(all_groups, weights)
    unmatched_units: List[dict] = []
    for entry in unmatched_tracker.values():
        phases_list = sorted(entry.pop('phases', set()))
        unmatched_units.append({
            'team_id': entry.get('team_id'),
            'phases': phases_list,
            'can_host_any': bool(entry.get('can_host_any')),
            'can_host_main': bool(entry.get('can_host_main')),
            'size': int(entry.get('size') or 0),
        })
    unmatched_units.sort(key=lambda item: item['team_id'])
    global_missing = sorted(unit_ids.difference(global_assigned))
    total_participants = expected_participants_total
    assigned_participants = sum(_unit_size(uid) for uid in global_assigned)
    unmatched_participants = max(0, total_participants - assigned_participants)
    metrics.update({
        'total_unit_count': len(unit_ids),
        'assigned_unit_count': len(global_assigned),
        'unmatched_unit_count': len(global_missing),
        'unmatched_unit_ids': global_missing,
        'phase_summary': phase_summary,
        'total_participant_count': total_participants,
        'assigned_participant_count': assigned_participants,
        'unmatched_participant_count': unmatched_participants,
    })
    await _emit_progress(progress_cb, 1.0, 'Algorithm complete')
    return {
        'algorithm': 'greedy',
        'groups': all_groups,
        'metrics': metrics,
        'unmatched_units': unmatched_units,
    }


async def algo_random(
    event_oid: ObjectId,
    weights: Dict[str, float],
    seed: Optional[int] = None,
    progress_cb: Optional[AlgorithmProgressCallback] = None,
) -> dict:
    await _emit_progress(progress_cb, 0.02, 'Loading teams...')
    teams = await build_teams(event_oid)
    units, unit_emails = await build_units_from_teams(teams)
    await _emit_progress(progress_cb, 0.06, 'Preparing units...')
    event = await db_mod.db.events.find_one({'_id': event_oid})
    event_id_str = str(event.get('_id')) if event else None
    if event_id_str:
        constraints = await _load_constraints(event_id_str)
        forced_pairs = constraints.get('forced_pairs') or []
        split_ids = constraints.get('split_team_ids') or []
        if forced_pairs:
            units, unit_emails = apply_forced_pairs(units, unit_emails, forced_pairs)
        if split_ids:
            units, unit_emails = apply_required_splits(units, unit_emails, split_ids)
        await _emit_progress(progress_cb, 0.09, 'Applying constraints...')
    if allow_team_splits():
        units, unit_emails = await apply_minimal_splits(units, unit_emails)
        await _emit_progress(progress_cb, 0.11, 'Splitting oversized teams...')

    random_instance = random.Random(seed if seed is not None else algorithm_seed('random', 99))
    used_pairs: Set[Tuple[str, str]] = set()
    all_groups: List[dict] = []
    last_at: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    party_point = _after_party_point(event)
    travel_resolver = TravelTimeResolver(
        fast_mode=travel_fast_mode(),
        parallelism=routing_parallelism(),
    )
    distance_cache: Dict[Tuple[str, str], float] = {}
    guest_limit = guest_candidate_limit()
    host_usage: Dict[str, int] = {}
    unit_lookup = {str(unit['unit_id']): unit for unit in units if unit.get('unit_id') is not None}
    unit_ids: Set[str] = set(unit_lookup.keys())
    def _unit_size(unit_id: Optional[str], info: Optional[dict] = None) -> int:
        if not unit_id:
            return 0
        candidate = info if info is not None else unit_lookup.get(str(unit_id))
        if candidate is None:
            candidate = unit_lookup.get(str(unit_id))
        try:
            return int(candidate.get('size') or 0) if candidate else 0
        except (TypeError, ValueError):
            return 0
    expected_participants_total = sum(_unit_size(uid) for uid in unit_ids)
    global_assigned: Set[str] = set()
    unmatched_tracker: Dict[str, dict] = {}
    phase_summary: Dict[str, dict] = {}

    phase_sequence = list(phases()[:3])
    phase_count = len(phase_sequence) or 1
    phase_ratio_start = 0.12
    phase_ratio_end = 0.92
    phase_ratio_span = max(0.0, phase_ratio_end - phase_ratio_start)

    for index, phase in enumerate(phase_sequence):
        random_instance.shuffle(units)
        display_name = str(phase).replace('_', ' ').title()
        start_ratio = phase_ratio_start + phase_ratio_span * (index / phase_count)
        end_ratio = phase_ratio_start + phase_ratio_span * ((index + 1) / phase_count)
        await _emit_progress(progress_cb, start_ratio, f'{display_name} matching started')
        phase_coro = phase_groups(
            units,
            phase,
            used_pairs,
            weights,
            last_at_host=last_at,
            after_party_point=(party_point if phase == 'dessert' else None),
            travel_resolver=travel_resolver,
            candidate_guest_limit=guest_limit,
            distance_cache=distance_cache,
            host_usage=host_usage,
            host_limit=1,
        )
        groups, leftovers = await _await_with_progress(
            phase_coro,
            progress_cb=progress_cb,
            start_ratio=start_ratio,
            end_ratio=end_ratio,
            message=f'{display_name} matching in progress',
        )
        all_groups.extend(groups)
        phase_assigned: Set[str] = set()
        for group in groups:
            host_id = group.get('host_team_id')
            if host_id is not None:
                host_key = str(host_id)
                host_usage[host_key] = host_usage.get(host_key, 0) + 1
                phase_assigned.add(host_key)
                global_assigned.add(host_key)
            for guest_id in group.get('guest_team_ids') or []:
                if guest_id is None:
                    continue
                guest_key = str(guest_id)
                phase_assigned.add(guest_key)
                global_assigned.add(guest_key)

        def mark_unmatched(team_id_str: str, unit_info: Optional[dict]) -> None:
            details = unit_info or unit_lookup.get(team_id_str) or {}
            entry = unmatched_tracker.setdefault(team_id_str, {
                'team_id': team_id_str,
                'phases': set(),
                'can_host_any': True,
                'can_host_main': True,
                'size': 0,
            })
            entry['phases'].add(str(phase))
            entry['can_host_any'] = entry['can_host_any'] and bool(details.get('can_host_any', True))
            entry['can_host_main'] = entry['can_host_main'] and bool(details.get('can_host_main', True))
            size_value = _unit_size(team_id_str, details)
            entry['size'] = max(entry.get('size', 0), size_value)

        leftover_ids: Set[str] = set()
        for leftover in leftovers:
            team_id = leftover.get('unit_id')
            if team_id is None:
                continue
            team_key = str(team_id)
            leftover_ids.add(team_key)
            mark_unmatched(team_key, leftover)

        missing_ids = unit_ids.difference(phase_assigned)
        for team_key in missing_ids:
            if team_key not in leftover_ids:
                mark_unmatched(team_key, unit_lookup.get(team_key))

        phase_summary[str(phase)] = {
            'group_count': len(groups),
            'assigned_units': len(phase_assigned),
            'missing_units': len(missing_ids),
            'expected_units': len(unit_ids),
            'assigned_participants': sum(_unit_size(team_id) for team_id in phase_assigned),
            'missing_participants': sum(_unit_size(team_id) for team_id in missing_ids),
            'expected_participants': expected_participants_total,
        }
        _update_last_locations(groups, last_at, units)
        await _emit_progress(progress_cb, end_ratio, f'{display_name} matching complete')

    await _emit_progress(progress_cb, 0.95, 'Scoring results...')
    metrics = compute_metrics(all_groups, weights)
    unmatched_units: List[dict] = []
    for entry in unmatched_tracker.values():
        phases_list = sorted(entry.pop('phases', set()))
        unmatched_units.append({
            'team_id': entry.get('team_id'),
            'phases': phases_list,
            'can_host_any': bool(entry.get('can_host_any')),
            'can_host_main': bool(entry.get('can_host_main')),
            'size': int(entry.get('size') or 0),
        })
    unmatched_units.sort(key=lambda item: item['team_id'])
    global_missing = sorted(unit_ids.difference(global_assigned))
    total_participants = expected_participants_total
    assigned_participants = sum(_unit_size(uid) for uid in global_assigned)
    unmatched_participants = max(0, total_participants - assigned_participants)
    metrics.update({
        'total_unit_count': len(unit_ids),
        'assigned_unit_count': len(global_assigned),
        'unmatched_unit_count': len(global_missing),
        'unmatched_unit_ids': global_missing,
        'phase_summary': phase_summary,
        'total_participant_count': total_participants,
        'assigned_participant_count': assigned_participants,
        'unmatched_participant_count': unmatched_participants,
    })
    await _emit_progress(progress_cb, 1.0, 'Algorithm complete')
    return {
        'algorithm': 'random',
        'groups': all_groups,
        'metrics': metrics,
        'unmatched_units': unmatched_units,
    }


async def algo_local_search(
    event_oid: ObjectId,
    weights: Dict[str, float],
    seed: Optional[int] = None,
    progress_cb: Optional[AlgorithmProgressCallback] = None,
) -> dict:
    base = await algo_greedy(
        event_oid,
        weights,
        seed if seed is not None else algorithm_seed('local_search', 7),
        progress_cb=progress_cb,
    )
    groups = base['groups'][:]
    metrics = base.get('metrics') or compute_metrics(groups, weights)
    await _emit_progress(progress_cb, 1.0, 'Algorithm complete')
    return {
        'algorithm': 'local_search',
        'groups': groups,
        'metrics': metrics,
        'unmatched_units': base.get('unmatched_units', []),
    }


ALGORITHMS = {
    'greedy': algo_greedy,
    'random': algo_random,
    'local_search': algo_local_search,
}


ProgressCallback = Callable[[Dict[str, Any]], Awaitable[None]]


async def run_algorithms(
    event_id: str,
    *,
    algorithms: List[str],
    weights: Optional[Dict[str, float]] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> List[dict]:
    event = await get_event(event_id)
    if not event:
        raise ValueError('event not found')
    oid = event['_id']
    weights = weights or {}
    results: List[dict] = []
    total = max(1, len(algorithms))
    for index, name in enumerate(algorithms, start=1):
        fn = ALGORITHMS.get(name)
        if not fn:
            continue
        if progress_cb:
            await progress_cb({
                'stage': 'start',
                'algorithm': name,
                'index': index,
                'total': total,
            })

            async def algorithm_step_cb(
                ratio: float,
                message: Optional[str] = None,
                *,
                _name: str = name,
                _index: int = index,
                _total: int = total,
            ) -> None:
                payload: Dict[str, Any] = {
                    'stage': 'step',
                    'algorithm': _name,
                    'index': _index,
                    'total': _total,
                    'ratio': max(0.0, min(1.0, float(ratio))),
                }
                if message:
                    payload['message'] = message
                await progress_cb(payload)
        else:
            algorithm_step_cb = None

        res = await fn(oid, weights, progress_cb=algorithm_step_cb)
        res['event_id'] = str(event['_id'])
        results.append(res)

        if progress_cb:
            await progress_cb({
                'stage': 'done',
                'algorithm': name,
                'index': index,
                'total': total,
            })
    return results


async def _await_with_progress(
    awaitable: Awaitable[T],
    *,
    progress_cb: Optional[AlgorithmProgressCallback],
    start_ratio: float,
    end_ratio: float,
    message: Optional[str] = None,
    interval: float = 2.0,
    min_step: float = 0.02,
) -> T:
    if progress_cb is None:
        return await awaitable

    task = asyncio.create_task(awaitable)
    start_ratio = max(0.0, min(1.0, start_ratio))
    end_ratio = max(start_ratio, min(1.0, end_ratio))
    step = max(min_step, (end_ratio - start_ratio) * 0.2)
    current_ratio = start_ratio

    try:
        while True:
            try:
                result = await asyncio.wait_for(asyncio.shield(task), timeout=interval)
                if current_ratio < end_ratio:
                    await progress_cb(end_ratio, message)
                return result
            except asyncio.TimeoutError:
                current_ratio = min(end_ratio, current_ratio + step)
                await progress_cb(current_ratio, message)
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _emit_progress(
    progress_cb: Optional[AlgorithmProgressCallback],
    ratio: float,
    message: Optional[str] = None,
) -> None:
    if progress_cb is None:
        return
    await progress_cb(max(0.0, min(1.0, float(ratio))), message)


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
