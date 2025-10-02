import pytest
from app import db as db_mod
from app.auth import create_access_token


@pytest.mark.asyncio
async def test_email_template_crud(client):
    # create admin user
    await db_mod.db.users.insert_one({'email': 'admin@example.com', 'password_hash': 'x', 'roles': ['admin']})
    token = create_access_token({'sub': 'admin@example.com'})
    headers = {'Authorization': f'Bearer {token}'}
    payload = {
        'key': 'test_template',
        'subject': 'Hello {{name}}',
        'html_body': '<p>Hi {{name}}</p>',
        'description': 'Test template',
        'variables': ['name']
    }
    r = await client.post('/admin/email-templates', json=payload, headers=headers)
    assert r.status_code == 200, r.text
    r = await client.get('/admin/email-templates', headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert any(t['key'] == 'test_template' for t in data)
    payload['subject'] = 'Hello {{name}} UPDATED'
    r = await client.put('/admin/email-templates/test_template', json=payload, headers=headers)
    assert r.status_code == 200
    r = await client.get('/admin/email-templates/test_template', headers=headers)
    assert r.status_code == 200
    r = await client.delete('/admin/email-templates/test_template', headers=headers)
    assert r.status_code == 200

