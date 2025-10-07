"""Geographical helper endpoints (ZIP code lookup, etc.)."""
from fastapi import APIRouter, Query
from typing import List, Any
import re
import os

from ..db import get_db

router = APIRouter()

# Map German umlauts and ß to regex groups for diacritic-insensitive search
_DIACRITIC_MAP = {
    'a': '[aä]', 'ä': '[aä]',
    'o': '[oö]', 'ö': '[oö]',
    'u': '[uü]', 'ü': '[uü]',
    's': '[sß]', 'ß': '(?:ß|ss)',
}

def _city_to_regex_pattern(city: str) -> str:
    out = []
    for ch in city:
        base = ch.lower()
        if base in _DIACRITIC_MAP:
            out.append(_DIACRITIC_MAP[base])
        else:
            # Escape regex meta characters
            if re.escape(base) != base:
                out.append(re.escape(base))
            else:
                out.append(base)
    return ''.join(out)

@router.get('/zip-codes')
async def get_zip_codes(city: str = Query(..., min_length=2, description="City/locality name (case-insensitive, approximate diacritic-insensitive match)")):
    db = get_db()
    if db is None:
        return {"city": city, "zip_codes": [], "count": 0, "records": []}

    pattern = _city_to_regex_pattern(city.strip())
    try:
        regex_exact = re.compile(f'^{pattern}$', re.IGNORECASE)
    except re.error:
        regex_exact = re.compile('^' + re.escape(city.strip()) + '$', re.IGNORECASE)

    use_fake = bool(os.getenv('USE_FAKE_DB_FOR_TESTS'))
    records: List[Any] = []

    if use_fake:
        # Fake DB does not implement $regex; fetch all and filter in memory
        cursor = db.zip_codes.find({}, projection={
            '_id': 0,
            'plz_code': 1,
            'plz_name': 1,
            'plz_name_long': 1,
            'krs_code': 1,
            'lan_name': 1,
            'lan_code': 1,
            'krs_name': 1,
            'geo_point_2d': 1,
        })
        async for doc in cursor:
            pn = (doc.get('plz_name') or '').strip()
            if regex_exact.search(pn):
                records.append(doc)
    else:
        cursor = db.zip_codes.find({'plz_name': {'$regex': regex_exact}}, projection={
            '_id': 0,
            'plz_code': 1,
            'plz_name': 1,
            'plz_name_long': 1,
            'krs_code': 1,
            'lan_name': 1,
            'lan_code': 1,
            'krs_name': 1,
            'geo_point_2d': 1,
        })
        async for doc in cursor:
            records.append(doc)

    seen = set(); zip_codes: List[str] = []
    for r in records:
        code = str(r.get('plz_code') or '').strip()
        if code and code not in seen:
            seen.add(code); zip_codes.append(code)

    if not zip_codes:
        try:
            regex_loose = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex_loose = re.compile(re.escape(city.strip()), re.IGNORECASE)
        if use_fake:
            loose_cursor = db.zip_codes.find({}, projection={
                '_id': 0,'plz_code':1,'plz_name':1,'plz_name_long':1,'krs_code':1,'lan_name':1,'lan_code':1,'krs_name':1,'geo_point_2d':1,
            })
            loose_records: List[Any] = []
            async for doc in loose_cursor:
                pn = (doc.get('plz_name') or '').strip()
                if regex_loose.search(pn):
                    loose_records.append(doc)
        else:
            loose_cursor = db.zip_codes.find({'plz_name': {'$regex': regex_loose}}, projection={
                '_id': 0,'plz_code':1,'plz_name':1,'plz_name_long':1,'krs_code':1,'lan_name':1,'lan_code':1,'krs_name':1,'geo_point_2d':1,
            })
            loose_records = []
            async for doc in loose_cursor:
                loose_records.append(doc)
        for r in loose_records:
            code = str(r.get('plz_code') or '').strip()
            if code and code not in seen:
                seen.add(code); zip_codes.append(code)
        if not records and loose_records:
            records = loose_records
    if len(records) > 50: records = records[:50]
    if len(zip_codes) > 50: zip_codes = zip_codes[:50]
    # Before returning, strip any ObjectId to avoid serialization issues when using Fake DB
    for r in records:
        if isinstance(r, dict):
            r.pop('_id', None)
    return {"city": city, "zip_codes": zip_codes, "count": len(zip_codes), "records": records}
