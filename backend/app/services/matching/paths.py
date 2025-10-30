from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from bson.objectid import ObjectId

from ... import db as db_mod
from ...utils import approx_travel_time_minutes as _approx_minutes  # type: ignore
from ...utils import haversine_m as _haversine_m  # type: ignore

from ..routing import route_duration_seconds

from .config import travel_fast_mode
from .data import build_teams, get_event


async def compute_team_paths(event_id: str, version: Optional[int] = None, ids: Optional[Set[str]] = None, fast: bool = True) -> dict:
    query: Dict[str, Any] = {'event_id': event_id}
    if version is not None:
        query['version'] = int(version)
    match_doc = await db_mod.db.matches.find_one(query, sort=[('version', -1)])
    if not match_doc:
        return {'team_paths': {}, 'bounds': None, 'after_party': None}
    groups = match_doc.get('groups') or []
    id_filter = set(ids) if ids else None
    if id_filter:
        groups = [group for group in groups if _group_involves_requested(group, id_filter)]
        if not groups:
            return {'team_paths': {}, 'bounds': None, 'after_party': None}
    event = await get_event(event_id)
    if not event:
        return {'team_paths': {}, 'bounds': None, 'after_party': None}
    teams = await build_teams(event['_id'])
    after_party_point = _after_party_location(event)
    phase_sequence = ['appetizer', 'main', 'dessert']
    phase_rank = {phase: idx for idx, phase in enumerate(phase_sequence)}
    has_after_party_coords = (
        isinstance(after_party_point, dict)
        and isinstance(after_party_point.get('lat'), (int, float))
        and isinstance(after_party_point.get('lon'), (int, float))
    )
    if has_after_party_coords:
        phase_rank['after_party'] = len(phase_rank)
    needed_ids = _collect_needed_ids(groups) if id_filter else None
    coord_map: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    for team in teams:
        team_id = str(team['team_id'])
        if needed_ids and team_id not in needed_ids:
            continue
        coord_map[team_id] = (team.get('lat'), team.get('lon'))

    async def resolve_coords(team_id: str) -> Tuple[Optional[float], Optional[float]]:
        if team_id in coord_map:
            return coord_map[team_id]
        if team_id.startswith('split:'):
            email = team_id.split(':', 1)[1]
            user = await db_mod.db.users.find_one({'email': email})
            if user and isinstance(user.get('lat'), (int, float)) and isinstance(user.get('lon'), (int, float)):
                return (float(user['lat']), float(user['lon']))
        if team_id.startswith('pair:'):
            part = team_id.split(':', 1)[1]
            emails = [email for email in part.split('+') if email]
            points = []
            for email in emails:
                user = await db_mod.db.users.find_one({'email': email})
                if user and isinstance(user.get('lat'), (int, float)) and isinstance(user.get('lon'), (int, float)):
                    points.append((float(user['lat']), float(user['lon'])))
            if points:
                lat = sum(point[0] for point in points) / len(points)
                lon = sum(point[1] for point in points) / len(points)
                return (lat, lon)
        return (None, None)

    path_points: Dict[str, List[Tuple[str, Optional[float], Optional[float]]]] = {}
    for phase in phase_sequence:
        for group in groups:
            if group.get('phase') != phase:
                continue
            host = str(group.get('host_team_id')) if group.get('host_team_id') is not None else None
            guest_ids = [str(team_id) for team_id in (group.get('guest_team_ids') or [])]
            if host is None:
                continue
            for team_id in [host] + guest_ids:
                if id_filter and team_id not in id_filter:
                    continue
                lat, lon = await resolve_coords(host)
                path_points.setdefault(team_id, []).append((phase, lat, lon))
    if has_after_party_coords:
        for points in path_points.values():
            points.append(('after_party', after_party_point['lat'], after_party_point['lon']))
    bounds = None
    min_lat = min_lon = float('inf')
    max_lat = max_lon = float('-inf')
    team_paths: Dict[str, dict] = {}
    for team_id, points in path_points.items():
        sorted_points = sorted(points, key=lambda item: phase_rank.get(item[0], len(phase_rank)))
        for _, lat, lon in sorted_points:
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                min_lat = min(min_lat, float(lat))
                max_lat = max(max_lat, float(lat))
                min_lon = min(min_lon, float(lon))
                max_lon = max(max_lon, float(lon))
        leg_seconds: List[float] = []
        leg_minutes: List[float] = []
        for index in range(len(sorted_points) - 1):
            a = sorted_points[index]
            b = sorted_points[index + 1]
            if not _has_coordinates(a) or not _has_coordinates(b):
                leg_seconds.append(0.0)
                leg_minutes.append(0.0)
                continue
            if fast and travel_fast_mode():
                distance = _haversine_m(float(a[1]), float(a[2]), float(b[1]), float(b[2]))
                minutes = _approx_minutes(distance, mode='bike')
                leg_minutes.append(minutes)
                leg_seconds.append(minutes * 60.0)
            else:
                seconds = await route_duration_seconds([(float(a[1]), float(a[2])), (float(b[1]), float(b[2]))])
                sec_value = float(seconds or 0.0)
                leg_seconds.append(sec_value)
                leg_minutes.append(sec_value / 60.0 if sec_value else 0.0)
        team_paths[team_id] = {
            'points': [{'phase': phase, 'lat': lat, 'lon': lon} for (phase, lat, lon) in sorted_points],
            'leg_seconds': leg_seconds,
            'leg_minutes': leg_minutes,
        }
    if min_lat != float('inf'):
        bounds = {'min_lat': min_lat, 'min_lon': min_lon, 'max_lat': max_lat, 'max_lon': max_lon}
    elif has_after_party_coords:
        bounds = {
            'min_lat': float(after_party_point['lat']),
            'max_lat': float(after_party_point['lat']),
            'min_lon': float(after_party_point['lon']),
            'max_lon': float(after_party_point['lon']),
        }
    return {'team_paths': team_paths, 'bounds': bounds, 'after_party': after_party_point if has_after_party_coords else None}


def _group_involves_requested(group: dict, id_filter: Set[str]) -> bool:
    host = str(group.get('host_team_id')) if group.get('host_team_id') is not None else None
    guests = [str(team_id) for team_id in (group.get('guest_team_ids') or [])]
    if host and host in id_filter:
        return True
    return any(team_id in id_filter for team_id in guests)


def _collect_needed_ids(groups: List[dict]) -> Set[str]:
    needed: Set[str] = set()
    for group in groups:
        host = group.get('host_team_id')
        if host is not None:
            needed.add(str(host))
        for team_id in (group.get('guest_team_ids') or []):
            needed.add(str(team_id))
    return needed


def _has_coordinates(point: Tuple[str, Optional[float], Optional[float]]) -> bool:
    return isinstance(point[1], (int, float)) and isinstance(point[2], (int, float))


def _after_party_location(event: Optional[dict]) -> Optional[dict]:
    def _extract_coords(source: Optional[dict]) -> Optional[Tuple[float, float]]:
        if not isinstance(source, dict):
            return None
        try:
            for key in ('point', 'zip'):
                candidate = source.get(key)
                if isinstance(candidate, dict):
                    coords = candidate.get('coordinates')
                    if (
                        isinstance(coords, list)
                        and len(coords) == 2
                        and all(isinstance(value, (int, float)) for value in coords)
                    ):
                        return (float(coords[1]), float(coords[0]))
            direct = source.get('coordinates')
            if (
                isinstance(direct, list)
                and len(direct) == 2
                and all(isinstance(value, (int, float)) for value in direct)
            ):
                return (float(direct[1]), float(direct[0]))
            lat = source.get('lat')
            lon = source.get('lon')
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                return (float(lat), float(lon))
        except Exception:
            return None
        return None

    event_dict = event or {}
    primary = _extract_coords(event_dict.get('after_party_location'))
    if primary:
        return {'lat': primary[0], 'lon': primary[1]}
    fallback = _extract_coords(event_dict.get('location'))
    if fallback:
        return {'lat': fallback[0], 'lon': fallback[1]}
    return None
