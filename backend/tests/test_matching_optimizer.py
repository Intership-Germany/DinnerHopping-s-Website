"""
Tests for matching optimization functionality
"""
import pytest
from bson import ObjectId

from app.services.matching.optimizer import (
    optimize_match_result,
    _analyze_issues,
    _compute_overall_score,
)


def test_analyze_issues_empty_result():
    """Test issue analysis with no issues"""
    result = {
        'groups': [],
        'metrics': {},
        'unmatched_units': [],
    }
    issues = _analyze_issues(result)
    
    assert isinstance(issues, dict)
    assert len(issues['missing_participants']) == 0
    assert len(issues['host_reuse']) == 0
    assert len(issues['uncovered_allergies']) == 0
    assert len(issues['diet_conflicts']) == 0
    assert len(issues['capacity_mismatches']) == 0


def test_analyze_issues_with_unmatched():
    """Test issue analysis with unmatched participants"""
    result = {
        'groups': [],
        'metrics': {
            'unmatched_unit_ids': ['solo:123', 'solo:456'],
        },
        'unmatched_units': [
            {'team_id': 'solo:123', 'phases': ['appetizer', 'main']},
            {'team_id': 'solo:456', 'phases': ['dessert']},
        ],
    }
    issues = _analyze_issues(result)
    
    assert len(issues['missing_participants']) == 2
    assert 'solo:123' in issues['missing_participants']
    assert 'solo:456' in issues['missing_participants']


def test_analyze_issues_with_host_reuse():
    """Test issue analysis with host reuse"""
    result = {
        'groups': [
            {
                'phase': 'appetizer',
                'host_team_id': 'team:001',
                'guest_team_ids': ['team:002', 'team:003'],
                'warnings': [],
            },
            {
                'phase': 'main',
                'host_team_id': 'team:001',
                'guest_team_ids': ['team:004', 'team:005'],
                'warnings': ['host_reuse'],
            },
        ],
        'metrics': {},
        'unmatched_units': [],
    }
    issues = _analyze_issues(result)
    
    assert len(issues['host_reuse']) == 1
    assert 'team:001' in issues['host_reuse']


def test_analyze_issues_with_warnings():
    """Test issue analysis with various warnings"""
    result = {
        'groups': [
            {
                'phase': 'main',
                'host_team_id': 'team:001',
                'guest_team_ids': ['team:002', 'team:003'],
                'warnings': ['allergy_uncovered', 'diet_conflict'],
                'uncovered_allergies': ['nuts', 'shellfish'],
            },
            {
                'phase': 'dessert',
                'host_team_id': 'team:004',
                'guest_team_ids': ['team:005', 'team:006'],
                'warnings': ['host_cannot_main'],
            },
        ],
        'metrics': {},
        'unmatched_units': [],
    }
    issues = _analyze_issues(result)
    
    assert len(issues['uncovered_allergies']) == 1
    assert 'team:001' in issues['uncovered_allergies']
    
    # diet_conflict affects all units in the group
    assert len(issues['diet_conflicts']) == 3
    
    assert len(issues['capacity_mismatches']) == 1
    assert 'team:004' in issues['capacity_mismatches']


def test_compute_overall_score_perfect_match():
    """Test score computation for perfect match (no issues)"""
    result = {
        'groups': [
            {
                'phase': 'appetizer',
                'host_team_id': 'team:001',
                'guest_team_ids': ['team:002', 'team:003'],
                'warnings': [],
            },
        ],
        'metrics': {
            'total_score': 1000.0,
            'total_unit_count': 3,
            'assigned_unit_count': 3,
            'unmatched_unit_count': 0,
            'unmatched_participant_count': 0,
        },
        'unmatched_units': [],
    }
    
    score = _compute_overall_score(result)
    
    # Base score + completion bonus
    # 1000.0 + (3/3 * 500) = 1500.0
    assert score == pytest.approx(1500.0)


def test_compute_overall_score_with_issues():
    """Test score computation with various issues"""
    result = {
        'groups': [
            {
                'phase': 'appetizer',
                'host_team_id': 'team:001',
                'guest_team_ids': ['team:002', 'team:003'],
                'warnings': ['host_reuse', 'allergy_uncovered'],
            },
        ],
        'metrics': {
            'total_score': 1000.0,
            'total_unit_count': 5,
            'assigned_unit_count': 3,
            'unmatched_unit_count': 2,
            'unmatched_participant_count': 2,
        },
        'unmatched_units': [],
    }
    
    score = _compute_overall_score(result)
    
    # Base: 1000.0
    # - 2 unmatched participants: -2000.0
    # - 2 unmatched units: -1000.0
    # - 1 host_reuse: -200.0
    # - 1 allergy_uncovered: -150.0
    # + completion bonus (3/5 * 500): +300.0
    # Total: 1000 - 2000 - 1000 - 200 - 150 + 300 = -2050.0
    assert score == pytest.approx(-2050.0)


def test_compute_overall_score_penalties():
    """Test individual penalty calculations"""
    # Test diet conflict
    result_diet = {
        'groups': [
            {
                'phase': 'main',
                'host_team_id': 'team:001',
                'guest_team_ids': ['team:002', 'team:003'],
                'warnings': ['diet_conflict'],
            },
        ],
        'metrics': {
            'total_score': 1000.0,
            'total_unit_count': 3,
            'assigned_unit_count': 3,
        },
    }
    score_diet = _compute_overall_score(result_diet)
    # 1000 - 100 (diet_conflict) + 500 (completion) = 1400.0
    assert score_diet == pytest.approx(1400.0)
    
    # Test capacity mismatch
    result_capacity = {
        'groups': [
            {
                'phase': 'main',
                'host_team_id': 'team:001',
                'guest_team_ids': ['team:002', 'team:003'],
                'warnings': ['host_cannot_main'],
            },
        ],
        'metrics': {
            'total_score': 1000.0,
            'total_unit_count': 3,
            'assigned_unit_count': 3,
        },
    }
    score_capacity = _compute_overall_score(result_capacity)
    # 1000 - 80 (capacity) + 500 (completion) = 1420.0
    assert score_capacity == pytest.approx(1420.0)


@pytest.mark.asyncio
async def test_optimize_match_result_no_issues(monkeypatch):
    """Test that optimization is skipped when no issues are found"""
    result = {
        'algorithm': 'greedy',
        'groups': [
            {
                'phase': 'appetizer',
                'host_team_id': 'team:001',
                'guest_team_ids': ['team:002', 'team:003'],
                'warnings': [],
            },
        ],
        'metrics': {
            'total_unit_count': 3,
            'assigned_unit_count': 3,
            'unmatched_unit_count': 0,
            'unmatched_participant_count': 0,
        },
        'unmatched_units': [],
    }
    
    # Mock event lookup
    async def mock_get_event(event_id):
        return {'_id': ObjectId()}
    
    monkeypatch.setattr('app.services.matching.optimizer.get_event', mock_get_event)
    
    optimized = await optimize_match_result(
        ObjectId(),
        result,
        {},
        max_attempts=3,
    )
    
    # Should return the same result since no issues were found
    assert optimized == result


def test_analyze_issues_comprehensive():
    """Test comprehensive issue analysis with all issue types"""
    result = {
        'groups': [
            {
                'phase': 'appetizer',
                'host_team_id': 'team:001',
                'guest_team_ids': ['team:002', 'team:003'],
                'warnings': [],
            },
            {
                'phase': 'main',
                'host_team_id': 'team:001',  # Reused host
                'guest_team_ids': ['team:004', 'team:005'],
                'warnings': ['host_reuse', 'allergy_uncovered', 'diet_conflict', 'host_cannot_main'],
                'uncovered_allergies': ['peanuts'],
            },
            {
                'phase': 'dessert',
                'host_team_id': 'team:006',
                'guest_team_ids': ['team:007', 'team:008'],
                'warnings': ['host_no_kitchen'],
            },
        ],
        'metrics': {
            'unmatched_unit_ids': ['solo:999'],
        },
        'unmatched_units': [
            {'team_id': 'solo:999', 'phases': ['appetizer', 'main', 'dessert']},
        ],
    }
    
    issues = _analyze_issues(result)
    
    # Check all issue types are detected
    assert 'solo:999' in issues['missing_participants']
    assert 'team:001' in issues['host_reuse']
    assert 'team:001' in issues['uncovered_allergies']
    assert len(issues['diet_conflicts']) >= 3  # team:001, team:004, team:005
    assert len(issues['capacity_mismatches']) == 2  # team:001 and team:006
