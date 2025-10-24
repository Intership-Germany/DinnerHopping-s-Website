from __future__ import annotations

from typing import Dict, List, Optional, Set


def _extract_solo_ids_from_synthetic(synthetic_id: str, team_details: Dict[str, dict]) -> List[str]:
    """
    Extract underlying solo team IDs from synthetic team identifiers.
    
    Args:
        synthetic_id: Team ID that may be synthetic (pair:email1+email2 or split:email)
        team_details: Mapping of team_id to team details (must include 'emails' field)
    
    Returns:
        List of solo team IDs that compose this synthetic team
    """
    solo_ids = []
    
    # Extract emails from synthetic ID
    synthetic_emails = []
    if synthetic_id.startswith('pair:'):
        # Extract emails from pair:email1+email2
        emails_part = synthetic_id[5:]  # Remove "pair:"
        synthetic_emails = [e.strip().lower() for e in emails_part.split('+') if e.strip()]
    elif synthetic_id.startswith('split:'):
        # Extract email from split:email
        email = synthetic_id[6:].strip().lower()  # Remove "split:"
        if email:
            synthetic_emails = [email]
    
    # Find solo teams whose emails match the synthetic team emails
    for team_id, details in team_details.items():
        if team_id.startswith('solo:'):
            team_emails = details.get('emails', [])
            # Check if any email from this solo team matches the synthetic team emails
            for team_email in team_emails:
                if team_email.lower() in synthetic_emails:
                    solo_ids.append(team_id)
                    break  # Only add each solo team once
    
    return solo_ids


def compute_metrics(groups: List[dict], weights: Dict[str, float], team_details: Dict[str, dict] = None) -> dict:
    """
    Compute comprehensive metrics for a set of groups.
    
    Args:
        groups: List of group dictionaries with phase, host_team_id, guest_team_ids, etc.
        weights: Weight configuration (not currently used but kept for compatibility)
        team_details: Optional mapping of team_id to team details (size, etc.)
                     If not provided, participant counts will not be calculated.
    
    Returns:
        Dictionary with metrics including travel, score, warnings, and participant counts
    """
    total_travel = sum(float(group.get('travel_seconds') or 0.0) for group in groups)
    total_score = sum(float(group.get('score') or 0.0) for group in groups)
    issues = sum(1 for group in groups if group.get('warnings'))
    
    metrics = {
        'total_travel_seconds': total_travel,
        'aggregate_group_score': total_score,
        'groups_with_warnings': issues,
    }
    
    # Calculate participant counts if team_details provided
    if team_details:
        def _coerce_size(value: object) -> Optional[int]:
            try:
                size_int = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
            if size_int <= 0:
                return None
            return size_int

        def _effective_team_size(team_id: str, trail: Optional[Set[str]] = None) -> int:
            if not team_id:
                return 0
            key = str(team_id)
            seen = set(trail or set())
            if key in seen:
                return 0
            seen.add(key)
            entry = team_details.get(key, {})
            size_val = _coerce_size(entry.get('size')) if isinstance(entry, dict) else None
            if size_val is not None:
                return size_val
            if key.startswith('pair:') or key.startswith('split:'):
                solo_ids = _extract_solo_ids_from_synthetic(key, team_details)
                if solo_ids:
                    total = 0
                    for solo_id in solo_ids:
                        total += _effective_team_size(solo_id, seen)
                    if total > 0:
                        return total
            return 1

        # Count all participants from team_details (excluding synthetic pair:/split: teams)
        all_team_ids = [tid for tid in team_details.keys() if not (tid.startswith('pair:') or tid.startswith('split:'))]
        total_participants = 0
        for tid in all_team_ids:
            entry = team_details.get(tid)
            size_val = _coerce_size(entry.get('size')) if isinstance(entry, dict) else None
            total_participants += size_val if size_val is not None else 1
        
        # Count assigned participants by phase
        phase_summary = {
            'appetizer': {'assigned_participants': 0, 'expected_participants': total_participants, 'missing_participants': 0},
            'main': {'assigned_participants': 0, 'expected_participants': total_participants, 'missing_participants': 0},
            'dessert': {'assigned_participants': 0, 'expected_participants': total_participants, 'missing_participants': 0}
        }

        all_assigned_units: Set[str] = set()
        
        for group in groups:
            phase = group.get('phase')
            if phase not in phase_summary:
                continue
            
            # Count host
            host_id = group.get('host_team_id')
            if host_id:
                host_key = str(host_id)
                team_size = _effective_team_size(host_key)
                phase_summary[phase]['assigned_participants'] += team_size
                all_assigned_units.add(host_key)
            
            # Count guests
            for guest_id in (group.get('guest_team_ids') or []):
                guest_key = str(guest_id)
                team_size = _effective_team_size(guest_key)
                phase_summary[phase]['assigned_participants'] += team_size
                all_assigned_units.add(guest_key)
        
        # Calculate missing participants per phase
        for phase in phase_summary:
            phase_summary[phase]['missing_participants'] = max(
                0,
                phase_summary[phase]['expected_participants'] - phase_summary[phase]['assigned_participants']
            )
        assigned_participants = sum(_effective_team_size(team_id) for team_id in all_assigned_units)
        
        metrics['total_participant_count'] = total_participants
        metrics['assigned_participant_count'] = assigned_participants
        metrics['phase_summary'] = phase_summary
    
    return metrics
