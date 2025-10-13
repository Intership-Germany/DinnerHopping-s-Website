import os
import base64
import logging
import datetime
from typing import Dict, Any, Optional

from pymongo import ReturnDocument
from pymongo.errors import PyMongoError, DuplicateKeyError

from app import db as db_mod

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


async def create_order(amount_cents: int, currency: str, payment_id, idempotency_key: str | None = None) -> Dict[str, Any]:
    httpx = _import_httpx()
    token = await get_access_token()
    # Frontend fallback: direct user to the payment landing page which will forward token
    base = os.getenv('FRONTEND_BASE_URL') or 'http://localhost:8000'
    return_url = f"{base.rstrip('/')}/payement?payment_id={str(payment_id)}"
    cancel_url = f"{base.rstrip('/')}/payement?payment_id={str(payment_id)}&status=cancelled"
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
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    if idempotency_key:
        # PayPal supports idempotency via PayPal-Request-Id header
        headers['PayPal-Request-Id'] = str(idempotency_key)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{_paypal_base()}/v2/checkout/orders",
                headers=headers,
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


def get_frontend_config() -> Dict[str, str]:
    client_id = os.getenv('PAYPAL_CLIENT_ID')
    if not client_id:
        raise RuntimeError('PayPal not configured')
    currency = (os.getenv('PAYMENT_CURRENCY') or 'EUR').upper()
    env = (os.getenv('PAYPAL_MODE') or os.getenv('PAYPAL_ENV') or 'sandbox').lower()
    return {"clientId": client_id, "currency": currency, "env": env}


async def get_or_create_order_for_registration(
    registration_oid,
    *,
    amount_cents: int,
    currency: str,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    existing = await db_mod.db.payments.find_one({"registration_id": registration_oid, "provider": "paypal"})
    expired_states = {"refunded", "failed", "cancelled", "cancelled_by_user", "cancelled_admin", "expired"}
    if existing:
        status = (existing.get('status') or '').lower()
        has_valid_link = bool(existing.get('provider_payment_id') and existing.get('payment_link'))
        if has_valid_link and status not in expired_states:
            logger.debug(
                'payment.create.paypal_order.idempotent registration_id=%s payment_id=%s order_id=%s',
                registration_oid,
                existing.get('_id'),
                existing.get('provider_payment_id'),
            )
            return {"payment": existing, "order_id": existing.get('provider_payment_id')}

    initial_doc = {
        "registration_id": registration_oid,
        "amount": amount_cents / 100.0,
        "currency": (currency or 'EUR').upper(),
        "status": "in_process",
        "provider": "paypal",
        "idempotency_key": idempotency_key,
        "meta": {},
    "created_at": datetime.datetime.now(datetime.timezone.utc),
    }
    # Use insert-then-fallback approach to avoid a findAndModify duplicate-key race
    doc = await db_mod.db.payments.find_one({"registration_id": registration_oid, "provider": "paypal"})
    if not doc:
        try:
            insert_result = await db_mod.db.payments.insert_one(initial_doc)
            doc = await db_mod.db.payments.find_one({"_id": insert_result.inserted_id})
        except Exception as exc:
            # Be defensive: duplicate key errors can be raised/wrapped in different shapes
            is_dup = False
            try:
                if isinstance(exc, DuplicateKeyError):
                    is_dup = True
            except Exception:
                pass
            if not is_dup:
                try:
                    if getattr(exc, 'code', None) == 11000:
                        is_dup = True
                except Exception:
                    pass
            if not is_dup:
                try:
                    if 'duplicate key' in str(exc).lower():
                        is_dup = True
                except Exception:
                    pass
            if not is_dup:
                raise
            # Another concurrent writer created the payment. Load and continue.
            logger.info('paypal.get_or_create_order_for_registration.duplicate registration_id=%s exc=%s', registration_oid, str(exc))
            doc = await db_mod.db.payments.find_one({"registration_id": registration_oid, "provider": "paypal"})
            if not doc:
                # Unexpected: re-raise to surface the error
                raise
    payment_id = doc.get('_id')
    # If we are retrying after a previous failed/expired attempt, alter the PayPal idempotency header
    # to force a fresh order on PayPal side, while keeping DB idempotency stable.
    paypal_request_id = idempotency_key
    if existing and idempotency_key:
        try:
            ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            paypal_request_id = f"{idempotency_key}:{ts}"
        except Exception:
            paypal_request_id = idempotency_key
    order = await create_order(amount_cents, currency or 'EUR', payment_id, idempotency_key=paypal_request_id)
    approval = order.get('approval_link')
    order_id = order.get('id')
    await db_mod.db.payments.update_one(
        {"_id": payment_id},
        {"$set": {"provider_payment_id": order_id, "payment_link": approval, "status": "in_process", "meta": {"create_order": order}}},
    )
    try:
        await db_mod.db.registrations.update_one({"_id": registration_oid}, {"$set": {"payment_id": payment_id}})
    except PyMongoError:
        pass
    doc['provider_payment_id'] = order_id
    doc['payment_link'] = approval
    doc['meta'] = {"create_order": order}
    logger.info(
        'payment.create.paypal_order.ok registration_id=%s payment_id=%s order_id=%s',
        registration_oid,
        payment_id,
        order_id,
    )
    return {"payment": doc, "order_id": order_id}


async def ensure_paypal_payment(
    registration_oid,
    *,
    amount_cents: int,
    currency: str,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    existing = await db_mod.db.payments.find_one({"registration_id": registration_oid, "provider": "paypal"})
    expired_states = {"refunded", "failed", "cancelled", "cancelled_by_user", "cancelled_admin", "expired"}
    if existing:
        status = (existing.get('status') or '').lower()
        has_valid_link = bool(existing.get('provider_payment_id') and existing.get('payment_link'))
        if has_valid_link and status not in expired_states:
            logger.debug(
                'payment.create.paypal.existing registration_id=%s payment_id=%s',
                registration_oid,
                existing.get('_id'),
            )
            return existing

    initial_doc = {
        "registration_id": registration_oid,
        "amount": amount_cents / 100.0,
        "currency": (currency or 'EUR').upper(),
        "status": "in_process",
        "provider": "paypal",
        "idempotency_key": idempotency_key,
        "meta": {},
    "created_at": datetime.datetime.now(datetime.timezone.utc),
    }
    # Use insert-then-fallback approach to avoid a findAndModify duplicate-key race
    doc = await db_mod.db.payments.find_one({"registration_id": registration_oid, "provider": "paypal"})
    if not doc:
        try:
            insert_result = await db_mod.db.payments.insert_one(initial_doc)
            doc = await db_mod.db.payments.find_one({"_id": insert_result.inserted_id})
        except Exception as exc:
            is_dup = False
            try:
                if isinstance(exc, DuplicateKeyError):
                    is_dup = True
            except Exception:
                pass
            if not is_dup:
                try:
                    if getattr(exc, 'code', None) == 11000:
                        is_dup = True
                except Exception:
                    pass
            if not is_dup:
                try:
                    if 'duplicate key' in str(exc).lower():
                        is_dup = True
                except Exception:
                    pass
            if not is_dup:
                raise
            logger.info('paypal.ensure_paypal_payment.duplicate registration_id=%s exc=%s', registration_oid, str(exc))
            doc = await db_mod.db.payments.find_one({"registration_id": registration_oid, "provider": "paypal"})
            if not doc:
                # Unexpected: re-raise to surface the error
                raise
    payment_id = doc.get('_id')
    paypal_request_id = idempotency_key
    if existing and idempotency_key:
        try:
            ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            paypal_request_id = f"{idempotency_key}:{ts}"
        except Exception:
            paypal_request_id = idempotency_key
    order = await create_order(amount_cents, currency or 'EUR', payment_id, idempotency_key=paypal_request_id)
    approval = order.get('approval_link')
    order_id = order.get('id')
    await db_mod.db.payments.update_one(
        {"_id": payment_id},
        {"$set": {"provider_payment_id": order_id, "payment_link": approval, "status": "in_process", "meta": {"create_order": order}}},
    )
    try:
        await db_mod.db.registrations.update_one({"_id": registration_oid}, {"$set": {"payment_id": payment_id}})
    except PyMongoError:
        pass
    doc['provider_payment_id'] = order_id
    doc['payment_link'] = approval
    doc['meta'] = {"create_order": order}
    logger.info(
        'payment.create.paypal.ok registration_id=%s payment_id=%s order_id=%s',
        registration_oid,
        payment_id,
        order_id,
    )
    return doc


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
