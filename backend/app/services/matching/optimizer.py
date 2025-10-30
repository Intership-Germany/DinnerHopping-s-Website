"""
Matching optimizer: attempts to improve matching results by recreating auto-generated teams
when issues are detected (missing participants, host reuse, etc.)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from bson.objectid import ObjectId

from .algorithms import AlgorithmProgressCallback, algo_greedy, algo_random
from .config import algorithm_seed
from .data import build_teams
from .metrics import compute_metrics
from .units import auto_pair_solos, build_units_from_teams

logger = logging.getLogger(__name__)

OptimizationCallback = Callable[[Dict[str, Any]], Awaitable[None]]


async def optimize_match_result(
    event_oid: ObjectId,
    initial_result: dict,
    weights: Dict[str, float],
    *,
    max_attempts: int = 3,
    progress_cb: Optional[OptimizationCallback] = None,
    parallel: bool = True,
) -> dict:
    """
    Try to improve match results by recreating auto-paired teams when issues are detected.
    
    Args:
        event_oid: Event object ID
        initial_result: Initial matching result with groups, metrics, and unmatched_units
        weights: Scoring weights
        max_attempts: Maximum number of optimization attempts
        progress_cb: Optional callback for progress updates
        parallel: Whether to run attempts in parallel (default: True for speed)
        
    Returns:
        Best matching result found (may be the initial result if no improvement)
    """
    logger.info('matching.optimizer starting optimization for event=%s max_attempts=%d parallel=%s', str(event_oid), max_attempts, parallel)
    
    # Analyze initial result for issues
    issues = _analyze_issues(initial_result)
    issue_count = sum(len(v) for v in issues.values())
    
    if progress_cb:
        await progress_cb({
            'stage': 'analyze',
            'issues_found': issue_count,
            'issues': issues,
        })
    
    # If no significant issues, return initial result
    if issue_count == 0:
        logger.info('matching.optimizer no issues found, returning initial result')
        if progress_cb:
            await progress_cb({'stage': 'complete', 'improved': False, 'attempts': 0})
        return initial_result
    
    logger.info('matching.optimizer found %d issues: %s', issue_count, issues)
    
    # Keep track of best result
    best_result = initial_result
    best_score = _compute_overall_score(initial_result)
    
    # Run attempts in parallel or sequential
    if parallel:
        best_result, best_score, issue_count = await _run_parallel_attempts(
            event_oid,
            weights,
            max_attempts,
            initial_result,
            best_score,
            issue_count,
            progress_cb,
        )
    else:
        best_result, best_score, issue_count = await _run_sequential_attempts(
            event_oid,
            weights,
            max_attempts,
            initial_result,
            best_score,
            issue_count,
            progress_cb,
        )
    
    improved = best_result != initial_result
    if progress_cb:
        await progress_cb({
            'stage': 'complete',
            'improved': improved,
            'attempts': max_attempts,
            'final_score': best_score,
            'final_issues': issue_count,
        })
    
    if improved:
        logger.info('matching.optimizer optimization successful, score %.2f->%.2f', _compute_overall_score(initial_result), best_score)
    else:
        logger.info('matching.optimizer no improvement found')
    
    return best_result


async def _run_parallel_attempts(
    event_oid: ObjectId,
    weights: Dict[str, float],
    max_attempts: int,
    initial_result: dict,
    initial_score: float,
    initial_issue_count: int,
    progress_cb: Optional[OptimizationCallback] = None,
) -> Tuple[dict, float, int]:
    """
    Run all optimization attempts in parallel for maximum speed.
    
    Returns:
        Tuple of (best_result, best_score, issue_count)
    """
    logger.info('matching.optimizer running %d attempts in parallel', max_attempts)
    
    # Create all tasks at once
    tasks = []
    for attempt in range(max_attempts):
        task = _try_optimization(
            event_oid,
            weights,
            attempt=attempt,
            seed=algorithm_seed('optimizer', 1000) + attempt,
        )
        tasks.append(task)
    
    if progress_cb:
        await progress_cb({
            'stage': 'parallel_start',
            'total_attempts': max_attempts,
        })
    
    # Run all attempts in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Analyze all results and find the best one
    best_result = initial_result
    best_score = initial_score
    issue_count = initial_issue_count
    
    for attempt, result in enumerate(results, start=1):
        # Skip failed attempts
        if isinstance(result, Exception):
            logger.exception('matching.optimizer parallel attempt %d failed', attempt, exc_info=result)
            continue
        
        if result is None:
            continue
        
        try:
            # Analyze result
            new_issues = _analyze_issues(result)
            new_issue_count = sum(len(v) for v in new_issues.values())
            new_score = _compute_overall_score(result)
            
            logger.info(
                'matching.optimizer parallel attempt %d: issues=%d->%d score=%.2f->%.2f',
                attempt,
                initial_issue_count,
                new_issue_count,
                initial_score,
                new_score,
            )
            
            # Keep if better
            if new_issue_count < issue_count or (new_issue_count == issue_count and new_score > best_score):
                best_result = result
                best_score = new_score
                issue_count = new_issue_count
                
                if progress_cb:
                    await progress_cb({
                        'stage': 'improvement',
                        'attempt': attempt,
                        'new_score': new_score,
                        'new_issues': new_issue_count,
                    })
                
                logger.info('matching.optimizer found improvement in parallel attempt %d', attempt)
        
        except Exception as e:
            logger.exception('matching.optimizer error analyzing parallel attempt %d: %s', attempt, e)
            continue
    
    return best_result, best_score, issue_count


async def _run_sequential_attempts(
    event_oid: ObjectId,
    weights: Dict[str, float],
    max_attempts: int,
    initial_result: dict,
    initial_score: float,
    initial_issue_count: int,
    progress_cb: Optional[OptimizationCallback] = None,
) -> Tuple[dict, float, int]:
    """
    Run optimization attempts sequentially (original behavior).
    Can stop early if all issues are resolved.
    
    Returns:
        Tuple of (best_result, best_score, issue_count)
    """
    best_result = initial_result
    best_score = initial_score
    issue_count = initial_issue_count
    
    for attempt in range(max_attempts):
        if progress_cb:
            await progress_cb({
                'stage': 'attempt',
                'attempt': attempt + 1,
                'total_attempts': max_attempts,
            })
        
        logger.info('matching.optimizer sequential attempt %d/%d', attempt + 1, max_attempts)
        
        try:
            # Try optimizing with different strategies
            optimized = await _try_optimization(
                event_oid,
                weights,
                attempt=attempt,
                seed=algorithm_seed('optimizer', 1000) + attempt,
            )
            
            if optimized is None:
                continue
            
            # Analyze new result
            new_issues = _analyze_issues(optimized)
            new_issue_count = sum(len(v) for v in new_issues.values())
            new_score = _compute_overall_score(optimized)
            
            logger.info(
                'matching.optimizer sequential attempt %d: issues=%d->%d score=%.2f->%.2f',
                attempt + 1,
                issue_count,
                new_issue_count,
                best_score,
                new_score,
            )
            
            # Keep if better (fewer issues or better score)
            if new_issue_count < issue_count or (new_issue_count == issue_count and new_score > best_score):
                best_result = optimized
                best_score = new_score
                issue_count = new_issue_count
                
                if progress_cb:
                    await progress_cb({
                        'stage': 'improvement',
                        'attempt': attempt + 1,
                        'new_score': new_score,
                        'new_issues': new_issue_count,
                    })
                
                logger.info('matching.optimizer found improvement at sequential attempt %d', attempt + 1)
                
                # If we resolved all issues, we can stop early
                if new_issue_count == 0:
                    logger.info('matching.optimizer all issues resolved, stopping early')
                    break
        
        except Exception as e:
            logger.exception('matching.optimizer sequential attempt %d failed: %s', attempt + 1, e)
            continue
    
    return best_result, best_score, issue_count


async def _try_optimization(
    event_oid: ObjectId,
    weights: Dict[str, float],
    attempt: int,
    seed: int,
) -> Optional[dict]:
    """
    Try one optimization attempt by recreating auto-paired teams with different strategies.
    """
    # Load base teams (without auto-pairing)
    teams = await build_teams(event_oid)
    units, unit_emails = await build_units_from_teams(teams)
    
    # Apply different pairing strategies based on attempt number
    if attempt % 3 == 0:
        # Strategy 1: More selective pairing (higher threshold)
        units, unit_emails, auto_pair_details = auto_pair_solos(
            units,
            unit_emails,
            min_score=12.0,  # Higher threshold
        )
    elif attempt % 3 == 1:
        # Strategy 2: Less selective pairing (lower threshold)
        units, unit_emails, auto_pair_details = auto_pair_solos(
            units,
            unit_emails,
            min_score=8.0,  # Lower threshold
        )
    else:
        # Strategy 3: No auto-pairing, keep all solos separate
        auto_pair_details = []
    
    logger.debug('matching.optimizer attempt=%d strategy=%d auto_pairs=%d', attempt, attempt % 3, len(auto_pair_details))
    
    # Use different algorithm based on attempt
    if attempt % 2 == 0:
        result = await algo_greedy(event_oid, weights, seed=seed + attempt)
    else:
        result = await algo_random(event_oid, weights, seed=seed + attempt)
    
    return result


def _analyze_issues(result: dict) -> Dict[str, List[str]]:
    """
    Analyze matching result for issues.
    
    Returns dict with issue categories and affected unit IDs.
    """
    issues: Dict[str, List[str]] = {
        'missing_participants': [],
        'host_reuse': [],
        'uncovered_allergies': [],
        'diet_conflicts': [],
        'capacity_mismatches': [],
    }
    
    groups = result.get('groups') or []
    unmatched_units = result.get('unmatched_units') or []
    
    # Track missing participants
    for entry in unmatched_units:
        team_id = entry.get('team_id')
        if team_id:
            issues['missing_participants'].append(str(team_id))
    
    # Also check metrics for unmatched unit IDs
    metrics = result.get('metrics') or {}
    unmatched_ids = metrics.get('unmatched_unit_ids') or []
    for unit_id in unmatched_ids:
        if str(unit_id) not in issues['missing_participants']:
            issues['missing_participants'].append(str(unit_id))
    
    # Track issues in formed groups
    host_usage: Dict[str, int] = {}
    for group in groups:
        host_id = group.get('host_team_id')
        if host_id:
            host_id_str = str(host_id)
            host_usage[host_id_str] = host_usage.get(host_id_str, 0) + 1
        
        warnings = group.get('warnings') or []
        
        # Host reuse
        if 'host_reuse' in warnings and host_id:
            if str(host_id) not in issues['host_reuse']:
                issues['host_reuse'].append(str(host_id))
        
        # Uncovered allergies
        if 'allergy_uncovered' in warnings:
            uncovered = group.get('uncovered_allergies') or []
            if uncovered and host_id:
                if str(host_id) not in issues['uncovered_allergies']:
                    issues['uncovered_allergies'].append(str(host_id))
        
        # Diet conflicts
        if 'diet_conflict' in warnings:
            all_units = [host_id] + (group.get('guest_team_ids') or [])
            for unit_id in all_units:
                if unit_id and str(unit_id) not in issues['diet_conflicts']:
                    issues['diet_conflicts'].append(str(unit_id))
        
        # Capacity mismatches
        if 'host_cannot_main' in warnings or 'host_no_kitchen' in warnings:
            if host_id and str(host_id) not in issues['capacity_mismatches']:
                issues['capacity_mismatches'].append(str(host_id))
    
    # Additional host reuse detection
    for host_id_str, usage in host_usage.items():
        if usage > 1 and host_id_str not in issues['host_reuse']:
            issues['host_reuse'].append(host_id_str)
    
    return issues


def _compute_overall_score(result: dict) -> float:
    """
    Compute overall quality score for a matching result.
    Higher is better.
    """
    metrics = result.get('metrics') or {}
    groups = result.get('groups') or []
    
    # Start with base score from metrics
    score = float(metrics.get('total_score', 0.0))
    
    # Penalize unmatched participants heavily
    unmatched_count = int(metrics.get('unmatched_participant_count', 0))
    score -= unmatched_count * 1000.0
    
    # Penalize unmatched units
    unmatched_units = int(metrics.get('unmatched_unit_count', 0))
    score -= unmatched_units * 500.0
    
    # Penalize issues in groups
    for group in groups:
        warnings = group.get('warnings') or []
        
        # Heavy penalties for serious issues
        if 'host_reuse' in warnings:
            score -= 200.0
        if 'allergy_uncovered' in warnings:
            score -= 150.0
        if 'diet_conflict' in warnings:
            score -= 100.0
        if 'host_cannot_main' in warnings or 'host_no_kitchen' in warnings:
            score -= 80.0
    
    # Bonus for complete matching
    total_units = int(metrics.get('total_unit_count', 0))
    assigned_units = int(metrics.get('assigned_unit_count', 0))
    if total_units > 0:
        completion_ratio = assigned_units / total_units
        score += completion_ratio * 500.0
    
    return float(score)
