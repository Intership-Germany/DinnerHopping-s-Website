"""Central helpers for standardized ISO8601 UTC timestamps.

Storage standard:
- Always store timestamps as strings in the form YYYY-MM-DDTHH:MM:SS.mmm+00:00 (millisecond precision, UTC)
- Parsing helpers accept:
  * aware or naive datetime objects (assumed UTC if naive)
  * existing strings in one of several common ISO8601 variants
  * BSON datetimes coming from Mongo (as datetime.datetime)

Rationale:
- Consistent human readable format
- Lexicographically sortable
- Avoids mixing datetime objects and strings in collections

NOTE: If you rely on TTL indexes, you must keep a BSON datetime field separately.
"""
from __future__ import annotations
import datetime as _dt
import re
from typing import Any, Iterable

_ISO_MILLIS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$")

# Accepted fallback input patterns (without milliseconds, with Z, etc.) we will normalize from
_FALLBACK_PARSE_FORMATS: Iterable[str] = [
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S.%f+00:00",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",  # date only
]

def now_iso() -> str:
    """Return current UTC time as normalized ISO string with millisecond precision and +00:00 offset."""
    now = _dt.datetime.utcnow().replace(tzinfo=_dt.timezone.utc)
    # milliseconds
    ms = int(now.microsecond / 1000)
    base = now.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{ms:03d}+00:00"


def to_iso(value: Any) -> str | None:
    """Convert supported input to normalized ISO string or return None if input is falsy.

    Supported inputs: datetime (naive=UTC), string (various ISO forms), date.
    Raises ValueError for unsupported types or unparsable strings.
    """
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        else:
            dt = dt.astimezone(_dt.timezone.utc)
        ms = int(dt.microsecond / 1000)
        return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{ms:03d}+00:00"
    if isinstance(value, _dt.date):
        # Interpret date as midnight UTC
        dt = _dt.datetime(value.year, value.month, value.day, tzinfo=_dt.timezone.utc)
        return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.000+00:00"
    if isinstance(value, str):
        s = value.strip()
        if _ISO_MILLIS_RE.match(s):
            return s  # already normalized
        # replace trailing Z with +00:00 for consistent parsing
        if s.endswith('Z'):
            # Try parsing with microseconds first
            for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
                try:
                    dt = _dt.datetime.strptime(s, fmt).replace(tzinfo=_dt.timezone.utc)
                    ms = int(dt.microsecond / 1000)
                    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{ms:03d}+00:00"
                except ValueError:
                    pass
        # Try each fallback format
        for fmt in _FALLBACK_PARSE_FORMATS:
            try:
                dt = _dt.datetime.strptime(s, fmt)
                if fmt == "%Y-%m-%d":
                    dt = _dt.datetime(dt.year, dt.month, dt.day)
                dt = dt.replace(tzinfo=_dt.timezone.utc)
                ms = int(dt.microsecond / 1000)
                return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{ms:03d}+00:00"
            except ValueError:
                continue
        raise ValueError(f"Unrecognized datetime string format: {value!r}")
    raise ValueError(f"Unsupported datetime value type: {type(value)}")


def parse_iso(s: str | None) -> _dt.datetime | None:
    """Parse a normalized ISO string (or accepted variant) into a timezone-aware UTC datetime.
    Returns None for falsy input.
    """
    if not s:
        return None
    normalized = to_iso(s)  # will raise if invalid
    # normalized guaranteed pattern: YYYY-MM-DDTHH:MM:SS.mmm+00:00
    base, _plus, _offset = normalized.partition('+')  # we know offset is +00:00
    # split millis
    main, _dot, milli_part = base.partition('.')
    dt = _dt.datetime.strptime(main, "%Y-%m-%dT%H:%M:%S")
    micro = int(milli_part) * 1000
    return dt.replace(microsecond=micro, tzinfo=_dt.timezone.utc)

__all__ = ["now_iso", "to_iso", "parse_iso"]
