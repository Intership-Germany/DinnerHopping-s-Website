import pytest

from app.services.matching import _group_units_in_triads
from app.services.matching.config import weight_defaults
from app.services.matching.grouping import TravelTimeResolver, phase_groups


def _duo(uid: str, origin: str | None = None):
    return {
        'unit_id': uid,
        'origin_id': origin or uid,
        'size': 2,
        'lat': 0.0,
        'lon': 0.0,
        'team_diet': 'omnivore',
        'course_preference': None,
        'can_host_main': True,
        'can_host_any': True,
    }


def _solo(uid: str, origin: str | None = None):
    return {
        'unit_id': uid,
        'origin_id': origin or uid,
        'size': 1,
        'lat': 0.0,
        'lon': 0.0,
        'team_diet': 'omnivore',
        'course_preference': None,
        'can_host_main': False,
        'can_host_any': True,
    }


def test_grouping_all_duos_triads():
    units = [_duo(f'd{i}') for i in range(6)]  # 6 duos -> 2 triads of 3 duos
    groups = _group_units_in_triads(units)
    assert sum(len(g) for g in groups) == len(units)
    assert all(len(g) == 3 for g in groups)
    # all members are duos
    assert all(all(u['size'] >= 2 for u in g) for g in groups)


def test_grouping_prefers_duos_minimizes_solos():
    # 4 duos + 2 solos -> expect 1 triad of 3 duos and 1 triad of (1 duo + 2 solos)
    duos = [_duo(f'd{i}') for i in range(4)]
    solos = [_solo(f's{i}') for i in range(2)]
    units = duos + solos
    groups = _group_units_in_triads(units)
    assert all(len(g) == 3 for g in groups)
    # Count duos per group
    duos_per_group = [sum(1 for u in g if u['size'] >= 2) for g in groups]
    # One group should have 3 duos, the other 1 duo
    assert sorted(duos_per_group) == [1, 3]


@pytest.mark.asyncio
async def test_phase_groups_reuses_host_when_limit_reached():
    weights = weight_defaults()
    travel_resolver = TravelTimeResolver(fast_mode=True, parallelism=1)
    units = [
        _duo('h1'),
        _solo('g1'),
        _solo('g2'),
    ]
    groups, leftovers = await phase_groups(
        units,
        'main',
        used_pairs=set(),
        weights=weights,
        travel_resolver=travel_resolver,
        host_usage={'h1': 1},
        host_limit=1,
    )
    assert len(groups) == 1
    group = groups[0]
    assert group['host_team_id'] == 'h1'
    assert set(group['guest_team_ids']) == {'g1', 'g2'}
    assert 'host_reuse' in group.get('warnings', [])
    assert leftovers == []

