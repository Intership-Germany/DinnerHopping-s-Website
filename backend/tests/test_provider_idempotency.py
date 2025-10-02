import sys
import types
import pytest

from app.payments_providers import stripe as stripe_mod
from app.payments_providers import paypal as paypal_mod


def test_stripe_forwards_idempotency_key(monkeypatch):
    """Ensure our normalized idempotency key is passed to stripe.checkout.Session.create as idempotency_key."""
    captured = {}

    class FakeSession:
        @staticmethod
        def create(**kwargs):
            # capture the kwargs for assertions
            captured.update(kwargs)

            class S:
                id = 'sess_abc123'
                url = 'https://stripe.test/checkout/sess_abc123'

            return S()

    fake_stripe = types.SimpleNamespace(checkout=types.SimpleNamespace(Session=FakeSession))
    # Inject fake stripe module into sys.modules so the adapter import resolves to it
    monkeypatch.setitem(sys.modules, 'stripe', fake_stripe)
    # Adapter checks STRIPE_API_KEY at runtime; set a dummy key for the test
    monkeypatch.setenv('STRIPE_API_KEY', 'sk_test_dummy')

    res = stripe_mod.create_checkout_session(2500, 'payment-id-1', idempotency_key='my-server-key')

    assert captured.get('idempotency_key') == 'my-server-key'
    assert res['id'] == 'sess_abc123'


@pytest.mark.asyncio
async def test_paypal_sets_request_id_header(monkeypatch):
    """Ensure PayPal adapter sends PayPal-Request-Id header when idempotency_key is provided."""
    captured = {}

    class FakeResponse:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self._body = body

        def json(self):
            return self._body

        @property
        def text(self):
            return str(self._body)

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, data=None, headers=None):
            # capture headers for assertion
            captured['headers'] = headers
            # simulate a successful create-order response
            body = {'id': 'ORDER-XYZ', 'links': [{'rel': 'approve', 'href': 'https://paypal.test/approve'}]}
            return FakeResponse(201, body)

    fake_httpx = types.SimpleNamespace(AsyncClient=FakeAsyncClient)
    # Monkeypatch the module loader used in paypal adapter
    monkeypatch.setattr(paypal_mod, '_import_httpx', lambda: fake_httpx)
    # PayPal adapter validates env vars and requests a token; stub those
    monkeypatch.setenv('PAYPAL_CLIENT_ID', 'paypal-client')
    monkeypatch.setenv('PAYPAL_CLIENT_SECRET', 'paypal-secret')

    async def _fake_token():
        return 'fake-token'

    monkeypatch.setattr(paypal_mod, 'get_access_token', _fake_token)

    order = await paypal_mod.create_order(1500, 'EUR', 'payment-id-2', idempotency_key='paypal-key-987')

    assert captured.get('headers') is not None
    assert captured['headers'].get('PayPal-Request-Id') == 'paypal-key-987'
    assert order.get('id') == 'ORDER-XYZ'
