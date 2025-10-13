from __future__ import annotations

import os
from functools import lru_cache
from typing import Dict, List

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _float_env(name: str, default: str) -> float:
    raw = os.getenv(name, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _int_env(name: str, default: str) -> int:
    raw = os.getenv(name, default)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return int(default)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in _TRUE_VALUES


@lru_cache(maxsize=1)
def weight_defaults() -> Dict[str, float]:
    """Return default scoring weights sourced from the environment."""
    return {
        "dup": _float_env("MATCH_W_DUP", "1000"),
        "dist": _float_env("MATCH_W_DIST", "1"),
        "pref": _float_env("MATCH_W_PREF", "5"),
        "allergy": _float_env("MATCH_W_ALLERGY", "3"),
        "desired_host": _float_env("MATCH_W_DESIRED_HOST", "10"),
        "trans": _float_env("MATCH_W_TRANS", "0.5"),
        "final_party": _float_env("MATCH_W_FINAL_PARTY", "0.5"),
        "phase_order": _float_env("MATCH_W_PHASE_ORDER", "0.0"),
        "cap_penalty": _float_env("MATCH_W_CAPABILITY", "5"),
    }


@lru_cache(maxsize=1)
def host_candidate_limit() -> int:
    return max(0, _int_env("MATCH_HOST_CANDIDATES", "0"))


def geocode_missing_enabled() -> bool:
    return _bool_env("MATCH_GEOCODE_ON_MISSING", True)


def travel_fast_mode() -> bool:
    return _bool_env("MATCH_TRAVEL_FAST", False)


@lru_cache(maxsize=1)
def phases() -> List[str]:
    raw = os.getenv("MATCH_PHASES")
    if not raw:
        return ["appetizer", "main", "dessert"]
    parsed = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return parsed or ["appetizer", "main", "dessert"]


@lru_cache(maxsize=1)
def meal_time_defaults() -> Dict[str, str]:
    defaults = {
        "appetizer": os.getenv("MATCH_MEAL_TIME_APPETIZER", "18:00"),
        "main": os.getenv("MATCH_MEAL_TIME_MAIN", "20:00"),
        "dessert": os.getenv("MATCH_MEAL_TIME_DESSERT", "22:00"),
    }
    # ensure each phase has a value, fallback to sensible default if missing
    return {phase: defaults.get(phase, "20:00") for phase in phases()}


def algorithm_seed(name: str, default: int) -> int:
    env_name = f"MATCH_SEED_{name.upper()}"
    raw = os.getenv(env_name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default
