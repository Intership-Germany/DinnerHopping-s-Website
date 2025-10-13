"""Geographical helper endpoints (ZIP code lookup, etc.)."""
from fastapi import APIRouter, Query
from typing import List, Any, Iterable, Optional, Set
import re
import os

from ..db import get_db

router = APIRouter()

_ZIP_PROJECTION = {
    '_id': 0,
    'plz_code': 1,
    'plz_name': 1,
    'plz_name_long': 1,
    'krs_code': 1,
    'lan_name': 1,
    'lan_code': 1,
    'krs_name': 1,
    'geo_point_2d': 1,
}

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

def _normalize_code_hints(raw_codes: Optional[Iterable[str]]) -> Set[str]:
    """Extract relevant administrative code prefixes (e.g., Kreis code) from raw inputs."""
    hints: Set[str] = set()
    if not raw_codes:
        return hints
    for raw in raw_codes:
        if raw is None:
            continue
        value = str(raw).strip()
        if not value:
            continue
        for part in re.split(r'[;,\s/]+', value):
            if not part:
                continue
            digits = ''.join(ch for ch in part if ch.isdigit())
            if not digits:
                continue
            variants = {digits}
            stripped = digits.lstrip('0')
            if stripped:
                variants.add(stripped)
            if len(digits) >= 5:
                variants.add(digits[:5])
                if stripped:
                    variants.add(stripped[:5])
            for var in variants:
                if var:
                    hints.add(var)
    return hints


def _matches_krs_hint(krs_code: Optional[str], code_hints: Set[str]) -> bool:
    if not krs_code or not code_hints:
        return False
    raw = str(krs_code).strip()
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if not digits:
        return False
    variants = {digits}
    stripped = digits.lstrip('0')
    if stripped:
        variants.add(stripped)
    for variant in list(variants):
        if len(variant) >= 5:
            variants.add(variant[:5])
    for hint in code_hints:
        if not hint:
            continue
        if any(variant.startswith(hint) or hint.startswith(variant) for variant in variants):
            return True
    return False


@router.get('/zip-codes')
async def get_zip_codes(
    city: str = Query(..., min_length=2, description="City/locality name (case-insensitive, approximate diacritic-insensitive match)"),
    codes: Optional[List[str]] = Query(None, description="Optional administrative code hints (repeatable)."),
    gisco_id: Optional[str] = Query(None, description="Optional GISCO identifier to help match administrative areas."),
    nuts_id: Optional[str] = Query(None, description="Optional NUTS identifier to help match administrative areas."),
):
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
    record_keys: Set[str] = set()

    def _append_record(document: dict[str, Any]) -> None:
        key = f"{document.get('plz_code', '')}|{document.get('krs_code', '')}"
        if key in record_keys:
            return
        record_keys.add(key)
        records.append(document)

    # Initial lookup by exact city match
    if use_fake:
        cursor = db.zip_codes.find({}, projection=_ZIP_PROJECTION)
        async for doc in cursor:
            pn = (doc.get('plz_name') or '').strip()
            if pn and regex_exact.search(pn):
                _append_record(dict(doc))
    else:
        cursor = db.zip_codes.find({'plz_name': {'$regex': regex_exact}}, projection=_ZIP_PROJECTION)
        async for doc in cursor:
            _append_record(dict(doc))

    seen = set(); zip_codes: List[str] = []
    for r in records:
        code = str(r.get('plz_code') or '').strip()
        if code and code not in seen:
            seen.add(code); zip_codes.append(code)

    # Fallback to loose matching if no codes found yet
    if not zip_codes:
        try:
            regex_loose = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex_loose = re.compile(re.escape(city.strip()), re.IGNORECASE)
        if use_fake:
            loose_cursor = db.zip_codes.find({}, projection=_ZIP_PROJECTION)
            async for doc in loose_cursor:
                pn = (doc.get('plz_name') or '').strip()
                if pn and regex_loose.search(pn):
                    loose_doc = dict(doc)
                    _append_record(loose_doc)
        else:
            loose_cursor = db.zip_codes.find({'plz_name': {'$regex': regex_loose}}, projection=_ZIP_PROJECTION)
            async for doc in loose_cursor:
                _append_record(dict(doc))
        for r in records:
            code = str(r.get('plz_code') or '').strip()
            if code and code not in seen:
                seen.add(code); zip_codes.append(code)

    raw_code_inputs: List[str] = []
    if codes:
        raw_code_inputs.extend(codes)
    if gisco_id:
        raw_code_inputs.append(gisco_id)
    if nuts_id:
        raw_code_inputs.append(nuts_id)
    code_hints = _normalize_code_hints(raw_code_inputs)

    if code_hints:
        if use_fake:
            cursor = db.zip_codes.find({}, projection=_ZIP_PROJECTION)
            async for doc in cursor:
                if _matches_krs_hint(doc.get('krs_code'), code_hints):
                    match_doc = dict(doc)
                    _append_record(match_doc)
                    code_value = str(match_doc.get('plz_code') or '').strip()
                    if code_value and code_value not in seen:
                        seen.add(code_value)
                        zip_codes.append(code_value)
        else:
            cursor = db.zip_codes.find({'krs_code': {'$exists': True, '$ne': None}}, projection=_ZIP_PROJECTION)
            async for doc in cursor:
                if _matches_krs_hint(doc.get('krs_code'), code_hints):
                    match_doc = dict(doc)
                    _append_record(match_doc)
                    code_value = str(match_doc.get('plz_code') or '').strip()
                    if code_value and code_value not in seen:
                        seen.add(code_value)
                        zip_codes.append(code_value)

    if len(records) > 50:
        records = records[:50]
    if len(zip_codes) > 50:
        zip_codes = zip_codes[:50]
    for r in records:
        if isinstance(r, dict):
            r.pop('_id', None)
    return {"city": city, "zip_codes": zip_codes, "count": len(zip_codes), "records": records}
