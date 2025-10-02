#!/usr/bin/env python3
"""
Backfill user latitude/longitude using Pelias (primary) and Nominatim (fallback).

- Connects to MongoDB using MONGO_URI / MONGO_DB
- Scans users without lat/lon but with address_struct.{street,street_no,postal_code,city}
- Geocodes and updates users with lat, lon, geocoded_at

Env:
  MONGO_URI=mongodb://...
  MONGO_DB=dinnerhopping
  GEOCODER_DISABLE=false            # set true to dry-run without network
  GEOCODER_NOMINATIM_DELAY=1.0      # seconds between Nominatim requests
  PELIAS_BASE=https://pelias.cephlabs.de
  NOMINATIM_URL=https://nominatim.bunsencloud.de/search.php
  GEOCODER_USER_AGENT="dinnerhopping-backfill/1.0 (admin@example.com)"

Usage examples:
  python backend/scripts/backfill_user_geocodes.py --limit 200
  DRY_RUN=1 python backend/scripts/backfill_user_geocodes.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from typing import Optional, Tuple

from pymongo import MongoClient

try:
    import httpx  # type: ignore
except Exception:
    httpx = None

PELIAS_BASE = os.getenv('PELIAS_BASE', 'https://pelias.cephlabs.de').rstrip('/')
NOMINATIM_URL = os.getenv('NOMINATIM_URL', 'https://nominatim.bunsencloud.de/search.php')
UA = os.getenv('GEOCODER_USER_AGENT', 'dinnerhopping-backfill/1.0')
NOM_DELAY = float(os.getenv('GEOCODER_NOMINATIM_DELAY', '1.0') or '1.0')
DISABLED = os.getenv('GEOCODER_DISABLE', 'false').lower() in ('1','true','yes')
DRY_RUN = os.getenv('DRY_RUN', '0').lower() in ('1','true','yes')

async def pelias_geocode(address: str) -> Optional[Tuple[float, float]]:
    if httpx is None:
        return None
    url = f"{PELIAS_BASE}/v1/search"
    params = {'text': address, 'size': 1}
    headers = {'User-Agent': UA}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, params=params, headers=headers)
            if r.status_code != 200:
                return None
            data = r.json() or {}
            feats = data.get('features') or []
            if feats:
                coords = (feats[0].get('geometry') or {}).get('coordinates') or []
                if len(coords) == 2:
                    lon, lat = coords
                    return float(lat), float(lon)
    except Exception:
        return None
    return None

async def nominatim_geocode(address: str) -> Optional[Tuple[float, float]]:
    if httpx is None:
        return None
    params = {'q': address, 'format': 'jsonv2', 'limit': 1}
    headers = {'User-Agent': UA}
    await asyncio.sleep(NOM_DELAY)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(NOMINATIM_URL, params=params, headers=headers)
            if r.status_code != 200:
                return None
            arr = r.json() or []
            if isinstance(arr, list) and arr:
                return float(arr[0]['lat']), float(arr[0]['lon'])
    except Exception:
        return None
    return None

async def geocode(address: str) -> Optional[Tuple[float, float]]:
    if DISABLED:
        return None
    latlon = await pelias_geocode(address)
    if latlon:
        return latlon
    return await nominatim_geocode(address)


def _addr_from_struct(struct: dict | None) -> Optional[str]:
    if not struct:
        return None
    st = (struct.get('street') or '').strip()
    no = (struct.get('street_no') or '').strip()
    pc = (struct.get('postal_code') or '').strip()
    city = (struct.get('city') or '').strip()
    parts_left = ' '.join([p for p in (st, no) if p])
    parts_right = ' '.join([p for p in (pc, city) if p])
    addr = ', '.join([p for p in (parts_left, parts_right) if p])
    return addr or None


async def main(limit: int = 0):
    mongo_uri = os.getenv('MONGO_URI') or os.getenv('MONGO_URL') or 'mongodb://dinnerhopping:babyschnaps@10.8.0.2:27017/dinnerhopping?authSource=admin'
    db_name = os.getenv('MONGO_DB', 'dinnerhopping')
    client = MongoClient(mongo_uri)
    db = client[db_name]

    # Find users without lat/lon
    query = {'$or': [{'lat': {'$exists': False}}, {'lon': {'$exists': False}}, {'lat': None}, {'lon': None}]}
    cur = db.users.find(query)
    processed = 0
    updated = 0

    async def _process(doc: dict):
        nonlocal updated
        addr = _addr_from_struct(doc.get('address_struct'))
        if not addr:
            return
        latlon = await geocode(addr)
        if not latlon:
            return
        lat, lon = latlon
        update = {'lat': float(lat), 'lon': float(lon), 'geocoded_at': datetime.utcnow()}
        print(f"{doc.get('email')}: {addr} -> {lat:.6f},{lon:.6f}{' (dry-run)' if DRY_RUN else ''}")
        if not DRY_RUN:
            db.users.update_one({'_id': doc['_id']}, {'$set': update})
            updated += 1

    tasks = []
    for doc in cur:
        processed += 1
        if limit and updated >= limit:
            break
        tasks.append(_process(doc))
        # Pace tasks in small batches to avoid overwhelming services
        if len(tasks) >= 10:
            await asyncio.gather(*tasks)
            tasks = []
    if tasks:
        await asyncio.gather(*tasks)

    print(f"Processed: {processed}, Updated: {updated}")


if __name__ == '__main__':
    try:
        limit_arg = 0
        if len(sys.argv) > 1 and sys.argv[1].startswith('--limit'):
            try:
                _, val = sys.argv[1].split('=')
                limit_arg = int(val)
            except Exception:
                limit_arg = 0
        asyncio.run(main(limit=limit_arg))
    except KeyboardInterrupt:
        print("Aborted")

