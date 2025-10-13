from __future__ import annotations

from typing import Dict, List


def compute_metrics(groups: List[dict], weights: Dict[str, float]) -> dict:
    total_travel = sum(float(group.get('travel_seconds') or 0.0) for group in groups)
    total_score = sum(float(group.get('score') or 0.0) for group in groups)
    issues = sum(1 for group in groups if group.get('warnings'))
    return {
        'total_travel_seconds': total_travel,
        'aggregate_group_score': total_score,
        'groups_with_warnings': issues,
    }
