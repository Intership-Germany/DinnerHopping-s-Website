import datetime as dt

import pytest
from bson.objectid import ObjectId

from app import db as db_mod


@pytest.mark.asyncio
async def test_admin_event_participants_listing(client, admin_token):
    now = dt.datetime.now(dt.timezone.utc)
    ev_id = ObjectId()

    # Ensure clean state for collections touched in this test (leave admin user intact)
    await db_mod.db.events.delete_many({'_id': ev_id})
    await db_mod.db.registrations.delete_many({'event_id': ev_id})
    await db_mod.db.teams.delete_many({'event_id': ev_id})

    await db_mod.db.events.insert_one({
        '_id': ev_id,
        'title': 'Participants Test Event',
        'status': 'open',
        'fee_cents': 2000,
        'created_at': now,
        'updated_at': now,
    })

    # Solo participant (paid)
    solo_user_id = ObjectId()
    await db_mod.db.users.insert_one({
        '_id': solo_user_id,
        'email': 'solo.participants@example.com',
        'first_name': 'Solo',
        'last_name': 'Player',
        'gender': 'male',
        'created_at': now,
        'updated_at': now,
    })
    solo_reg_id = ObjectId()
    await db_mod.db.registrations.insert_one({
        '_id': solo_reg_id,
        'event_id': ev_id,
        'user_id': solo_user_id,
    'user_email_snapshot': 'solo.participants@example.com',
        'team_size': 1,
        'status': 'confirmed',
        'preferences': {},
        'diet': 'omnivore',
        'created_at': now,
        'updated_at': now,
    })
    await db_mod.db.payments.insert_one({
        'registration_id': solo_reg_id,
        'status': 'succeeded',
        'amount': 20,
        'currency': 'EUR',
        'provider': 'paypal',
        'created_at': now,
        'paid_at': now,
    })

    # Team with creator (pending payment) and partner (covered)
    creator_id = ObjectId()
    partner_id = ObjectId()
    await db_mod.db.users.insert_one({
        '_id': creator_id,
        'email': 'creator.participants@example.com',
        'first_name': 'Team',
        'last_name': 'Leader',
        'gender': 'female',
        'created_at': now,
        'updated_at': now,
    })
    await db_mod.db.users.insert_one({
        '_id': partner_id,
        'email': 'partner.participants@example.com',
        'first_name': 'Partner',
        'last_name': 'Two',
        'gender': 'male',
        'created_at': now,
        'updated_at': now,
    })
    team_id = ObjectId()
    await db_mod.db.teams.insert_one({
        '_id': team_id,
        'event_id': ev_id,
        'created_by_user_id': creator_id,
        'members': [
            {
                'type': 'user',
                'user_id': creator_id,
                'email': 'creator.participants@example.com',
                'diet': 'omnivore',
                'kitchen_available': True,
                'main_course_possible': True,
            },
            {
                'type': 'user',
                'user_id': partner_id,
                'email': 'partner.participants@example.com',
                'diet': 'vegetarian',
                'kitchen_available': True,
                'main_course_possible': False,
                'gender': 'male',
            },
        ],
        'status': 'pending',
        'created_at': now,
        'updated_at': now,
    })
    creator_reg_id = ObjectId()
    partner_reg_id = ObjectId()
    await db_mod.db.registrations.insert_one({
        '_id': creator_reg_id,
        'event_id': ev_id,
        'team_id': team_id,
        'user_id': creator_id,
    'user_email_snapshot': 'creator.participants@example.com',
        'team_size': 2,
        'status': 'pending_payment',
        'preferences': {},
        'diet': 'omnivore',
        'created_at': now,
        'updated_at': now,
    })
    await db_mod.db.registrations.insert_one({
        '_id': partner_reg_id,
        'event_id': ev_id,
        'team_id': team_id,
        'user_id': partner_id,
    'user_email_snapshot': 'partner.participants@example.com',
        'team_size': 2,
        'status': 'confirmed',
        'preferences': {},
        'diet': 'vegetarian',
        'created_at': now,
        'updated_at': now,
    })
    await db_mod.db.payments.insert_one({
        'registration_id': creator_reg_id,
        'status': 'pending',
        'amount': 40,
        'currency': 'EUR',
        'provider': 'stripe',
        'created_at': now,
    })

    resp = await client.get(
        f"/admin/events/{ev_id}/participants",
        headers={'Authorization': f'Bearer {admin_token}'},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload['event_id'] == str(ev_id)
    assert payload['summary']['total'] == 3
    emails = {entry['email']: entry for entry in payload['participants']}
    assert emails['solo.participants@example.com']['payment_status'] == 'paid'
    assert emails['solo.participants@example.com']['first_name'] == 'Solo'
    assert emails['creator.participants@example.com']['payment_status'] in {'pending', 'pending_payment'}
    assert emails['creator.participants@example.com']['team_role'] == 'creator'
    assert emails['partner.participants@example.com']['payment_status'] == 'covered_by_team'
    assert emails['partner.participants@example.com']['team_role'] == 'partner'
    assert payload['summary']['by_payment_status']['paid'] == 1