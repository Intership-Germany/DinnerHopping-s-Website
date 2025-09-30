import os
from typing import List, Tuple, Optional

try:
    import httpx  # type: ignore
except Exception:  # pragma: no cover
    httpx = None

# Routing service for cycling durations between points
# Env:
# - OSRM_BASE (e.g., https://osrm.bunsencloud.de)
# - ORS_BASE (e.g., https://germany.ors.bunsencloud.de/ors)
# - ORS_API_KEY (optional)
# - ROUTING_PREFER (osrm|ors)

OSRM_BASE = os.getenv('OSRM_BASE', os.getenv('OSRM_URL', 'https://osrm.bunsencloud.de')).rstrip('/')
ORS_BASE = os.getenv('ORS_BASE', os.getenv('ORS_URL', 'https://germany.ors.bunsencloud.de/ors')).rstrip('/')
ORS_API_KEY = os.getenv('ORS_API_KEY')
PREFER = os.getenv('ROUTING_PREFER', 'osrm').lower()
OSRM_PROFILE = os.getenv('OSRM_PROFILE', 'bike')  # user requested 'bike' path; default to bike


async def _osrm_route(coords: List[Tuple[float, float]]) -> Optional[float]:
    # coords: list of (lat,lon); OSRM expects lon,lat semicolon separated
    if httpx is None:
        return None
    if not coords or len(coords) < 2:
        return 0.0
    pairs = [f"{lon:.6f},{lat:.6f}" for (lat, lon) in coords]
    url = f"{OSRM_BASE}/route/v1/{OSRM_PROFILE}/" + ";".join(pairs)
    params = {'overview': 'false', 'alternatives': 'false', 'steps': 'false'}
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, params=params)
        if r.status_code != 200:
            return None
        data = r.json()
        routes = (data or {}).get('routes') or []
        if not routes:
            return None
        # duration in seconds
        return float(routes[0].get('duration') or 0.0)


async def _ors_route(coords: List[Tuple[float, float]]) -> Optional[float]:
    if httpx is None:
        return None
    if not coords or len(coords) < 2:
        return 0.0
    # ORS expects [[lon,lat], [lon,lat]]
    locations = [[c[1], c[0]] for c in coords]
    url = f"{ORS_BASE}/v2/directions/cycling-regular"
    headers = {'Content-Type': 'application/json'}
    if ORS_API_KEY:
        headers['Authorization'] = ORS_API_KEY
    payload = {'coordinates': locations, 'units': 'm'}
    async with httpx.AsyncClient(timeout=12.0) as client:
        r = await client.post(url, json=payload, headers=headers)
        if r.status_code != 200:
            return None
        data = r.json()
        summary = (((data or {}).get('routes') or [{}])[0].get('summary') or {})
        return float(summary.get('duration') or 0.0)


async def route_duration_seconds(coords: List[Tuple[float, float]]) -> Optional[float]:
    """Return cycling route duration in seconds for the sequence of coordinates.

    Tries the preferred engine first then falls back to the other.
    """
    if PREFER == 'ors':
        d = await _ors_route(coords)
        if d is not None:
            return d
        return await _osrm_route(coords)
    else:
        d = await _osrm_route(coords)
        if d is not None:
            return d
        return await _ors_route(coords)


async def route_polyline(coords: List[Tuple[float, float]], *, alternatives: bool = True) -> Optional[List[List[float]]]:
    """Fetch a real OSRM route geometry for given coordinates.

    Returns list of [lat, lon] points for the primary route geometry, or None if unavailable.
    Uses geometries=geojson and steps=true to approximate the user's desired 'real route'.
    """
    if httpx is None:
        return None
    if not coords or len(coords) < 2:
        return None
    pairs = [f"{lon:.6f},{lat:.6f}" for (lat, lon) in coords]
    # user-provided example used '/route/v1/bike/...&overview=false&alternatives=true&steps=true'
    url = f"{OSRM_BASE}/route/v1/{OSRM_PROFILE}/" + ";".join(pairs)
    params = {
        'overview': 'full',
        'geometries': 'geojson',
        'alternatives': 'true' if alternatives else 'false',
        'steps': 'true',
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                return None
            data = r.json() or {}
            routes = data.get('routes') or []
            if not routes:
                return None
            geom = routes[0].get('geometry') or {}
            coords_ll = geom.get('coordinates') or []  # [[lon,lat], ...]
            if not isinstance(coords_ll, list) or not coords_ll:
                return None
            # convert to [lat, lon]
            return [[float(lat), float(lon)] for lon, lat in coords_ll]
    except Exception:
        return None

