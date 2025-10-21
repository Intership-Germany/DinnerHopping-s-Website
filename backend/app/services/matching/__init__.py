from __future__ import annotations

from .algorithms import ALGORITHMS, algo_greedy, algo_local_search, algo_random, run_algorithms
from .config import host_candidate_limit, meal_time_defaults, phases, travel_fast_mode, weight_defaults
from .data import (
    augment_emails_map_with_splits as _augment_emails_map_with_splits,
    build_teams as _build_teams,
    team_emails_map as _team_emails_map,
    user_address_string as _user_address_string,
)
from .metrics import compute_metrics as _compute_metrics
from .operations import (
    finalize_and_generate_plans,
    generate_plans_from_matches,
    list_issues,
    mark_finalized,
    persist_match_proposal,
    process_refunds,
    refunds_overview,
)
from .paths import compute_team_paths
from .grouping import (
    compatible_diet,
    phase_groups,
    score_group_phase as _score_group_phase,
    travel_time_for_phase as _travel_time_for_phase,
)
from .units import (
    apply_forced_pairs,
    apply_minimal_splits,
    apply_required_splits,
    build_units_from_teams,
    emails_to_unit_index,
    group_units_in_triads as _group_units_in_triads,
    merge_two_solos,
)
from .jobs import (
    enqueue_matching_job,
    get_matching_job,
    list_matching_jobs,
)
from .validation import (
    validate_matching_constraints,
    check_team_coverage,
    analyze_dietary_compatibility,
    analyze_distance_distribution,
)

__all__ = [
    'ALGORITHMS',
    'algo_greedy',
    'algo_local_search',
    'algo_random',
    'run_algorithms',
    'persist_match_proposal',
    'mark_finalized',
    'list_issues',
    'refunds_overview',
    'finalize_and_generate_plans',
    'generate_plans_from_matches',
    'compute_team_paths',
    'process_refunds',
    '_build_teams',
    '_team_emails_map',
    '_augment_emails_map_with_splits',
    '_score_group_phase',
    '_travel_time_for_phase',
    '_compute_metrics',
    '_group_units_in_triads',
    '_user_address_string',
    'enqueue_matching_job',
    'get_matching_job',
    'list_matching_jobs',
    'validate_matching_constraints',
    'check_team_coverage',
    'analyze_dietary_compatibility',
    'analyze_distance_distribution',
]
