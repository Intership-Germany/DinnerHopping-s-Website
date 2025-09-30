import pytest

from app.services.matching import _group_units_in_triads


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

