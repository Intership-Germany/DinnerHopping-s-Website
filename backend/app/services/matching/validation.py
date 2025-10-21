from __future__ import annotations

import logging
from typing import Dict, List, Set, Tuple

logger = logging.getLogger(__name__)


def validate_matching_constraints(groups: List[dict]) -> Dict[str, any]:
    """
    Validate that a set of groups satisfies all matching constraints.
    
    Constraints checked:
    1. Each team appears in exactly 3 groups (one per phase)
    2. Each team hosts exactly once
    3. Each team is a guest exactly twice
    4. No team meets another team more than once
    5. Each group has exactly 3 teams (1 host + 2 guests)
    6. No team appears in the same group as both host and guest
    
    Args:
        groups: List of group dictionaries with phase, host_team_id, guest_team_ids
        
    Returns:
        Dictionary with validation results:
        {
            'valid': bool,
            'errors': List[str],
            'warnings': List[str],
            'statistics': dict,
        }
    """
    errors: List[str] = []
    warnings: List[str] = []
    
    # Track team participation
    team_appearances: Dict[str, int] = {}  # team_id -> count of groups
    team_host_count: Dict[str, int] = {}   # team_id -> count of times hosting
    team_guest_count: Dict[str, int] = {}  # team_id -> count of times as guest
    team_phases: Dict[str, Set[str]] = {}  # team_id -> set of phases participated in
    
    # Track pair meetings
    pair_meetings: Dict[Tuple[str, str], List[str]] = {}  # (team_a, team_b) -> [phase1, phase2, ...]
    
    # Track groups by phase
    groups_by_phase: Dict[str, List[dict]] = {}
    
    # Validate group structure
    for idx, group in enumerate(groups):
        phase = group.get('phase')
        host_id = group.get('host_team_id')
        guest_ids = group.get('guest_team_ids') or []
        
        if not phase:
            errors.append(f"Group {idx}: missing phase")
            continue
            
        groups_by_phase.setdefault(phase, []).append(group)
        
        if not host_id:
            errors.append(f"Group {idx} (phase {phase}): missing host_team_id")
            continue
            
        if len(guest_ids) != 2:
            errors.append(
                f"Group {idx} (phase {phase}): expected 2 guests, got {len(guest_ids)}"
            )
            
        # Check for host appearing as guest in same group
        if host_id in guest_ids:
            errors.append(
                f"Group {idx} (phase {phase}): host {host_id} also appears as guest"
            )
            
        # Track all team IDs in this group
        all_team_ids = [host_id] + list(guest_ids)
        
        # Check for duplicate team IDs within group
        if len(all_team_ids) != len(set(all_team_ids)):
            errors.append(
                f"Group {idx} (phase {phase}): duplicate team IDs within group"
            )
            
        # Update team statistics
        for team_id in all_team_ids:
            team_appearances[team_id] = team_appearances.get(team_id, 0) + 1
            team_phases.setdefault(team_id, set()).add(phase)
            
        team_host_count[host_id] = team_host_count.get(host_id, 0) + 1
        
        for guest_id in guest_ids:
            team_guest_count[guest_id] = team_guest_count.get(guest_id, 0) + 1
            
        # Track pair meetings
        for i, team_a in enumerate(all_team_ids):
            for team_b in all_team_ids[i+1:]:
                pair_key = _pair_key(team_a, team_b)
                pair_meetings.setdefault(pair_key, []).append(phase)
    
    # Validate constraint: each team appears in exactly 3 groups
    for team_id, count in team_appearances.items():
        if count != 3:
            errors.append(
                f"Team {team_id}: appears in {count} groups (expected 3)"
            )
    
    # Validate constraint: each team hosts exactly once
    for team_id, count in team_host_count.items():
        if count != 1:
            errors.append(
                f"Team {team_id}: hosts {count} times (expected 1)"
            )
            
    # Validate constraint: each team is guest exactly twice
    for team_id in team_appearances.keys():
        guest_count = team_guest_count.get(team_id, 0)
        if guest_count != 2:
            errors.append(
                f"Team {team_id}: is guest {guest_count} times (expected 2)"
            )
    
    # Validate constraint: no team meets another team more than once
    duplicate_pairs: List[Tuple[str, str, int]] = []
    for pair, phases in pair_meetings.items():
        if len(phases) > 1:
            errors.append(
                f"Teams {pair[0]} and {pair[1]} meet {len(phases)} times "
                f"(phases: {', '.join(phases)})"
            )
            duplicate_pairs.append((pair[0], pair[1], len(phases)))
    
    # Validate constraint: each team participates in all 3 phases
    expected_phases = {'appetizer', 'main', 'dessert'}
    for team_id, phases in team_phases.items():
        missing_phases = expected_phases - phases
        if missing_phases:
            errors.append(
                f"Team {team_id}: missing from phases {', '.join(missing_phases)}"
            )
    
    # Check for phase balance
    phase_counts = {phase: len(grps) for phase, grps in groups_by_phase.items()}
    if len(set(phase_counts.values())) > 1:
        warnings.append(
            f"Unbalanced phase distribution: {phase_counts}"
        )
    
    # Compute statistics
    statistics = {
        'total_groups': len(groups),
        'total_teams': len(team_appearances),
        'groups_by_phase': phase_counts,
        'duplicate_pair_count': len(duplicate_pairs),
        'teams_with_errors': len([
            tid for tid, count in team_appearances.items()
            if count != 3 or team_host_count.get(tid, 0) != 1 or team_guest_count.get(tid, 0) != 2
        ]),
    }
    
    logger.info(
        "Matching validation: %d groups, %d teams, %d errors, %d warnings",
        len(groups), len(team_appearances), len(errors), len(warnings)
    )
    
    return {
        'valid': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
        'statistics': statistics,
    }


def _pair_key(team_a: str, team_b: str) -> Tuple[str, str]:
    """Return normalized pair key (sorted)."""
    return (team_a, team_b) if team_a <= team_b else (team_b, team_a)


def check_team_coverage(groups: List[dict], expected_teams: Set[str]) -> Dict[str, any]:
    """
    Check that all expected teams are included in the matching.
    
    Args:
        groups: List of group dictionaries
        expected_teams: Set of team IDs that should be matched
        
    Returns:
        Dictionary with coverage analysis:
        {
            'complete': bool,
            'matched_teams': Set[str],
            'missing_teams': Set[str],
            'unexpected_teams': Set[str],
        }
    """
    matched_teams: Set[str] = set()
    
    for group in groups:
        host_id = group.get('host_team_id')
        guest_ids = group.get('guest_team_ids') or []
        
        if host_id:
            matched_teams.add(host_id)
        for guest_id in guest_ids:
            matched_teams.add(guest_id)
    
    missing_teams = expected_teams - matched_teams
    unexpected_teams = matched_teams - expected_teams
    
    return {
        'complete': len(missing_teams) == 0,
        'matched_teams': matched_teams,
        'missing_teams': missing_teams,
        'unexpected_teams': unexpected_teams,
        'coverage_ratio': len(matched_teams & expected_teams) / len(expected_teams) if expected_teams else 1.0,
    }


def analyze_dietary_compatibility(groups: List[dict], team_details: Dict[str, dict]) -> Dict[str, any]:
    """
    Analyze dietary compatibility issues across all groups.
    
    Args:
        groups: List of group dictionaries
        team_details: Dictionary mapping team_id to team details (including team_diet)
        
    Returns:
        Dictionary with dietary analysis:
        {
            'total_conflicts': int,
            'conflicts_by_phase': Dict[str, int],
            'conflict_details': List[dict],
        }
    """
    conflicts: List[dict] = []
    conflicts_by_phase: Dict[str, int] = {}
    
    for idx, group in enumerate(groups):
        phase = group.get('phase', 'unknown')
        host_id = group.get('host_team_id')
        guest_ids = group.get('guest_team_ids') or []
        
        if not host_id or host_id not in team_details:
            continue
            
        host_diet = team_details[host_id].get('team_diet', 'omnivore')
        
        for guest_id in guest_ids:
            if guest_id not in team_details:
                continue
                
            guest_diet = team_details[guest_id].get('team_diet', 'omnivore')
            
            if not _compatible_diet(host_diet, guest_diet):
                conflicts.append({
                    'group_idx': idx,
                    'phase': phase,
                    'host_id': host_id,
                    'host_diet': host_diet,
                    'guest_id': guest_id,
                    'guest_diet': guest_diet,
                })
                conflicts_by_phase[phase] = conflicts_by_phase.get(phase, 0) + 1
    
    return {
        'total_conflicts': len(conflicts),
        'conflicts_by_phase': conflicts_by_phase,
        'conflict_details': conflicts,
    }


def _compatible_diet(host_diet: str, guest_diet: str) -> bool:
    """Check if host diet can accommodate guest diet."""
    host = (host_diet or 'omnivore').lower()
    guest = (guest_diet or 'omnivore').lower()
    
    # Omnivore hosts cannot prepare vegan meals
    if host == 'omnivore' and guest == 'vegan':
        return False
    
    # Vegetarian hosts cannot prepare vegan meals
    if host == 'vegetarian' and guest == 'vegan':
        return False
    
    return True


def analyze_distance_distribution(groups: List[dict]) -> Dict[str, any]:
    """
    Analyze travel distance/time distribution across groups.
    
    Args:
        groups: List of group dictionaries with travel_seconds
        
    Returns:
        Dictionary with distance statistics:
        {
            'total_travel_seconds': float,
            'mean_travel_seconds': float,
            'median_travel_seconds': float,
            'max_travel_seconds': float,
            'groups_by_phase': Dict[str, dict],
        }
    """
    travel_times = [float(g.get('travel_seconds', 0)) for g in groups]
    travel_times = [t for t in travel_times if t > 0]
    
    if not travel_times:
        return {
            'total_travel_seconds': 0.0,
            'mean_travel_seconds': 0.0,
            'median_travel_seconds': 0.0,
            'max_travel_seconds': 0.0,
            'groups_by_phase': {},
        }
    
    travel_times_sorted = sorted(travel_times)
    n = len(travel_times_sorted)
    median = travel_times_sorted[n // 2] if n % 2 == 1 else (
        travel_times_sorted[n // 2 - 1] + travel_times_sorted[n // 2]
    ) / 2.0
    
    # Analyze by phase
    phase_stats: Dict[str, dict] = {}
    for group in groups:
        phase = group.get('phase', 'unknown')
        travel = float(group.get('travel_seconds', 0))
        
        if phase not in phase_stats:
            phase_stats[phase] = {
                'count': 0,
                'total': 0.0,
                'max': 0.0,
            }
        
        phase_stats[phase]['count'] += 1
        phase_stats[phase]['total'] += travel
        phase_stats[phase]['max'] = max(phase_stats[phase]['max'], travel)
    
    # Calculate means for each phase
    for phase, stats in phase_stats.items():
        stats['mean'] = stats['total'] / stats['count'] if stats['count'] > 0 else 0.0
    
    return {
        'total_travel_seconds': sum(travel_times),
        'mean_travel_seconds': sum(travel_times) / len(travel_times),
        'median_travel_seconds': median,
        'max_travel_seconds': max(travel_times),
        'groups_by_phase': phase_stats,
    }
