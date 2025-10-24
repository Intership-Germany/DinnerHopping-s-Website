from typing import Dict, List, Optional

from app.services.matching.units import auto_pair_solos


def _solo_unit(
    unit_id: str,
    email: str,
    *,
    can_host_any: bool,
    can_host_main: bool,
    gender: Optional[str],
    course: Optional[str] = None,
    diet: str = 'omnivore',
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    allergies: Optional[List[str]] = None,
) -> dict:
    allergies = allergies or []
    return {
      'unit_id': unit_id,
      'size': 1,
      'lat': lat,
      'lon': lon,
      'team_diet': diet,
      'can_host_any': can_host_any,
      'can_host_main': can_host_main,
      'course_preference': course,
      'host_emails': [email],
    'allergies': list(allergies),
    'host_allergies': list(allergies),
    'member_profiles': [{'email': email, 'gender': gender, 'allergies': list(allergies)}],
      'gender_mix': [gender] if gender else [],
    }


def test_auto_pair_solos_pairs_host_capable_singles():
    units = [
        _solo_unit(
            'solo:1',
            'alice@example.com',
            can_host_any=True,
            can_host_main=False,
            gender='female',
            course='appetizer',
            lat=48.1374,
            lon=11.5755,
        ),
        _solo_unit(
            'solo:2',
            'bob@example.com',
            can_host_any=False,
            can_host_main=True,
            gender='male',
            course='main',
            lat=48.139,
            lon=11.58,
        ),
    ]
    unit_emails: Dict[str, List[str]] = {unit['unit_id']: [unit['host_emails'][0]] for unit in units}

    updated_units, updated_map, details = auto_pair_solos(units, unit_emails)

    assert len(details) == 1
    pair_unit = next(unit for unit in updated_units if str(unit['unit_id']).startswith('pair:'))
    assert pair_unit['size'] == 2
    assert pair_unit['can_host_any'] is True
    assert pair_unit['can_host_main'] is True
    assert pair_unit['gender_mix'] == ['female', 'male']
    assert updated_map[pair_unit['unit_id']] == ['alice@example.com', 'bob@example.com']
    assert pair_unit['host_emails'][0] == 'alice@example.com'


def test_auto_pair_solos_skips_non_host_combinations():
    units = [
        _solo_unit('solo:10', 'eve@example.com', can_host_any=False, can_host_main=False, gender='female'),
        _solo_unit('solo:11', 'zoe@example.com', can_host_any=False, can_host_main=False, gender='female'),
    ]
    unit_emails = {unit['unit_id']: [unit['host_emails'][0]] for unit in units}

    updated_units, updated_map, details = auto_pair_solos(units, unit_emails)

    assert details == []
    assert updated_units == units
    assert updated_map == unit_emails


def test_auto_pair_solos_ignores_split_units():
    split_unit = _solo_unit(
        'split:solo:3',
        'carol@example.com',
        can_host_any=True,
        can_host_main=True,
        gender='female',
    )
    regular_unit = _solo_unit(
        'solo:4',
        'dave@example.com',
        can_host_any=True,
        can_host_main=True,
        gender='male',
    )
    units = [split_unit, regular_unit]
    unit_emails = {
        split_unit['unit_id']: [split_unit['host_emails'][0]],
        regular_unit['unit_id']: [regular_unit['host_emails'][0]],
    }

    updated_units, updated_map, details = auto_pair_solos(units, unit_emails)

    assert len(details) == 0
    assert updated_units == units
    assert updated_map == unit_emails
