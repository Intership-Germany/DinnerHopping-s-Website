#!/usr/bin/env python3
"""Search for algorithm-parameter environment configurations that optimise matching results."""

from __future__ import annotations

import argparse
import asyncio
import itertools
import math
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence


@dataclass
class AlgorithmOutcome:
    name: str
    metrics: Dict[str, float]
    objective: float
    average_duration: float
    runs: int


@dataclass
class EvaluationResult:
    env: Dict[str, str]
    objective: float
    outcomes: List[AlgorithmOutcome]


def _ensure_app_on_path() -> None:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_app_on_path()

from app import db as db_mod  # noqa: E402
from app.services.matching import algorithms as algorithms_mod  # noqa: E402
from app.services.matching import config as config_mod  # noqa: E402


OBJECTIVE_CHOICES = {"score", "travel", "warnings"}
COMBINE_CHOICES = {"mean", "sum", "min", "max"}

ALGORITHM_PARAM_KEYS = {
    "MATCH_W_DUP",
    "MATCH_W_DIST",
    "MATCH_W_PREF",
    "MATCH_W_ALLERGY",
    "MATCH_W_DESIRED_HOST",
    "MATCH_W_TRANS",
    "MATCH_W_FINAL_PARTY",
    "MATCH_W_PHASE_ORDER",
    "MATCH_W_CAPABILITY",
    "MATCH_HOST_CANDIDATES",
    "MATCH_GUEST_CANDIDATES",
    "MATCH_ALLOW_TEAM_SPLITS",
    "MATCH_PHASES",
}

DEFAULT_PARAM_SWEEPS: Dict[str, List[str]] = {
    "MATCH_W_DIST": ["0.25", "0.5", "0.75"],
    "MATCH_W_PREF": ["1.5", "2", "3"],
    "MATCH_W_CAPABILITY": ["3", "5", "7"],
    "MATCH_HOST_CANDIDATES": ["2", "3", "4"],
    "MATCH_GUEST_CANDIDATES": ["8", "10", "12"],
    "MATCH_ALLOW_TEAM_SPLITS": ["0", "1"],
}


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate environment sweeps to optimise matching algorithm metrics.",
    )
    parser.add_argument("--event", required=True, help="Event identifier used by the algorithms (Mongo ObjectId as string).")
    parser.add_argument(
        "--algorithms",
        nargs="+",
        default=["greedy"],
        help="Algorithm names to evaluate (default: greedy).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of recorded runs per configuration (default: 1).",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=0,
        help="Warmup executions per configuration that are discarded (default: 0).",
    )
    parser.add_argument(
        "--sweep",
        nargs="*",
        default=[],
        help="Environment sweeps in the form NAME=v1,v2 (multiple allowed).",
    )
    parser.add_argument(
        "--objective",
        default="score",
        choices=sorted(OBJECTIVE_CHOICES),
        help="Metric to optimise: score (maximise), travel (minimise), warnings (minimise).",
    )
    parser.add_argument(
        "--combine",
        default="mean",
        choices=sorted(COMBINE_CHOICES),
        help="How to combine objectives across algorithms (default: mean).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of top configurations to show in the summary (default: 5).",
    )
    parser.add_argument(
        "--no-defaults",
        action="store_true",
        help="Disable the built-in parameter sweep presets when no --sweep is provided.",
    )
    return parser.parse_args(argv)


def parse_sweeps(items: Iterable[str]) -> Dict[str, List[str]]:
    sweeps: Dict[str, List[str]] = {}
    for raw in items:
        key, sep, values = raw.partition("=")
        key = key.strip()
        if not key or sep != "=":
            raise ValueError(f"Invalid sweep expression: {raw}")
        value_list = [value.strip() for value in values.split(",") if value.strip()]
        if not value_list:
            raise ValueError(f"Sweep for {key} has no values")
        sweeps[key] = value_list
    return sweeps


def validate_sweeps(sweeps: Dict[str, List[str]], allowed: set[str]) -> None:
    invalid = sorted(set(sweeps) - allowed)
    if invalid:
        joined = ", ".join(invalid)
        raise ValueError(f"Unsupported sweep keys for this script: {joined}")


def iter_env_combinations(sweeps: Dict[str, List[str]]) -> Iterable[Dict[str, str]]:
    if not sweeps:
        yield {}
        return
    keys = sorted(sweeps)
    for combo in itertools.product(*(sweeps[key] for key in keys)):
        yield {key: value for key, value in zip(keys, combo)}


def apply_env_overrides(overrides: Dict[str, str]) -> Dict[str, Optional[str]]:
    snapshot: Dict[str, Optional[str]] = {}
    for key, value in overrides.items():
        snapshot[key] = os.environ.get(key)
        os.environ[key] = value
    return snapshot


def restore_environment(snapshot: Dict[str, Optional[str]]) -> None:
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def clear_config_caches() -> None:
    config_mod.weight_defaults.cache_clear()
    config_mod.host_candidate_limit.cache_clear()
    config_mod.guest_candidate_limit.cache_clear()
    config_mod.allow_team_splits.cache_clear()
    config_mod.routing_parallelism.cache_clear()
    config_mod.geocode_parallelism.cache_clear()
    config_mod.phases.cache_clear()
    config_mod.meal_time_defaults.cache_clear()


def compute_objective(metric: Dict[str, float], mode: str) -> float:
    if not metric:
        return float("-inf")
    if mode == "score":
        return float(metric.get("aggregate_group_score", 0.0))
    if mode == "travel":
        return -float(metric.get("total_travel_seconds", 0.0))
    if mode == "warnings":
        return -float(metric.get("groups_with_warnings", 0.0))
    raise ValueError(f"Unsupported objective: {mode}")


def combine_scores(scores: List[float], mode: str) -> float:
    if not scores:
        return float("-inf")
    if mode == "mean":
        return statistics.fmean(scores)
    if mode == "sum":
        return sum(scores)
    if mode == "min":
        return min(scores)
    if mode == "max":
        return max(scores)
    raise ValueError(f"Unsupported combine mode: {mode}")


def average_metrics(collection: List[Dict[str, float]]) -> Dict[str, float]:
    if not collection:
        return {}
    keys = set().union(*(metrics.keys() for metrics in collection))
    averaged: Dict[str, float] = {}
    for key in keys:
        values = [float(metrics.get(key, 0.0)) for metrics in collection]
        averaged[key] = statistics.fmean(values)
    return averaged


async def run_single_algorithm(event_id: str, algorithm: str, weights: Dict[str, float]) -> dict:
    results = await algorithms_mod.run_algorithms(event_id, algorithms=[algorithm], weights=weights)
    if not results:
        raise RuntimeError(f"Algorithm {algorithm} produced no result")
    return results[0]


async def evaluate_algorithm(event_id: str, algorithm: str, runs: int, warmup: int, objective: str) -> AlgorithmOutcome:
    weights = config_mod.weight_defaults()
    total_runs = max(0, warmup) + max(1, runs)
    recorded_metrics: List[Dict[str, float]] = []
    recorded_durations: List[float] = []
    for index in range(total_runs):
        start = time.perf_counter()
        result = await run_single_algorithm(event_id, algorithm, weights)
        duration = time.perf_counter() - start
        if index >= warmup:
            recorded_metrics.append(result.get("metrics", {}))
            recorded_durations.append(duration)
    averaged_metrics = average_metrics(recorded_metrics)
    objective_value = compute_objective(averaged_metrics, mode=objective)
    average_duration = statistics.fmean(recorded_durations) if recorded_durations else float("nan")
    return AlgorithmOutcome(
        name=algorithm,
        metrics=averaged_metrics,
        objective=objective_value,
        average_duration=average_duration,
        runs=len(recorded_durations),
    )


async def optimise(event_id: str, algorithms: Sequence[str], runs: int, warmup: int, sweeps: Dict[str, List[str]], objective: str, combine: str) -> List[EvaluationResult]:
    await db_mod.connect()
    results: List[EvaluationResult] = []
    try:
        for env_overrides in iter_env_combinations(sweeps):
            snapshot = apply_env_overrides(env_overrides)
            clear_config_caches()
            try:
                tasks = [
                    asyncio.create_task(
                        evaluate_algorithm(event_id, algorithm, runs=runs, warmup=warmup, objective=objective),
                    )
                    for algorithm in algorithms
                ]
                outcomes = list(await asyncio.gather(*tasks))
                objective_scores = [outcome.objective for outcome in outcomes if math.isfinite(outcome.objective)]
                combined = combine_scores(objective_scores, combine)
                results.append(EvaluationResult(env=dict(env_overrides), objective=combined, outcomes=outcomes))
            finally:
                restore_environment(snapshot)
                clear_config_caches()
    finally:
        await db_mod.close()
    return results


def format_env(env: Dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(env.items())) or "(baseline)"


def print_summary(results: List[EvaluationResult], limit: int) -> None:
    if not results:
        print("No optimisation data collected.")
        return
    ranked = sorted(results, key=lambda item: item.objective, reverse=True)
    best = ranked[0]
    print("\nBest configuration:")
    print(f"  Environment : {format_env(best.env)}")
    print(f"  Objective   : {best.objective:.3f}")
    for outcome in best.outcomes:
        print(
            f"    - {outcome.name}: objective={outcome.objective:.3f} avg_duration={outcome.average_duration:.3f}s runs={outcome.runs} metrics={outcome.metrics}",
        )
    print("\nTop configurations:")
    header = f"{'Rank':>4}  {'Objective':>10}  {'Environment':<50}"
    print(header)
    print("-" * len(header))
    for index, item in enumerate(ranked[: max(1, limit)], start=1):
        print(f"{index:>4}  {item.objective:>10.3f}  {format_env(item.env):<50}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
        sweeps = parse_sweeps(args.sweep)
        if not sweeps and not args.no_defaults:
            sweeps = {key: values[:] for key, values in DEFAULT_PARAM_SWEEPS.items()}
        validate_sweeps(sweeps, ALGORITHM_PARAM_KEYS)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        results = asyncio.run(
            optimise(
                event_id=args.event,
                algorithms=args.algorithms,
                runs=args.runs,
                warmup=args.warmup,
                sweeps=sweeps,
                objective=args.objective,
                combine=args.combine,
            ),
        )
    except Exception as exc:
        print(f"Optimisation failed: {exc}", file=sys.stderr)
        return 1

    print_summary(results, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
