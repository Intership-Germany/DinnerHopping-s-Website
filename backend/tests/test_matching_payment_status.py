import pytest
from bson.objectid import ObjectId
import datetime as dt

from app import db as db_mod

@pytest.mark.asyncio
async def test_matching_counts_succeeded_as_paid(client, admin_token):
    # Create event (published, fee > 0)
    ev_id = ObjectId()
    now = dt.datetime.now(dt.timezone.utc)
    await db_mod.db.events.insert_one({
        '_id': ev_id,
        'title': 'Payment Status Test Event',
        'status': 'published',
        'fee_cents': 1500,
        'registration_deadline': now - dt.timedelta(days=1),
    })
    # Create 3 users + registrations (solo teams)
    regs = []
    emails = ['paid1@example.com', 'unpaid1@example.com', 'unpaid2@example.com']
    for i, em in enumerate(emails):
        await db_mod.db.users.insert_one({
            'email': em,
            'first_name': f'U{i}',
            'last_name': 'Test',
            'email_verified': True,
            'roles': [],
            'created_at': now,
            'updated_at': now,
        })
        reg_id = ObjectId()
        reg_doc = {
            '_id': reg_id,
            'event_id': ev_id,
            'user_email_snapshot': em,
            'status': 'confirmed',
            'team_size': 1,
            'preferences': {},
            'diet': 'omnivore',
            'created_at': now,
            'updated_at': now,
        }
        await db_mod.db.registrations.insert_one(reg_doc)
        regs.append(reg_doc)
    # Create payment for first registration with status 'succeeded'
    await db_mod.db.payments.insert_one({
        'registration_id': regs[0]['_id'],
        'status': 'succeeded',
        'amount': 15,
        'currency': 'EUR',
        'created_at': now,
        'paid_at': now,
    })
    # Insert a match doc referencing the three solo team ids
    def solo_id(r):
        return f"solo:{str(r['_id'])}"
    group = {
        'phase': 'appetizer',
        'host_team_id': solo_id(regs[0]),
        'guest_team_ids': [solo_id(regs[1]), solo_id(regs[2])],
        'score': 0.0,
        'travel_seconds': 0.0,
        'warnings': [],
    }
    await db_mod.db.matches.insert_one({
        'event_id': str(ev_id),
        'version': 1,
        'algorithm': 'test',
        'groups': [group],
        'metrics': {},
        'status': 'proposed',
        'created_at': now,
    })
    # Call details endpoint
    resp = await client.get(f"/matching/{ev_id}/details", headers={'Authorization': f'Bearer {admin_token}'})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    td = data['team_details']
    host_id = group['host_team_id']
    assert host_id in td, f"Expected host team id {host_id} in team_details keys: {list(td.keys())}"
    assert td[host_id]['payment']['status'] == 'paid', td[host_id]['payment']
    # Unpaid teams should be marked 'unpaid'
    for guest_id in group['guest_team_ids']:
        assert td[guest_id]['payment']['status'] == 'unpaid', td[guest_id]['payment']

