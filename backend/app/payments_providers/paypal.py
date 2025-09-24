import os
import base64
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger('payments.paypal')


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


async def _api_post(path: str, *, json: Optional[dict] = None, data: Optional[dict] = None, headers: Optional[dict] = None):
    """Internal helper to POST to PayPal with bearer token automatically.

    Used for endpoints beyond Orders (e.g., webhook signature verification).
    """
    httpx = _import_httpx()
    token = await get_access_token()
    merged_headers = {'Authorization': f'Bearer {token}'}
    if json is not None:
        merged_headers['Content-Type'] = 'application/json'
    if headers:
        merged_headers.update(headers)
    url = f"{_paypal_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=json, data=data, headers=merged_headers)
        logger.debug('paypal.api_post path=%s status=%s', path, resp.status_code)
        return resp
    except Exception:
        logger.exception('paypal.api_post.error path=%s', path)
        raise


async def get_access_token() -> str:
    client_id = os.getenv('PAYPAL_CLIENT_ID')
    client_secret = os.getenv('PAYPAL_CLIENT_SECRET') or os.getenv('PAYPAL_SECRET')
    if not client_id or not client_secret:
        raise RuntimeError('PayPal not configured')
    httpx = _import_httpx()
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    url = f"{_paypal_base()}/v1/oauth2/token"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                url,
                headers={'Authorization': f'Basic {auth}'},
                data={'grant_type': 'client_credentials'},
            )
        if resp.status_code >= 300:
            logger.error('paypal.token.error status=%s body=%s', resp.status_code, resp.text[:200])
            raise RuntimeError(f'PayPal token error: {resp.text[:200]}')
        data = resp.json()
        token = data.get('access_token')
        logger.debug('paypal.token.ok len=%s', len(token) if token else 0)
        return token
    except Exception:
        logger.exception('paypal.token.exception')
        raise


async def create_order(amount_cents: int, currency: str, payment_id) -> Dict[str, Any]:
    httpx = _import_httpx()
    token = await get_access_token()
    base_url = os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
    return_url = f"{base_url}/payments/paypal/return?payment_id={str(payment_id)}"
    cancel_url = f"{base_url}/payments/{str(payment_id)}/cancel"
    logger.info('paypal.create_order.start payment_id=%s amount_cents=%s currency=%s', payment_id, amount_cents, currency)
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
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{_paypal_base()}/v2/checkout/orders",
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                json=payload,
            )
        if resp.status_code >= 300:
            logger.error('paypal.create_order.error payment_id=%s status=%s body=%s', payment_id, resp.status_code, resp.text[:200])
            raise RuntimeError(f'PayPal order error: {resp.text[:200]}')
    except Exception:
        logger.exception('paypal.create_order.exception payment_id=%s', payment_id)
        raise
    data = resp.json()
    approval_link = None
    payer_action_link = None
    for l in data.get('links', []) or []:
        rel = (l.get('rel') or '').lower()
        if rel == 'approve':
            approval_link = l.get('href')
        elif rel == 'payer-action':
            payer_action_link = l.get('href')
    order_id = data.get('id')
    logger.info('paypal.create_order.ok payment_id=%s order_id=%s approval_link=%s', payment_id, order_id, (approval_link or payer_action_link))
    return {'id': order_id, 'approval_link': approval_link or payer_action_link, 'payer_action_link': payer_action_link, 'raw': data}


async def capture_order(order_id: str) -> Dict[str, Any]:
    httpx = _import_httpx()
    token = await get_access_token()
    logger.info('paypal.capture_order.start order_id=%s', order_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{_paypal_base()}/v2/checkout/orders/{order_id}/capture",
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        )
    if resp.status_code >= 300:
        logger.error('paypal.capture_order.error order_id=%s status=%s body=%s', order_id, resp.status_code, resp.text[:200])
        raise RuntimeError(f'PayPal capture error: {resp.text[:200]}')
    data = resp.json()
    logger.info('paypal.capture_order.ok order_id=%s status=%s', order_id, data.get('status'))
    return data


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
        logger.error('paypal.get_order.error order_id=%s status=%s body=%s', order_id, resp.status_code, resp.text[:200])
        raise RuntimeError(f'PayPal get order error: {resp.text[:200]}')
    data = resp.json()
    logger.debug('paypal.get_order.ok order_id=%s status=%s', order_id, data.get('status'))
    return data


async def verify_webhook_signature(
    *,
    webhook_id: str,
    transmission_id: str,
    transmission_time: str,
    cert_url: str,
    auth_algo: str,
    transmission_sig: str,
    event_body: dict,
) -> bool:
    """Verify a PayPal webhook signature via the official API.

    Docs: https://developer.paypal.com/docs/api/webhooks/v1/#verify-webhook-signature

    Returns True if signature status is SUCCESS; False otherwise (does not raise except for transport errors).
    """
    payload = {
        "transmission_id": transmission_id,
        "transmission_time": transmission_time,
        "cert_url": cert_url,
        "auth_algo": auth_algo,
        "transmission_sig": transmission_sig,
        "webhook_id": webhook_id,
        "webhook_event": event_body,
    }
    resp = await _api_post('/v1/notifications/verify-webhook-signature', json=payload)
    if resp.status_code >= 300:
        # Transport / API error – treat as failed verification but include context
        logger.warning('paypal.verify_webhook_signature.transport_error status=%s', resp.status_code)
        return False
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        logger.warning('paypal.verify_webhook_signature.invalid_json')
        return False
    ok = (data.get('verification_status') or '').upper() == 'SUCCESS'
    logger.info('paypal.verify_webhook_signature status=%s', data.get('verification_status'))
    return ok
