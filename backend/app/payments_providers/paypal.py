import os
import base64
from typing import Dict, Any


def _paypal_base() -> str:
    env = (os.getenv('PAYPAL_MODE') or os.getenv('PAYPAL_ENV') or 'sandbox').lower()
    if env == 'live':
        return 'https://api-m.paypal.com'
    return 'https://api-m.sandbox.paypal.com'


def _import_httpx():
    import importlib
    try:
        return importlib.import_module('httpx')
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError('httpx is required for PayPal integration') from exc


async def get_access_token() -> str:
    client_id = os.getenv('PAYPAL_CLIENT_ID')
    client_secret = os.getenv('PAYPAL_CLIENT_SECRET') or os.getenv('PAYPAL_SECRET')
    if not client_id or not client_secret:
        raise RuntimeError('PayPal not configured')
    httpx = _import_httpx()
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{_paypal_base()}/v1/oauth2/token",
            headers={'Authorization': f'Basic {auth}'},
            data={'grant_type': 'client_credentials'},
        )
        if resp.status_code >= 300:
            raise RuntimeError(f'PayPal token error: {resp.text[:200]}')
        data = resp.json()
        return data.get('access_token')


async def create_order(amount_cents: int, currency: str, payment_id) -> Dict[str, Any]:
    httpx = _import_httpx()
    token = await get_access_token()
    base_url = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
    return_url = f"{base_url}/payments/paypal/return?payment_id={str(payment_id)}"
    cancel_url = f"{base_url}/payments/{str(payment_id)}/cancel"
    payload = {
        'intent': 'CAPTURE',
        'purchase_units': [
            {
                'amount': {
                    'currency_code': (currency or 'EUR').upper(),
                    'value': f"{amount_cents/100:.2f}",
                },
                'reference_id': str(payment_id),
            }
        ],
        'application_context': {
            'return_url': return_url,
            'cancel_url': cancel_url,
            'brand_name': 'DinnerHopping',
            'user_action': 'PAY_NOW',
        },
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{_paypal_base()}/v2/checkout/orders",
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json=payload,
        )
    if resp.status_code >= 300:
        raise RuntimeError(f'PayPal order error: {resp.text[:200]}')
    data = resp.json()
    approval_link = None
    payer_action_link = None
    for l in data.get('links', []) or []:
        rel = (l.get('rel') or '').lower()
        if rel == 'approve':
            approval_link = l.get('href')
        elif rel == 'payer-action':
            payer_action_link = l.get('href')
    return {'id': data.get('id'), 'approval_link': approval_link or payer_action_link, 'payer_action_link': payer_action_link, 'raw': data}


async def capture_order(order_id: str) -> Dict[str, Any]:
    httpx = _import_httpx()
    token = await get_access_token()
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{_paypal_base()}/v2/checkout/orders/{order_id}/capture",
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        )
    if resp.status_code >= 300:
        raise RuntimeError(f'PayPal capture error: {resp.text[:200]}')
    return resp.json()


async def get_order(order_id: str) -> Dict[str, Any]:
    """Fetch PayPal order details (Orders v2: Show order details)."""
    httpx = _import_httpx()
    token = await get_access_token()
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{_paypal_base()}/v2/checkout/orders/{order_id}",
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        )
    if resp.status_code >= 300:
        raise RuntimeError(f'PayPal get order error: {resp.text[:200]}')
    return resp.json()
