import pytest
from bson.objectid import ObjectId

from app import db as db_mod
from app.services.matching.operations import list_issues


@pytest.mark.asyncio
async def test_list_issues_flags_missing_registration_and_phase_gap():
    event_oid = ObjectId()
    event_id = str(event_oid)
    team_oid = ObjectId()

    await db_mod.db.events.insert_one({
        '_id': event_oid,
        'title': 'Diagnostics event',
        'status': 'published',
        'fee_cents': 0,
    })

    await db_mod.db.teams.insert_one({
        '_id': team_oid,
        'event_id': event_oid,
        'status': 'active',
        'members': [{'email': 'missing@example.com'}],
    })

    await db_mod.db.registrations.insert_one({
        '_id': ObjectId(),
        'event_id': event_oid,
        'team_id': team_oid,
        'user_email_snapshot': 'missing@example.com',
        'status': 'confirmed',
        'team_size': 1,
    })

    await db_mod.db.matches.insert_one({
        'event_id': event_id,
        'version': 1,
        'status': 'proposed',
        'groups': [],
        'metrics': {},
        'unmatched_units': [{
            'team_id': str(team_oid),
            'phases': ['appetizer', 'main', 'dessert'],
            'size': 1,
            'can_host_any': False,
            'can_host_main': False,
        }],
    })

    result = await list_issues(event_id)

    issue_payloads = result['issues']
    registration_issue = next((item for item in issue_payloads if 'registration_missing' in item.get('issues', [])), None)
    assert registration_issue is not None
    assert registration_issue['issue_counts'].get('registration_missing') == 1
    registration_actor = registration_issue['actors']['registration_missing'][0]
    assert registration_actor['team_id'] == str(team_oid)
    assert 'missing@example.com' in registration_actor.get('missing_emails', [])

    phase_issue = next((item for item in issue_payloads if 'phase_participation_gap' in item.get('issues', [])), None)
    assert phase_issue is not None
    phase_actor = phase_issue['actors']['phase_participation_gap'][0]
    assert phase_actor['team_id'] == str(team_oid)
    assert set(phase_actor.get('missing_phases', [])) == {'appetizer', 'main', 'dessert'}
