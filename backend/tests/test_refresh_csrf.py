import pytest

from app import db as db_mod


async def _login_and_get(client, email, password):
    resp = await client.post('/login', json={'username': email, 'password': password})
    assert resp.status_code == 200
    data = resp.json()
    csrf = data.get('csrf_token')
    # try both host-prefixed and dev cookie names
    cookies = client.cookies
    refresh = cookies.get('__Host-refresh_token') or cookies.get('refresh_token')
    csrf_cookie = cookies.get('__Host-csrf_token') or cookies.get('csrf_token')
    return refresh, csrf, csrf_cookie


@pytest.mark.asyncio
async def test_refresh_requires_csrf_and_rotates(client, verified_user):
    # login - allow insecure cookies in test environment so http client will send them
    import os
    os.environ['ALLOW_INSECURE_COOKIES'] = '1'
    old_refresh, csrf_token, csrf_cookie = await _login_and_get(client, verified_user['email'], verified_user['password'])
    assert old_refresh is not None
    assert csrf_token is not None
    assert csrf_cookie is not None

    # Attempt refresh without CSRF header -> should be forbidden
    resp = await client.post('/refresh')
    assert resp.status_code == 403

    # Now refresh with correct CSRF header and cookie
    headers = {'x-csrf-token': csrf_token}
    resp2 = await client.post('/refresh', headers=headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert 'access_token' in data2

    # New refresh cookie should be set
    cookies = client.cookies
    new_refresh = cookies.get('__Host-refresh_token') or cookies.get('refresh_token')
    assert new_refresh is not None
    assert new_refresh != old_refresh

    # Attempt to use the old refresh token (simulate attacker replay)
    # send cookie header with old refresh and old csrf
    cookie_header = f"__Host-refresh_token={old_refresh}; __Host-csrf_token={csrf_cookie}"
    headers2 = {'x-csrf-token': csrf_token, 'Cookie': cookie_header}
    resp3 = await client.post('/refresh', headers=headers2)
    # Should be invalid (401)
    assert resp3.status_code == 401

    # Cleanup: remove any refresh tokens for the test user
    await db_mod.db.refresh_tokens.delete_many({'user_email': verified_user['email']})
