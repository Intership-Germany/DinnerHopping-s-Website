from fastapi import APIRouter, HTTPException, Header, Request, Depends
from pydantic import BaseModel
import os, datetime, logging, re
from enum import Enum
from typing import Optional
from bson.objectid import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from app import db as db_mod
from app.auth import get_current_user, require_admin
from app.utils import (
    require_event_published,
    require_registration_owner_or_admin,
    require_event_payment_open,
    finalize_registration_payment,
    _is_admin,
)
from app.payments_providers import paypal as paypal_provider
from app.payments_providers import stripe as stripe_provider
from app.payments_providers import wero as wero_provider

######### Router / Endpoints #########

router = APIRouter()
log = logging.getLogger('payments')
_IDEMPOTENCY_ALLOWED = re.compile(r'[^a-z0-9:._-]')

class PaymentProvider(str, Enum):
    auto = 'auto'
    paypal = 'paypal'
    stripe = 'stripe'
    wero = 'wero'


class PaymentFlow(str, Enum):
    redirect = 'redirect'
    order = 'order'  # PayPal JS SDK order creation flow


class CreatePaymentRequest(BaseModel):
    registration_id: str
    amount_cents: int | None = None
    idempotency_key: str | None = None
    provider: PaymentProvider | None = PaymentProvider.auto
    flow: PaymentFlow | None = PaymentFlow.redirect
    currency: Optional[str] = 'EUR'


class CapturePaymentIn(BaseModel):
    provider: Optional[str] = None
    order_id: Optional[str] = None


# Provider-specific logic delegated to app.payments_providers

@router.get('/paypal/config')
async def paypal_config():
    """Expose minimal PayPal client configuration for the frontend.

    Returns the client-id and currency so the JS SDK can be initialized.
    """
    try:
        return paypal_provider.get_frontend_config()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/providers')
async def list_providers_early():
    """List available payment providers (registered early to avoid param route capture).

    This duplicate is intentionally placed before parameterized routes so that
    the static path `/providers` is matched instead of the generic `/{payment_id}`.
    """
    providers = []
    paypal_configured = os.getenv('PAYPAL_CLIENT_ID') and (os.getenv('PAYPAL_CLIENT_SECRET') or os.getenv('PAYPAL_SECRET'))
    if paypal_configured:
        providers.append('paypal')
    if os.getenv('STRIPE_API_KEY'):
        providers.append('stripe')
    # Wero is always available as manual SEPA transfer
    providers.append('wero')
    default = 'paypal' if 'paypal' in providers else ('stripe' if 'stripe' in providers else 'wero')
    return {"providers": providers, "default": default}


@router.get('/stripe/config')
async def stripe_config():
    """Return the publishable key and currency for initializing Stripe.js."""
    publishable = os.getenv('STRIPE_PUBLISHABLE_KEY')
    secret = os.getenv('STRIPE_API_KEY')
    if not publishable or not secret:
        raise HTTPException(status_code=400, detail='Stripe not configured')
    currency = (os.getenv('PAYMENT_CURRENCY') or 'EUR').upper()
    mode = 'test' if publishable.startswith('pk_test_') or (secret or '').startswith('sk_test_') else 'live'
    return {"publishableKey": publishable, "currency": currency, "mode": mode}


@router.post('/paypal/orders/{order_id}/capture')
async def paypal_capture_order(order_id: str):
    """Capture a PayPal order by its id (Standard Checkout onApprove hook)."""
    if not order_id:
        raise HTTPException(status_code=400, detail='order_id required')
    # find the payment doc
    pay = await db_mod.db.payments.find_one({"provider": "paypal", "provider_payment_id": order_id})
    if not pay:
        # Not strictly required but helps tie back to registration updates
        capture = await paypal_provider.capture_order(order_id)
        return capture
    capture = await paypal_provider.capture_order(order_id)
    status = (capture.get('status') or '').upper()
    now = datetime.datetime.now(datetime.timezone.utc)
    if status == 'COMPLETED':
        log.info('paypal.capture.completed order_id=%s payment_id=%s', order_id, pay.get('_id'))
        await db_mod.db.payments.update_one({"_id": pay.get('_id')}, {"$set": {"status": "succeeded", "paid_at": now, "meta.capture": capture}})
        await finalize_registration_payment(pay.get('registration_id'), pay.get('_id'))
        return {"status": "COMPLETED"}
    log.warning('paypal.capture.failed order_id=%s payment_id=%s status=%s', order_id, pay.get('_id'), status)
    await db_mod.db.payments.update_one({"_id": pay.get('_id')}, {"$set": {"status": "failed", "meta.capture": capture}})
    return {"status": status or "FAILED", "detail": capture}


@router.get('/paypal/orders/{order_id}')
async def paypal_get_order(order_id: str):
    """Return details for a PayPal order (debug/support for Standard Buttons)."""
    if not order_id:
        raise HTTPException(status_code=400, detail='order_id required')
    try:
        details = await paypal_provider.get_order(order_id)
    except Exception as e:  # keeping broad due to provider SDK variability
        raise HTTPException(status_code=502, detail=f'PayPal error: {str(e)}') from e
    return details

def _enum_value(value, enum_cls):
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value.value
    return str(value).lower().strip()


def _build_payment_response(
    *,
    payment_doc,
    provider: str,
    amount_cents: int,
    currency: str,
    idempotency_key: str,
    next_action: Optional[dict] = None,
) -> dict:
    amount_minor = amount_cents
    if payment_doc and not amount_minor:
        amount_minor = int(round((payment_doc.get('amount') or 0) * 100))
    if not amount_minor and payment_doc:
        amount_minor = 0
    resp = {
        "payment_id": str(payment_doc.get('_id')) if payment_doc else None,
        "status": (payment_doc or {}).get('status') or 'pending',
        "amount_cents": amount_minor,
        "currency": (payment_doc or {}).get('currency') or currency,
        "provider": provider,
        "idempotency_key": idempotency_key,
    }
    if payment_doc:
        link = payment_doc.get('payment_link')
        if link:
            resp['payment_link'] = link
        if payment_doc.get('meta') and isinstance(payment_doc['meta'], dict):
            resp['meta'] = payment_doc['meta']
    if next_action:
        resp['next_action'] = next_action
    return resp


def _normalize_idempotency_key(raw_key, registration_id, provider: str, flow: str) -> str:
    candidate: str = ''
    if raw_key is not None:
        candidate = str(raw_key).strip().lower()
    if candidate:
        candidate = _IDEMPOTENCY_ALLOWED.sub('-', candidate)
        candidate = re.sub('-{2,}', '-', candidate)
        candidate = candidate.strip('-_.:')
    fallback = f"{str(registration_id)}:{provider}:{flow}"
    key = candidate or fallback
    if len(key) > 128:
        key = key[:128]
    return key


@router.post('/create')
async def create_payment(payload: CreatePaymentRequest, current_user=Depends(get_current_user)):
    """Create a payment record and return the next action to the client."""

    try:
        reg_obj = ObjectId(payload.registration_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail='Invalid registration_id') from exc

    reg = await require_registration_owner_or_admin(current_user, reg_obj)
    if not reg:
        raise HTTPException(status_code=404, detail='Registration not found')

    provider = _enum_value(payload.provider, PaymentProvider) or 'auto'
    flow = _enum_value(payload.flow, PaymentFlow) or 'redirect'

    if flow == 'order' and provider not in ('paypal', 'auto'):
        raise HTTPException(status_code=400, detail='flow "order" is only supported with provider=paypal')

    # Auto-select provider when requested
    if provider in ('', 'auto', None):
        paypal_configured = os.getenv('PAYPAL_CLIENT_ID') and (os.getenv('PAYPAL_CLIENT_SECRET') or os.getenv('PAYPAL_SECRET'))
        provider = 'paypal' if paypal_configured else ('stripe' if os.getenv('STRIPE_API_KEY') else 'wero')

    currency = (payload.currency or 'EUR').upper()

    ev = None
    try:
        ev = await db_mod.db.events.find_one({"_id": reg.get('event_id')}) if reg and reg.get('event_id') else None
    except PyMongoError:
        ev = None

    if ev:
        await require_event_published(ev.get('_id'))
        require_event_payment_open(ev)

    event_fee_cents = int((ev or {}).get('fee_cents') or 0)
    team_size = int((reg or {}).get('team_size') or 1)
    canonical_amount_cents = event_fee_cents * max(team_size, 1)

    if canonical_amount_cents <= 0:
        return {
            "status": "no_payment_required",
            "amount_cents": 0,
            "provider": provider,
            "idempotency_key": None,
        }

    if payload.amount_cents and payload.amount_cents != canonical_amount_cents:
        raise HTTPException(status_code=400, detail='Amount must match event fee configured by organizer')

    canonical_idempotency = _normalize_idempotency_key(payload.idempotency_key, reg_obj, provider, flow)

    existing = await db_mod.db.payments.find_one({"idempotency_key": canonical_idempotency})
    if existing:
        log.debug('payment.create.idempotent key=%s payment_id=%s', canonical_idempotency, existing.get('_id'))
        next_action = None
        if provider == 'paypal' and flow == 'order':
            next_action = {
                "type": "paypal_order",
                "order_id": existing.get('provider_payment_id'),
                "approval_link": existing.get('payment_link'),
            }
        elif existing.get('payment_link'):
            next_action = {"type": "redirect", "url": existing.get('payment_link')}
        return _build_payment_response(
            payment_doc=existing,
            provider=provider,
            amount_cents=canonical_amount_cents,
            currency=currency,
            idempotency_key=canonical_idempotency,
            next_action=next_action,
        )

    existing_for_registration = await db_mod.db.payments.find_one({"registration_id": reg_obj, "provider": provider})
    if existing_for_registration and not existing_for_registration.get('idempotency_key'):
        await db_mod.db.payments.update_one({"_id": existing_for_registration.get('_id')}, {"$set": {"idempotency_key": canonical_idempotency}})
        existing_for_registration['idempotency_key'] = canonical_idempotency

    if existing_for_registration and flow == 'order':
        next_action = {
            "type": "paypal_order",
            "order_id": existing_for_registration.get('provider_payment_id'),
            "approval_link": existing_for_registration.get('payment_link'),
        }
        return _build_payment_response(
            payment_doc=existing_for_registration,
            provider=provider,
            amount_cents=canonical_amount_cents,
            currency=currency,
            idempotency_key=canonical_idempotency,
            next_action=next_action,
        )

    # Provider-specific flows
    if provider == 'paypal':
        if flow == 'order':
            result = await paypal_provider.get_or_create_order_for_registration(
                reg_obj,
                amount_cents=canonical_amount_cents,
                currency=currency,
                idempotency_key=canonical_idempotency,
            )
            payment_doc = (result or {}).get('payment')
            order_id = (result or {}).get('order_id')
            if not payment_doc or not order_id:
                raise HTTPException(status_code=500, detail='Failed to prepare PayPal order')
            if payment_doc.get('idempotency_key') != canonical_idempotency:
                await db_mod.db.payments.update_one({"_id": payment_doc.get('_id')}, {"$set": {"idempotency_key": canonical_idempotency}})
                payment_doc['idempotency_key'] = canonical_idempotency
            next_action = {
                "type": "paypal_order",
                "order_id": order_id,
                "approval_link": payment_doc.get('payment_link'),
            }
            return _build_payment_response(
                payment_doc=payment_doc,
                provider=provider,
                amount_cents=canonical_amount_cents,
                currency=currency,
                idempotency_key=canonical_idempotency,
                next_action=next_action,
            )

        payment_doc = await paypal_provider.ensure_paypal_payment(
            reg_obj,
            amount_cents=canonical_amount_cents,
            currency=currency,
            idempotency_key=canonical_idempotency,
        )
        if payment_doc.get('idempotency_key') != canonical_idempotency:
            await db_mod.db.payments.update_one({"_id": payment_doc.get('_id')}, {"$set": {"idempotency_key": canonical_idempotency}})
            payment_doc['idempotency_key'] = canonical_idempotency
        next_action = {"type": "redirect", "url": payment_doc.get('payment_link')}
        return _build_payment_response(
            payment_doc=payment_doc,
            provider=provider,
            amount_cents=canonical_amount_cents,
            currency=currency,
            idempotency_key=canonical_idempotency,
            next_action=next_action,
        )

    if provider == 'stripe':
        stripe_key = os.getenv('STRIPE_API_KEY')
        if not stripe_key:
            raise HTTPException(status_code=400, detail='Stripe not configured')

        initial_doc = {
            "registration_id": reg_obj,
            "amount": canonical_amount_cents / 100.0,
            "currency": currency,
            "status": "pending",
            "provider": "stripe",
            "idempotency_key": canonical_idempotency,
            "meta": {},
            "created_at": datetime.datetime.now(datetime.timezone.utc),
        }
        doc = await db_mod.db.payments.find_one_and_update(
            {"registration_id": reg_obj, "provider": "stripe"},
            {"$setOnInsert": initial_doc},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if doc.get('idempotency_key') != canonical_idempotency:
            await db_mod.db.payments.update_one({"_id": doc.get('_id')}, {"$set": {"idempotency_key": canonical_idempotency}})
            doc['idempotency_key'] = canonical_idempotency

        payment_id = doc.get('_id')
        if doc.get('provider_payment_id') and doc.get('payment_link'):
            next_action = {"type": "redirect", "url": doc.get('payment_link')}
            return _build_payment_response(
                payment_doc=doc,
                provider=provider,
                amount_cents=canonical_amount_cents,
                currency=currency,
                idempotency_key=canonical_idempotency,
                next_action=next_action,
            )

        try:
            session = stripe_provider.create_checkout_session(canonical_amount_cents, payment_id, canonical_idempotency)
        except Exception as exc:  # noqa: BLE001
            try:
                await db_mod.db.payments.delete_one({"_id": payment_id, "provider_payment_id": {"$exists": False}})
            except PyMongoError:
                pass
            raise HTTPException(status_code=500, detail=f'Stripe error: {str(exc)}') from exc

        await db_mod.db.payments.update_one(
            {"_id": payment_id},
            {"$set": {"provider_payment_id": session.get('id'), "payment_link": session.get('url')}}
        )
        log.info('payment.create.stripe.ok payment_id=%s session_id=%s', payment_id, session.get('id'))
        try:
            await db_mod.db.registrations.update_one({"_id": reg_obj}, {"$set": {"payment_id": payment_id}})
        except PyMongoError:
            pass
        next_action = {"type": "redirect", "url": session.get('url')}
        doc['provider_payment_id'] = session.get('id')
        doc['payment_link'] = session.get('url')
        return _build_payment_response(
            payment_doc=doc,
            provider=provider,
            amount_cents=canonical_amount_cents,
            currency=currency,
            idempotency_key=canonical_idempotency,
            next_action=next_action,
        )

    if provider == 'wero':
        amount_cents = canonical_amount_cents
        wero_doc = {
            "registration_id": reg_obj,
            "amount": amount_cents / 100.0,
            "currency": currency,
            "status": "pending",
            "provider": "wero",
            "idempotency_key": canonical_idempotency,
            "meta": {},
            "created_at": datetime.datetime.now(datetime.timezone.utc),
        }
        doc = await db_mod.db.payments.find_one_and_update(
            {"registration_id": reg_obj, "provider": "wero"},
            {"$setOnInsert": wero_doc},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if doc.get('idempotency_key') != canonical_idempotency:
            await db_mod.db.payments.update_one({"_id": doc.get('_id')}, {"$set": {"idempotency_key": canonical_idempotency}})
            doc['idempotency_key'] = canonical_idempotency
        instructions = wero_provider.build_instructions(doc.get('_id'), amount_cents, currency)
        await db_mod.db.payments.update_one({"_id": doc.get('_id')}, {"$set": {"meta.instructions": instructions}})
        log.info('payment.create.wero.ok payment_id=%s amount_cents=%s', doc.get('_id'), amount_cents)
        try:
            await db_mod.db.registrations.update_one({"_id": reg_obj}, {"$set": {"payment_id": doc.get('_id')}})
        except PyMongoError:
            pass
        next_action = {"type": "instructions", "instructions": instructions}
        response = _build_payment_response(
            payment_doc=doc,
            provider=provider,
            amount_cents=amount_cents,
            currency=currency,
            idempotency_key=canonical_idempotency,
            next_action=next_action,
        )
        response['instructions'] = instructions
        return response

    raise HTTPException(status_code=400, detail='Unsupported payment provider or provider not configured')


# Removed dev-only pay endpoint: all providers are real (paypal/stripe/wero).


@router.get('/{payment_id}/cancel')
async def payment_cancel(payment_id: str, current_user=Depends(get_current_user)):
    """Generic cancel landing endpoint for providers. Marks payment as failed (non-destructive).
    
    Requires authentication and verifies user owns the payment through registration ownership.
    """
    try:
        oid = ObjectId(payment_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail='Invalid payment id') from exc
    p = await db_mod.db.payments.find_one({"_id": oid})
    if not p:
        raise HTTPException(status_code=404, detail='Payment not found')

    # Verify user owns this payment through registration ownership
    reg_id = p.get('registration_id')
    if reg_id:
        # authorization helper will raise HTTPException if not owner/admin
        await require_registration_owner_or_admin(current_user, reg_id)
    else:
        # Fallback: check if user is admin for orphaned payments
        if not _is_admin(current_user):
            raise HTTPException(status_code=403, detail='Access denied')
    
    await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "failed", "updated_at": datetime.datetime.utcnow()}})
    log.info('payment.cancel payment_id=%s user_email=%s', payment_id, current_user.get('email'))
    return {"status": "cancelled"}


@router.get('/{payment_id}/success')
async def payment_success(payment_id: str):
    """Generic success landing endpoint for providers that redirect. Does not mark paid by itself (Stripe uses webhook)."""
    try:
        oid = ObjectId(payment_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail='Invalid payment id') from exc
    p = await db_mod.db.payments.find_one({"_id": oid})
    if not p:
        raise HTTPException(status_code=404, detail='Payment not found')
    log.info('payment.success.landing payment_id=%s status=%s', payment_id, p.get('status'))
    return {"status": p.get('status')}


@router.get('/{payment_id}')
async def payment_details(payment_id: str, current_user=Depends(get_current_user)):
    """Return payment details including provider-specific 'source' information.

    Enforces that the caller is the registration owner or an admin.
    """
    try:
        oid = ObjectId(payment_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail='Invalid payment id') from exc
    pay = await db_mod.db.payments.find_one({"_id": oid})
    if not pay:
        raise HTTPException(status_code=404, detail='Payment not found')

    # Authorization: require registration owner or admin
    reg_obj = pay.get('registration_id')
    if reg_obj:
        reg = await require_registration_owner_or_admin(current_user, reg_obj)
        if not reg and not await require_admin(current_user):
            raise HTTPException(status_code=403, detail='Not authorized')
    else:
        # payment not tied to a registration - only admin may view
        await require_admin(current_user)

    # Normalize amount back to cents for clients
    amount_minor = int(round((pay.get('amount') or 0) * 100))

    resp = {
        "payment_id": str(pay.get('_id')),
        "status": pay.get('status'),
        "amount_cents": amount_minor,
        "currency": pay.get('currency') or 'EUR',
        "provider": pay.get('provider'),
        "created_at": pay.get('created_at'),
        "paid_at": pay.get('paid_at'),
    }

    # Provider-specific source details
    provider = (pay.get('provider') or '').lower()
    meta = pay.get('meta') or {}
    if provider == 'paypal':
        resp['source'] = {
            'order_id': pay.get('provider_payment_id'),
            'approval_link': pay.get('payment_link'),
            'create_order': meta.get('create_order') if isinstance(meta, dict) else meta,
        }
    elif provider == 'stripe':
        resp['source'] = {
            'session_id': pay.get('provider_payment_id'),
            'session_url': pay.get('payment_link'),
            'meta': meta,
        }
    elif provider == 'wero':
        resp['source'] = {
            'instructions': (meta or {}).get('instructions'),
        }
    else:
        resp['source'] = {'meta': meta}

    return resp


@router.get('/paypal/return')
async def paypal_return(payment_id: str, token: Optional[str] = None):
    """Return URL for PayPal. Captures the order and marks payment as paid if completed."""
    try:
        oid = ObjectId(payment_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail='Invalid payment id') from exc
    pay = await db_mod.db.payments.find_one({"_id": oid})
    if not pay:
        raise HTTPException(status_code=404, detail='Payment not found')
    order_id = token or pay.get('provider_payment_id')
    if not order_id:
        raise HTTPException(status_code=400, detail='Missing PayPal order id')
    # capture
    capture = await paypal_provider.capture_order(order_id)
    status = (capture.get('status') or '').upper()
    now = datetime.datetime.now(datetime.timezone.utc)
    if status == 'COMPLETED':
        log.info('paypal.return.completed payment_id=%s order_id=%s', payment_id, order_id)
        await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "succeeded", "paid_at": now, "meta.capture": capture}})
        await finalize_registration_payment(pay.get('registration_id'), pay.get('_id'))
        return {"status": "paid"}
    log.warning('paypal.return.failed payment_id=%s order_id=%s status=%s', payment_id, order_id, status)
    await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "failed", "meta.capture": capture}})
    return {"status": "failed"}


@router.post('/{payment_id}/capture')
async def capture_payment(payment_id: str, payload: CapturePaymentIn, current_user=Depends(get_current_user)):
    """Capture a payment for a given provider.

    For PayPal the frontend can POST here after the buyer approved the order
    (or the server can capture via the /paypal/return flow). The payload may
    include 'order_id' for PayPal. The endpoint verifies and marks the
    payment/registration as paid on success.
    """
    try:
        oid = ObjectId(payment_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail='Invalid payment id') from exc
    pay = await db_mod.db.payments.find_one({"_id": oid})
    if not pay:
        raise HTTPException(status_code=404, detail='Payment not found')
    provider = (payload.provider or pay.get('provider') or '').lower()
    if provider == 'paypal':
        order_id = payload.order_id or pay.get('provider_payment_id')
        if not order_id:
            raise HTTPException(status_code=400, detail='Missing PayPal order id')
        capture = await paypal_provider.capture_order(order_id)
        status = (capture.get('status') or '').upper()
        now = datetime.datetime.now(datetime.timezone.utc)
        if status == 'COMPLETED':
            log.info('payment.capture.paypal.completed payment_id=%s order_id=%s', payment_id, order_id)
            await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "succeeded", "paid_at": now, "meta.capture": capture}})
            await finalize_registration_payment(pay.get('registration_id'), pay.get('_id'))
            return {"status": "paid"}
        log.warning('payment.capture.paypal.failed payment_id=%s order_id=%s status=%s', payment_id, order_id, status)
        await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "failed", "meta.capture": capture}})
        return {"status": "failed", "detail": capture}
    elif provider == 'stripe':
        # Stripe should use webhooks to confirm payment. For completeness, we
        # accept a manual confirmation here if provider_payment_id is provided
        # and the caller is admin.
        session_id = pay.get('provider_payment_id')
        if not session_id:
            raise HTTPException(status_code=400, detail='Missing Stripe session id')
        # Only allow admin-triggered manual confirms for Stripe via this route
        if not await require_admin(current_user):
            raise HTTPException(status_code=403, detail='Admin required to manually confirm Stripe payments')
        try:
            session = stripe_provider.retrieve_checkout_session(session_id)
        except ValueError as exc:  # missing session id
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:  # Stripe not configured
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - Stripe SDK errors
            raise HTTPException(status_code=502, detail=f'Stripe error: {str(exc)}') from exc
        session_dict = session.to_dict() if hasattr(session, 'to_dict') else session
        payment_status = session_dict.get('payment_status') if isinstance(session_dict, dict) else getattr(session, 'payment_status', None)
        session_status = session_dict.get('status') if isinstance(session_dict, dict) else getattr(session, 'status', None)
        if payment_status != 'paid':
            detail = f'Stripe session not paid (status={session_status}, payment_status={payment_status})'
            raise HTTPException(status_code=409, detail=detail)
        now = datetime.datetime.now(datetime.timezone.utc)
        confirmation_meta = {
            "provider": "stripe",
            "confirmed_at": now,
            "session_id": session_id,
            "session_status": session_status,
            "payment_status": payment_status,
        }
        if isinstance(session_dict, dict):
            confirmation_meta["amount_total"] = session_dict.get('amount_total')
            confirmation_meta["currency"] = session_dict.get('currency')
        await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "succeeded", "paid_at": now, "meta.manual_confirmation": confirmation_meta}})
        log.info('payment.capture.stripe.manual payment_id=%s', payment_id)
        await finalize_registration_payment(pay.get('registration_id'), pay.get('_id'))
        return {"status": "paid"}
    elif provider == 'wero':
        raise HTTPException(status_code=400, detail='Use manual confirmation endpoint for WERO')
    else:
        raise HTTPException(status_code=400, detail='Unsupported provider')


@router.post('/webhooks/stripe')
async def stripe_webhook(request: Request, stripe_signature: str | None = Header(None)):
    """Receiver for Stripe webhooks. Verifies signature if STRIPE_WEBHOOK_SECRET is set.

    Processes 'checkout.session.completed' events and marks the corresponding payment as paid.
    Idempotency: ignores events already processed by checking provider_payment_id -> status.
    """
    payload = await request.body()
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    
    # Check if we're in production environment
    is_production = os.getenv('ENVIRONMENT', '').lower() in ('production', 'prod') or \
                   os.getenv('STRIPE_API_KEY', '').startswith('sk_live_')
    
    if webhook_secret:
        import stripe
        try:
            event = stripe.Webhook.construct_event(payload, stripe_signature, webhook_secret)
        except Exception as e:  # keep broad due to stripe lib
            # Fail-closed: reject when signature verification fails
            log.warning('webhook.stripe.invalid_signature detail=%s', str(e))
            raise HTTPException(status_code=400, detail=f'Invalid signature: {str(e)}') from e
    else:
        # In production, webhook signature validation is mandatory for security
        if is_production:
            raise HTTPException(
                status_code=400, 
                detail='Webhook signature validation required in production. Configure STRIPE_WEBHOOK_SECRET.'
            )
        # Development only: trust the payload without signature validation
        import json
        try:
            event = json.loads(payload)
        except Exception as exc:  # malformed JSON
            raise HTTPException(status_code=400, detail='Invalid payload') from exc

    # handle checkout.session.completed
    typ = event.get('type') if isinstance(event, dict) else getattr(event, 'type', None)
    data = event.get('data', {}).get('object') if isinstance(event, dict) else getattr(event, 'data', {}).get('object')
    if typ == 'checkout.session.completed' and data:
        session_id = data.get('id')
        log.info('webhook.stripe.session_completed session_id=%s', session_id)
        # find payment by provider_payment_id
        pay = await db_mod.db.payments.find_one({"provider_payment_id": session_id})
        if not pay:
            # fallback: try to extract our payment DB id from session metadata
            meta_payment_id = None
            if isinstance(data, dict):
                meta_payment_id = (data.get('metadata') or {}).get('payment_db_id')
            else:
                meta_payment_id = getattr(getattr(data, 'metadata', None), 'get', lambda k, d=None: None)('payment_db_id')
            if meta_payment_id:
                try:
                    meta_obj = ObjectId(meta_payment_id)
                    pay = await db_mod.db.payments.find_one({"_id": meta_obj})
                except (InvalidId, PyMongoError):
                    pay = None
            if not pay:
                # still not found; ignore
                return {"status": "not_found"}
        # Replay protection: record received event id in webhook_events (provider+event)
        event_id = event.get('id') if isinstance(event, dict) else getattr(event, 'id', None)
        if event_id:
            try:
                await db_mod.db.webhook_events.insert_one({"provider": "stripe", "event_id": event_id, "received_at": datetime.datetime.now(datetime.timezone.utc)})
            except Exception:
                # Duplicate key -> already processed
                log.info('webhook.stripe.duplicate event_id=%s', event_id)
                return {"status": "already_processed"}

        if pay.get('status') in ('succeeded', 'paid'):
            return {"status": "already_processed"}
        now = datetime.datetime.now(datetime.timezone.utc)
        await db_mod.db.payments.update_one({"_id": pay.get('_id')}, {"$set": {"status": "succeeded", "paid_at": now}})
        log.info('webhook.stripe.payment.succeeded session_id=%s payment_id=%s', session_id, pay.get('_id'))
        await finalize_registration_payment(pay.get('registration_id'), pay.get('_id'))
        return {"status": "processed"}
    return {"status": "ignored"}

@router.get('/admin/events/{event_id}/refunds', dependencies=[Depends(require_admin)])
async def list_refunds(event_id: str):
    """List registrations cancelled and eligible for refund for an event.

    A registration is considered refundable if:
    - Event has refund_on_cancellation true
    - Registration has field refund_flag == True (set by cancellation logic)
    Returns: { "event_id": ..., "currency": "EUR", "items": [ { registration_id, user_email, amount_cents } ], "total_cents": int }
    """
    try:
        ev_id = ObjectId(event_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail='invalid event_id') from exc
    ev = await db_mod.db.events.find_one({'_id': ev_id})
    if not ev:
        raise HTTPException(status_code=404, detail='Event not found')
    if not ev.get('refund_on_cancellation'):
        return {"event_id": event_id, "currency": (ev.get('currency') or 'EUR'), "items": [], "total_cents": 0}
    fee_cents = int(ev.get('fee_cents') or 0)
    items = []
    total = 0
    async for reg in db_mod.db.registrations.find({'event_id': ev_id, 'refund_flag': True}):
        amount = fee_cents * int(reg.get('team_size') or 1)
        items.append({
            'registration_id': str(reg.get('_id')),
            'user_email': reg.get('user_email_snapshot'),
            'amount_cents': amount,
        })
        total += amount
    return {"event_id": event_id, "currency": (ev.get('currency') or 'EUR'), "items": items, "total_cents": total}


@router.post('/webhooks/paypal')
async def paypal_webhook(request: Request):
    """Minimal PayPal webhook handler. In dev, we trust the payload; in prod, consider verifying via Webhook Verify API.

    Handles PAYMENT.CAPTURE.COMPLETED and CHECKOUT.ORDER.APPROVED/COMPLETED.
    """
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail='Invalid payload') from exc
    
    # Check if we're in production environment
    is_production = os.getenv('ENVIRONMENT', '').lower() in ('production', 'prod') or \
                   os.getenv('PAYPAL_CLIENT_ID', '').find('live') != -1
    
    # Optional signature verification if PAYPAL_WEBHOOK_ID present
    webhook_id = os.getenv('PAYPAL_WEBHOOK_ID')
    if webhook_id:
        transmission_id = request.headers.get('Paypal-Transmission-Id') or request.headers.get('PayPal-Transmission-Id')
        transmission_time = request.headers.get('Paypal-Transmission-Time') or request.headers.get('PayPal-Transmission-Time')
        cert_url = request.headers.get('Paypal-Cert-Url') or request.headers.get('PayPal-Cert-Url')
        auth_algo = request.headers.get('Paypal-Auth-Algo') or request.headers.get('PayPal-Auth-Algo')
        transmission_sig = request.headers.get('Paypal-Transmission-Sig') or request.headers.get('PayPal-Transmission-Sig')
        if all([transmission_id, transmission_time, cert_url, auth_algo, transmission_sig]):
            try:
                ok = await paypal_provider.verify_webhook_signature(
                    webhook_id=webhook_id,
                    transmission_id=transmission_id,
                    transmission_time=transmission_time,
                    cert_url=cert_url,
                    auth_algo=auth_algo,
                    transmission_sig=transmission_sig,
                    event_body=body,
                )
            except Exception as e:
                log.exception('paypal webhook signature verification failed: %s', str(e))
                ok = False
            if not ok:
                log.warning('webhook.paypal.invalid_signature transmission_id=%s', transmission_id)
                raise HTTPException(status_code=400, detail='Invalid PayPal webhook signature')
        else:
            # Missing required headers for signature verification
            if is_production:
                raise HTTPException(
                    status_code=400, 
                    detail='Missing PayPal webhook signature headers in production'
                )
    else:
        # In production, webhook signature validation is mandatory for security
        if is_production:
            raise HTTPException(
                status_code=400, 
                detail='Webhook signature validation required in production. Configure PAYPAL_WEBHOOK_ID.'
            )
    typ = body.get('event_type') or body.get('type')
    resource = body.get('resource') or {}
    order_id = resource.get('id') or resource.get('supplementary_data', {}).get('related_ids', {}).get('order_id')
    if not order_id:
        return {"status": "ignored"}
    # try to find payment by provider_payment_id
    pay = await db_mod.db.payments.find_one({"provider_payment_id": order_id})
    if not pay:
        return {"status": "not_found"}

    # Replay protection: use PayPal's event id if present
    event_id = body.get('id') or None
    if event_id:
        try:
            await db_mod.db.webhook_events.insert_one({"provider": "paypal", "event_id": event_id, "received_at": datetime.datetime.now(datetime.timezone.utc)})
        except Exception:
            log.info('webhook.paypal.duplicate event_id=%s', event_id)
            return {"status": "ok"}

    if typ in ('PAYMENT.CAPTURE.COMPLETED', 'CHECKOUT.ORDER.COMPLETED'):
        if pay.get('status') in ('succeeded', 'paid'):
            return {"status": "ok"}
        now = datetime.datetime.now(datetime.timezone.utc)
        await db_mod.db.payments.update_one({"_id": pay['_id']}, {"$set": {"status": "succeeded", "paid_at": now, "meta.webhook": body}})
        log.info('webhook.paypal.payment.succeeded order_id=%s payment_id=%s', order_id, pay.get('_id'))
        await finalize_registration_payment(pay.get('registration_id'), pay.get('_id'))
        return {"status": "ok"}
    return {"status": "ignored"}


@router.post('/{payment_id}/confirm')
async def confirm_manual_payment(payment_id: str, _current_user=Depends(require_admin)):
    """Manually confirm a bank transfer (Wero) payment. Admins only.

    Marks the payment as succeeded and the registration as paid.
    """
    try:
        oid = ObjectId(payment_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail='Invalid payment id') from exc
    pay = await db_mod.db.payments.find_one({"_id": oid})
    if not pay:
        raise HTTPException(status_code=404, detail='Payment not found')
    now = datetime.datetime.now(datetime.timezone.utc)
    await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "succeeded", "paid_at": now}})
    log.info('payment.confirm.manual payment_id=%s provider=%s', payment_id, pay.get('provider'))
    await finalize_registration_payment(pay.get('registration_id'), pay.get('_id'))
    return {"status": "paid"}



