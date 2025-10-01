import os
import pytest

# Ensure insecure cookies allowed for test client
os.environ['ALLOW_INSECURE_COOKIES'] = '1'


@pytest.mark.asyncio
async def test_csrf_middleware_blocks_cookie_only_post(client, verified_user):
    # Enable CSRF enforcement for this test only
    prev = os.environ.get('CSRF_ENFORCE')
    os.environ['CSRF_ENFORCE'] = 'true'
    try:
        # Login to obtain cookies
        resp = await client.post('/login', json={'username': verified_user['email'], 'password': verified_user['password']})
        assert resp.status_code == 200
        cookies = client.cookies
        # Simulate cookie-only POST to a protected endpoint (e.g., profile update) without X-CSRF-Token
        # Use the cookie jar already attached to the client
        payload = {'first_name': 'Hacker'}
        r = await client.put('/profile', json=payload)
        # Should be blocked with 403 by CSRFMiddleware when cookie auth present and no header
        assert r.status_code == 403
    finally:
        # restore previous env
        if prev is None:
            os.environ.pop('CSRF_ENFORCE', None)
        else:
            os.environ['CSRF_ENFORCE'] = prev
 