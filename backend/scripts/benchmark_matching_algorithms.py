#!/usr/bin/env python3
"""Benchmark matching algorithms focusing on performance-related environment knobs."""

from __future__ import annotations

import argparse
import asyncio
import itertools
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence


@dataclass
class BenchmarkResult:
    env: Dict[str, str]
    algorithm: str
    durations: List[float]

    @property
    def count(self) -> int:
        return len(self.durations)

    @property
    def average(self) -> float:
        return statistics.fmean(self.durations) if self.durations else float("nan")

    @property
    def minimum(self) -> float:
        return min(self.durations) if self.durations else float("nan")

    @property
    def maximum(self) -> float:
        return max(self.durations) if self.durations else float("nan")


def _ensure_app_on_path() -> None:
    """Ensure the backend/app package is importable when running the script directly."""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_app_on_path()

from app import db as db_mod  # noqa: E402
from app.services.matching import algorithms as algorithms_mod  # noqa: E402
from app.services.matching import config as config_mod  # noqa: E402


PERFORMANCE_KEYS = {
    "MATCH_ROUTING_PARALLELISM",
    "MATCH_GEOCODE_PARALLELISM",
    "MATCH_TRAVEL_FAST",
}

DEFAULT_PERFORMANCE_SWEEPS: Dict[str, List[str]] = {
    "MATCH_ROUTING_PARALLELISM": ["2", "4", "6", "8"],
    "MATCH_GEOCODE_PARALLELISM": ["2", "4"],
    "MATCH_TRAVEL_FAST": ["0", "1"],
}


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure execution time of matching algorithms while sweeping performance environment variables.",
    )
    parser.add_argument("--event", required=True, help="Event identifier used by the algorithms (Mongo ObjectId as string).")
    parser.add_argument(
        "--algorithms",
        nargs="+",
        default=["greedy"],
        help="Algorithm names to benchmark (default: greedy).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of executions per configuration (default: 1).",
    )
    parser.add_argument(
        "--sweep",
        nargs="*",
        default=[],
        help="Environment sweeps in the form NAME=v1,v2 (multiple allowed).",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=0,
        help="Optional warmup runs per configuration that are discarded (default: 0).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-run details, only print summary rows.",
    )
    parser.add_argument(
        "--no-defaults",
        action="store_true",
        help="Disable the built-in performance sweep presets when no --sweep is provided.",
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
    for combination in itertools.product(*(sweeps[key] for key in keys)):
        yield {key: value for key, value in zip(keys, combination)}


def apply_env_overrides(overrides: Dict[str, str]) -> Dict[str, Optional[str]]:
    previous: Dict[str, Optional[str]] = {}
    for key, value in overrides.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    return previous


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


async def run_single_algorithm(event_id: str, algorithm: str, weights: Dict[str, float]) -> dict:
    results = await algorithms_mod.run_algorithms(
        event_id,
        algorithms=[algorithm],
        weights=weights,
    )
    if not results:
        raise RuntimeError(f"Algorithm {algorithm} produced no result")
    return results[0]


async def measure_algorithm(event_id: str, algorithm: str, runs: int, warmup: int) -> List[float]:
    weights = config_mod.weight_defaults()
    durations: List[float] = []
    total_runs = max(0, warmup) + max(1, runs)
    for index in range(total_runs):
        start = time.perf_counter()
        await run_single_algorithm(event_id, algorithm, weights)
        duration = time.perf_counter() - start
        if index >= warmup:
            durations.append(duration)
    return durations


async def benchmark(event_id: str, algorithms: Sequence[str], runs: int, warmup: int, sweeps: Dict[str, List[str]], quiet: bool) -> List[BenchmarkResult]:
    await db_mod.connect()
    results: List[BenchmarkResult] = []
    try:
        for env_overrides in iter_env_combinations(sweeps):
            snapshot = apply_env_overrides(env_overrides)
            clear_config_caches()
            try:
                if not quiet:
                    pretty = ", ".join(f"{k}={v}" for k, v in env_overrides.items()) or "(baseline)"
                    print(f"\nConfiguration: {pretty}")
                tasks = [
                    asyncio.create_task(
                        measure_algorithm(event_id, algorithm, runs=runs, warmup=warmup),
                    )
                    for algorithm in algorithms
                ]
                durations_per_algorithm = await asyncio.gather(*tasks)
                for algorithm, durations in zip(algorithms, durations_per_algorithm):
                    result = BenchmarkResult(env=dict(env_overrides), algorithm=algorithm, durations=durations)
                    results.append(result)
                    if quiet:
                        continue
                    if not durations:
                        print(f"  - {algorithm}: no recorded durations")
                        continue
                    avg = result.average
                    print(
                        f"  - {algorithm}: avg={avg:.3f}s min={result.minimum:.3f}s max={result.maximum:.3f}s from {result.count} run(s)",
                    )
            finally:
                restore_environment(snapshot)
                clear_config_caches()
    finally:
        await db_mod.close()
    return results


def print_summary(results: Sequence[BenchmarkResult]) -> None:
    if not results:
        print("No benchmark data collected.")
        return
    header = f"{'Algorithm':<12}  {'Environment':<40}  {'Runs':>4}  {'Avg(s)':>10}  {'Min(s)':>10}  {'Max(s)':>10}"
    print("\n" + header)
    print("-" * len(header))
    for item in results:
        env_string = ", ".join(f"{k}={v}" for k, v in sorted(item.env.items())) or "(baseline)"
        print(
            f"{item.algorithm:<12}  {env_string:<40}  {item.count:>4}  {item.average:>10.3f}  {item.minimum:>10.3f}  {item.maximum:>10.3f}",
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
        sweeps = parse_sweeps(args.sweep)
        if not sweeps and not args.no_defaults:
            sweeps = {key: values[:] for key, values in DEFAULT_PERFORMANCE_SWEEPS.items()}
        validate_sweeps(sweeps, PERFORMANCE_KEYS)
    except Exception as exc:  # parsing errors should surface clearly
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        results = asyncio.run(
            benchmark(
                event_id=args.event,
                algorithms=args.algorithms,
                runs=args.runs,
                warmup=args.warmup,
                sweeps=sweeps,
                quiet=args.quiet,
            ),
        )
    except Exception as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 1

    print_summary(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
