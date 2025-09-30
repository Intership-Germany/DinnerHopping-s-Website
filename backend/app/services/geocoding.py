import os
import asyncio
from functools import lru_cache
from typing import Optional, Tuple

try:
    import httpx  # type: ignore
except Exception:  # pragma: no cover
    httpx = None  # graceful fallback when dependency not installed in analysis env

# Simple geocoding with Pelias autocomplete and Nominatim fallback
# Environment variables used:
# - PELIAS_BASE (e.g., https://pelias.cephlabs.de)
# - NOMINATIM_URL (e.g., https://nominatim.bunsencloud.de/search.php)
# - GEOCODER_USER_AGENT
# - GEOCODER_NOMINATIM_DELAY (seconds between requests)
# - GEOCODER_DISABLE (true/false)
# - GEOCODER_DEBUG (1/0)

PELIA_DEFAULT = os.getenv('PELIAS_BASE', 'https://pelias.cephlabs.de')
NOMINATIM_DEFAULT = os.getenv('NOMINATIM_URL', 'https://nominatim.bunsencloud.de/search.php')
UA = os.getenv('GEOCODER_USER_AGENT', 'dinnerhopping-app/1.0')
NOM_DELAY = float(os.getenv('GEOCODER_NOMINATIM_DELAY', '1.0') or '1.0')
DISABLED = os.getenv('GEOCODER_DISABLE', 'false').lower() in ('1','true','yes')
DEBUG = os.getenv('GEOCODER_DEBUG', '0') in ('1','true','yes')


async def _pelias_geocode(address: str) -> Optional[Tuple[float, float]]:
    if httpx is None:
        return None
    base = PELIA_DEFAULT.rstrip('/')
    url = f"{base}/v1/search"
    params = {'text': address, 'size': 1}
    headers = {'User-Agent': UA}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, params=params, headers=headers)
            if r.status_code != 200:
                return None
            data = r.json()
            feats = (data or {}).get('features') or []
            if feats:
                coords = feats[0].get('geometry', {}).get('coordinates') or []
                if len(coords) == 2:
                    lon, lat = coords
                    return (lat, lon)
    except Exception as e:  # pragma: no cover
        if DEBUG:
            print('[geocode] pelias error', e)
    return None


async def _nominatim_geocode(address: str) -> Optional[Tuple[float, float]]:
    if httpx is None:
        return None
    url = NOMINATIM_DEFAULT
    params = {'q': address, 'format': 'jsonv2', 'limit': 1}
    headers = {'User-Agent': UA}
    # Be polite: small delay between requests to public instance
    await asyncio.sleep(NOM_DELAY)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, params=params, headers=headers)
            if r.status_code != 200:
                return None
            ct = r.headers.get('content-type', '')
            arr = r.json() if 'application/json' in ct else []
            if isinstance(arr, list) and arr:
                lat = float(arr[0].get('lat'))
                lon = float(arr[0].get('lon'))
                return (lat, lon)
    except Exception as e:  # pragma: no cover
        if DEBUG:
            print('[geocode] nominatim error', e)
    return None


@lru_cache(maxsize=2048)
def _cache_key(address: str) -> str:
    return (address or '').strip().lower()


async def geocode_address(address: str) -> Optional[Tuple[float, float]]:
    if DISABLED:
        return None
    _ = _cache_key(address)  # hint for cache key usage
    # Call Pelias first then Nominatim
    latlon = await _pelias_geocode(address)
    if latlon:
        return latlon
    return await _nominatim_geocode(address)
