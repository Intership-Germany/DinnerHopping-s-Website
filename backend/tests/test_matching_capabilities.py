import asyncio

import pytest
import datetime as dt
from bson.objectid import ObjectId
from app import db as db_mod

@pytest.mark.asyncio
async def test_matching_team_capabilities_fallbacks(client, admin_token):
    now = dt.datetime.now(dt.timezone.utc)
    ev_id = ObjectId()
    await db_mod.db.events.insert_one({
        '_id': ev_id,
        'title': 'Capabilities Event',
        'status': 'published',
        'registration_deadline': now - dt.timedelta(days=1),
    })
    # Users & registrations
    users = [
        {  # A: kitchen true, main false (both in user + registration pref)
            'email': 'a@example.com', 'kitchen_available': True, 'main_course_possible': False,
            'prefs': {'kitchen_available': True, 'main_course_possible': False}
        },
        {  # B: main true only in registration pref
            'email': 'b@example.com', 'prefs': {'main_course_possible': True}
        },
        {  # C: no kitchen, no main anywhere
            'email': 'c@example.com', 'prefs': {}
        }
    ]
    reg_ids = []
    for u in users:
        await db_mod.db.users.insert_one({
            'email': u['email'],
            'first_name': u['email'].split('@')[0],
            'last_name': 'User',
            'email_verified': True,
            'created_at': now,
            'updated_at': now,
            **({ 'kitchen_available': u.get('kitchen_available') } if 'kitchen_available' in u else {}),
            **({ 'main_course_possible': u.get('main_course_possible') } if 'main_course_possible' in u else {}),
        })
        rid = ObjectId()
        reg_doc = {
            '_id': rid,
            'event_id': ev_id,
            'user_email_snapshot': u['email'],
            'team_id': None,
            'team_size': 1,
            'preferences': u['prefs'],
            'diet': 'omnivore',
            'status': 'confirmed',
            'created_at': now,
            'updated_at': now,
        }
        await db_mod.db.registrations.insert_one(reg_doc)
        reg_ids.append(rid)
    # Start matching (persist proposal)
    resp = await client.post(f"/matching/{ev_id}/start", json={'algorithms': ['greedy']}, headers={'Authorization': f'Bearer {admin_token}'})
    assert resp.status_code in (200, 202), resp.text
    job_payload = resp.json()
    job_url = job_payload['poll_url']

    job_data = None
    for _ in range(60):
        job_resp = await client.get(job_url, headers={'Authorization': f'Bearer {admin_token}'})
        assert job_resp.status_code == 200, job_resp.text
        job_data = job_resp.json()
        if job_data['status'] in {'completed', 'failed', 'cancelled'}:
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail('matching job did not complete in time')
    assert job_data['status'] == 'completed', job_data
    # Retrieve details
    details = await client.get(f"/matching/{ev_id}/details", headers={'Authorization': f'Bearer {admin_token}'})
    assert details.status_code == 200, details.text
    data = details.json()
    td = data['team_details']
    # Team ids are solo:<registration_id>
    def tid(rid):
        return f"solo:{rid}"
    # A: can_host_any True (kitchen), can_host_main False
    a_tid = tid(reg_ids[0])
    assert td[a_tid]['can_host_main'] is False
    # 'can_host_any' not directly exposed, but appetizer/dessert hosting eligibility uses can_host_any -> ensure attribute present in internal map by requesting groups
    # B: main true via registration prefs
    b_tid = tid(reg_ids[1])
    assert td[b_tid]['can_host_main'] is True
    # C: neither main nor kitchen -> can_host_main False; kitchen fallback False (not directly in team_details, but main False enough)
    c_tid = tid(reg_ids[2])
    assert td[c_tid]['can_host_main'] is False

