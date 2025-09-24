from fastapi import APIRouter, HTTPException, Header, Request, Depends
from pydantic import BaseModel
import os, datetime, logging
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
)
from app.payments_providers import paypal as paypal_provider
from app.payments_providers import stripe as stripe_provider
from app.payments_providers import wero as wero_provider

######### Router / Endpoints #########

router = APIRouter()
log = logging.getLogger('payments')

class CreatePaymentIn(BaseModel):
    registration_id: str
    amount_cents: int  # client supplies minor units (cents)
    idempotency_key: str | None = None
    provider: Optional[str] = None  # 'paypal' | 'wero' | 'stripe' (default based on env)
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
    client_id = os.getenv('PAYPAL_CLIENT_ID')
    if not client_id:
        raise HTTPException(status_code=400, detail='PayPal not configured')
    currency = (os.getenv('PAYMENT_CURRENCY') or 'EUR').upper()
    env = (os.getenv('PAYPAL_MODE') or os.getenv('PAYPAL_ENV') or 'sandbox').lower()
    return {"clientId": client_id, "currency": currency, "env": env}


@router.post('/paypal/orders')
async def paypal_create_order(payload: CreatePaymentIn, current_user=Depends(get_current_user)):
    """Create a PayPal Order for Standard Checkout and return the order id.

    This endpoint mirrors /payments/create for provider=paypal but returns an
    order id instead of an approval link, so it can be used with PayPal JS SDK
    (Standard Checkout buttons) per PayPal documentation.
    """
    # Validate registration and permissions
    try:
        reg_obj = ObjectId(payload.registration_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail='Invalid registration_id') from exc
    reg = await require_registration_owner_or_admin(current_user, reg_obj)
    if not reg:
        raise HTTPException(status_code=404, detail='Registration not found')

    # Load event and validate window
    ev = None
    try:
        ev = await db_mod.db.events.find_one({"_id": reg.get('event_id')}) if reg and reg.get('event_id') else None
    except PyMongoError:
        ev = None
    if ev:
        await require_event_published(ev.get('_id'))
        require_event_payment_open(ev)
    event_fee_cents = int((ev or {}).get('fee_cents') or 0)
    # multiply by team_size (default 1)
    team_size = int((reg or {}).get('team_size') or 1)
    canonical_amount_cents = event_fee_cents * max(team_size, 1)
    if canonical_amount_cents <= 0:
        return {"status": "no_payment_required", "amount_cents": 0}
    if payload.amount_cents and payload.amount_cents != canonical_amount_cents:
        raise HTTPException(status_code=400, detail='Amount must match event fee configured by organizer')

    # Idempotency: return existing order for this registration if present
    existing = await db_mod.db.payments.find_one({"registration_id": reg_obj, "provider": "paypal"})
    if existing and existing.get('provider_payment_id'):
        log.debug('paypal.order.idempotent registration_id=%s payment_id=%s order_id=%s', payload.registration_id, existing.get('_id'), existing.get('provider_payment_id'))
        return {"id": existing.get('provider_payment_id')}

    # Upsert payment doc
    initial_doc = {
        "registration_id": reg_obj,
        "amount": canonical_amount_cents / 100.0,
        "currency": (payload.currency or 'EUR').upper(),
        "status": "pending",
        "provider": "paypal",
        "idempotency_key": payload.idempotency_key,
        "meta": {},
        "created_at": datetime.datetime.utcnow(),
    }
    doc = await db_mod.db.payments.find_one_and_update(
        {"registration_id": reg_obj},
        {"$setOnInsert": initial_doc},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    payment_id = doc.get('_id')

    # Create PayPal order
    log.info('payment.create.paypal_order registration_id=%s payment_id=%s amount_cents=%s', payload.registration_id, payment_id, canonical_amount_cents)
    order = await paypal_provider.create_order(canonical_amount_cents, payload.currency or 'EUR', payment_id)
    order_id = order.get('id')
    approval = order.get('approval_link')
    await db_mod.db.payments.update_one({"_id": payment_id}, {"$set": {"provider_payment_id": order_id, "payment_link": approval, "meta.create_order": order}})
    try:
        await db_mod.db.registrations.update_one({"_id": reg_obj}, {"$set": {"payment_id": payment_id}})
    except PyMongoError:
        pass
    log.info('payment.create.paypal_order.ok registration_id=%s payment_id=%s order_id=%s', payload.registration_id, payment_id, order_id)
    return {"id": order_id}


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
    now = datetime.datetime.utcnow()
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


@router.post('/create')
async def create_payment(payload: CreatePaymentIn, current_user=Depends(get_current_user)):
    """Create a payment record and return a payment link.

    The user chooses a provider (paypal, stripe or wero). For PayPal we create
    an order and return the approval link. Stripe uses Checkout Sessions and
    Wero returns bank transfer instructions. The frontend should call the
    provider flow and then notify the backend (or use webhooks) to confirm
    payment validity which updates the DB.
    """
    # ensure registration exists
    try:
        reg_obj = ObjectId(payload.registration_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail='Invalid registration_id') from exc

    # Enforce owner-or-admin for payment creation
    reg = await require_registration_owner_or_admin(current_user, reg_obj)
    if not reg:
        raise HTTPException(status_code=404, detail='Registration not found')

    # idempotency: if a payment with same idempotency_key exists, return it
    if payload.idempotency_key:
        existing = await db_mod.db.payments.find_one({"idempotency_key": payload.idempotency_key})
        if existing:
            log.debug('payment.create.idempotent key=%s payment_id=%s', payload.idempotency_key, existing.get('_id'))
            return {"payment_id": str(existing.get('_id')), "payment_link": existing.get('payment_link'), "status": existing.get('status')}

    provider = (payload.provider or '').lower().strip()
    # default provider if not given
    if not provider:
        paypal_configured = os.getenv('PAYPAL_CLIENT_ID') and (os.getenv('PAYPAL_CLIENT_SECRET') or os.getenv('PAYPAL_SECRET'))
        provider = 'paypal' if paypal_configured else ('stripe' if os.getenv('STRIPE_API_KEY') else 'wero')

    # Derive amount from the event settings (admin-controlled). Use event.fee_cents as source of truth.
    # Load the registration to get event_id (we already loaded `reg` above).
    ev = None
    try:
        ev = await db_mod.db.events.find_one({"_id": reg.get('event_id')}) if reg and reg.get('event_id') else None
    except PyMongoError:
        ev = None
    # Do not allow payment creation for events that aren't published
    if ev:
        await require_event_published(ev.get('_id'))
        # Ensure payment window is still open
        require_event_payment_open(ev)
    event_fee_cents = int((ev or {}).get('fee_cents') or 0)
    team_size = int((reg or {}).get('team_size') or 1)
    canonical_amount_cents = event_fee_cents * max(team_size, 1)
    if canonical_amount_cents <= 0:
        # No payment required for this registration/event
        return {"status": "no_payment_required", "amount_cents": 0}

    # If client passed an explicit amount, ensure it matches the admin-configured fee
    if payload.amount_cents and payload.amount_cents != canonical_amount_cents:
        raise HTTPException(status_code=400, detail='Amount must match event fee configured by organizer')
    # canonical_amount_cents computed above includes team_size

    # Handle PayPal
    if provider == 'paypal':
        # idempotency by registration
        existing_by_reg = await db_mod.db.payments.find_one({"registration_id": reg_obj})
        if existing_by_reg:
            log.debug('payment.create.paypal.existing registration_id=%s payment_id=%s', payload.registration_id, existing_by_reg.get('_id'))
            return {"payment_id": str(existing_by_reg.get('_id')), "payment_link": existing_by_reg.get('payment_link'), "status": existing_by_reg.get('status')}

        initial_doc = {
            "registration_id": reg_obj,
            "amount": canonical_amount_cents / 100.0,
            "currency": (payload.currency or 'EUR').upper(),
            "status": "pending",
            "provider": "paypal",
            "idempotency_key": payload.idempotency_key,
            "meta": {},
            "created_at": datetime.datetime.utcnow(),
        }
        doc = await db_mod.db.payments.find_one_and_update(
            {"registration_id": reg_obj},
            {"$setOnInsert": initial_doc},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        payment_id = doc.get('_id')
        # if already created and has approval link, return
        if doc.get('provider_payment_id') and doc.get('payment_link'):
            return {"payment_id": str(payment_id), "payment_link": doc.get('payment_link'), "status": doc.get('status')}

        # Create PayPal order
        log.info('payment.create paypal registration_id=%s payment_id=%s amount_cents=%s', payload.registration_id, payment_id, canonical_amount_cents)
        order = await paypal_provider.create_order(canonical_amount_cents, payload.currency or 'EUR', payment_id)
        approval = order.get('approval_link')
        order_id = order.get('id')
        await db_mod.db.payments.update_one({"_id": payment_id}, {"$set": {"provider_payment_id": order_id, "payment_link": approval, "meta": {"create_order": order}}})
        try:
            await db_mod.db.registrations.update_one({"_id": reg_obj}, {"$set": {"payment_id": payment_id}})
        except PyMongoError:
            pass
        log.info('payment.create.paypal.ok payment_id=%s order_id=%s', payment_id, order_id)
        return {"payment_id": str(payment_id), "payment_link": approval, "status": "pending"}

    # Handle Stripe (existing)
    stripe_key = os.getenv('STRIPE_API_KEY')
    if provider == 'stripe' and stripe_key:
        # if a payment already exists for this registration, return it
        existing_by_reg = await db_mod.db.payments.find_one({"registration_id": reg_obj})
        if existing_by_reg:
            log.debug('payment.create.stripe.existing registration_id=%s payment_id=%s', payload.registration_id, existing_by_reg.get('_id'))
            return {"payment_id": str(existing_by_reg.get('_id')), "payment_link": existing_by_reg.get('payment_link'), "status": existing_by_reg.get('status')}
        # also respect idempotency key if provided
        if payload.idempotency_key:
            existing_by_idem = await db_mod.db.payments.find_one({"idempotency_key": payload.idempotency_key})
            if existing_by_idem:
                return {"payment_id": str(existing_by_idem.get('_id')), "payment_link": existing_by_idem.get('payment_link'), "status": existing_by_idem.get('status')}

        # create or return an existing payment doc atomically using upsert to avoid races
        initial_doc = {
            "registration_id": reg_obj,
            "amount": canonical_amount_cents / 100.0,
            "currency": 'EUR',
            "status": "pending",
            "provider": "stripe",
            "idempotency_key": payload.idempotency_key,
            "meta": {},
            "created_at": datetime.datetime.utcnow(),
        }
        doc = await db_mod.db.payments.find_one_and_update(
            {"registration_id": reg_obj},
            {"$setOnInsert": initial_doc},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        payment_id = doc.get('_id')
        # if provider details already present return immediately
        if doc.get('provider_payment_id') and doc.get('payment_link'):
            return {"payment_id": str(payment_id), "payment_link": doc.get('payment_link'), "status": doc.get('status')}

        # create stripe checkout session via provider helper
        try:
            session = stripe_provider.create_checkout_session(canonical_amount_cents, payment_id, payload.idempotency_key)
        except Exception as e:  # keeping broad due to provider SDK variability
            try:
                await db_mod.db.payments.delete_one({"_id": payment_id, "provider_payment_id": {"$exists": False}})
            except PyMongoError:
                pass
            raise HTTPException(status_code=500, detail=f'Stripe error: {str(e)}') from e

        # update payment with provider details
        await db_mod.db.payments.update_one({"_id": payment_id}, {"$set": {"provider_payment_id": session.get('id'), "payment_link": session.get('url')}})
        log.info('payment.create.stripe.ok payment_id=%s session_id=%s', payment_id, session.get('id'))
        try:
            await db_mod.db.registrations.update_one({"_id": reg_obj}, {"$set": {"payment_id": payment_id}})
        except PyMongoError:
            pass
        return {"payment_id": str(payment_id), "payment_link": session.get('url'), "status": "pending"}

    # WERO: provide bank transfer instructions (EPC QR)
    if provider == 'wero':
        amount_cents = canonical_amount_cents
        # Upsert payment doc for WERO (bank transfer)
        wero_doc = {
            "registration_id": reg_obj,
            "amount": amount_cents / 100.0,
            "currency": (payload.currency or 'EUR').upper(),
            "status": "pending",
            "provider": "wero",
            "idempotency_key": payload.idempotency_key,
            "meta": {},
            "created_at": datetime.datetime.utcnow(),
        }
        doc = await db_mod.db.payments.find_one_and_update(
            {"registration_id": reg_obj},
            {"$setOnInsert": wero_doc},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        payment_id = doc.get('_id')
        # Build payment instructions via provider helper
        instructions = wero_provider.build_instructions(payment_id, amount_cents, payload.currency or 'EUR')
        log.info('payment.create.wero.ok payment_id=%s amount_cents=%s', payment_id, amount_cents)
        await db_mod.db.payments.update_one({"_id": payment_id}, {"$set": {"meta.instructions": instructions}})
        try:
            await db_mod.db.registrations.update_one({"_id": reg_obj}, {"$set": {"payment_id": payment_id}})
        except PyMongoError:
            pass
        return {"payment_id": str(payment_id), "status": "pending", "instructions": instructions}

    # If provider is not supported or not configured, reject request. All
    # supported providers are handled above (paypal, stripe, wero).
    raise HTTPException(status_code=400, detail='Unsupported payment provider or provider not configured')


# Removed dev-only pay endpoint: all providers are real (paypal/stripe/wero).


@router.get('/{payment_id}/cancel')
async def payment_cancel(payment_id: str):
    """Generic cancel landing endpoint for providers. Marks payment as failed (non-destructive)."""
    try:
        oid = ObjectId(payment_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail='Invalid payment id') from exc
    p = await db_mod.db.payments.find_one({"_id": oid})
    if not p:
        raise HTTPException(status_code=404, detail='Payment not found')
    await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "failed", "updated_at": datetime.datetime.utcnow()}})
    log.info('payment.cancel payment_id=%s', payment_id)
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
        # reuse existing helper to validate owner or admin
        reg = await require_registration_owner_or_admin(current_user, reg_obj)
        if not reg:
            # require_registration_owner_or_admin may return None for not found
            # but if it returns None and the caller isn't admin, forbid
            if not await require_admin(current_user):
                raise HTTPException(status_code=403, detail='Not authorized')
    else:
        # payment not tied to a registration - only admin may view
        if not await require_admin(current_user):
            raise HTTPException(status_code=403, detail='Not authorized')

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
    now = datetime.datetime.utcnow()
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
        now = datetime.datetime.utcnow()
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
        if not pay.get('provider_payment_id'):
            raise HTTPException(status_code=400, detail='Missing Stripe session id')
        # Only allow admin-triggered manual confirms for Stripe via this route
        if not await require_admin(current_user):
            raise HTTPException(status_code=403, detail='Admin required to manually confirm Stripe payments')
        now = datetime.datetime.utcnow()
        await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "succeeded", "paid_at": now}})
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
    if webhook_secret:
        import stripe
        try:
            event = stripe.Webhook.construct_event(payload, stripe_signature, webhook_secret)
        except Exception as e:  # keep broad due to stripe lib
            raise HTTPException(status_code=400, detail=f'Invalid signature: {str(e)}') from e
    else:
        # If no webhook secret, trust the payload (dev only). Parse as JSON.
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
        if pay.get('status') in ('succeeded', 'paid'):
            return {"status": "already_processed"}
        now = datetime.datetime.utcnow()
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
            except Exception:
                ok = False
            if not ok:
                raise HTTPException(status_code=400, detail='Invalid PayPal webhook signature')
    typ = body.get('event_type') or body.get('type')
    resource = body.get('resource') or {}
    order_id = resource.get('id') or resource.get('supplementary_data', {}).get('related_ids', {}).get('order_id')
    if not order_id:
        return {"status": "ignored"}
    # try to find payment by provider_payment_id
    pay = await db_mod.db.payments.find_one({"provider_payment_id": order_id})
    if not pay:
        return {"status": "not_found"}
    if typ in ('PAYMENT.CAPTURE.COMPLETED', 'CHECKOUT.ORDER.COMPLETED'):
        if pay.get('status') in ('succeeded', 'paid'):
            return {"status": "ok"}
        now = datetime.datetime.utcnow()
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
    now = datetime.datetime.utcnow()
    await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "succeeded", "paid_at": now}})
    log.info('payment.confirm.manual payment_id=%s provider=%s', payment_id, pay.get('provider'))
    await finalize_registration_payment(pay.get('registration_id'), pay.get('_id'))
    return {"status": "paid"}


@router.get('/providers')
async def list_providers():
    """List available payment providers based on environment configuration and defaults."""
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
