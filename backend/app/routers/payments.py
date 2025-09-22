from fastapi import APIRouter, HTTPException, Header, Request, Depends
from pydantic import BaseModel
import os
from app import db as db_mod
from bson.objectid import ObjectId
from pymongo import ReturnDocument
import datetime
from typing import Optional, Dict, Any
import base64

from app.auth import get_current_user, require_admin
from app.utils import require_event_published, require_registration_owner_or_admin, require_event_payment_open

######### Router / Endpoints #########

router = APIRouter()

class CreatePaymentIn(BaseModel):
    registration_id: str
    amount_cents: int  # client supplies minor units (cents)
    idempotency_key: str | None = None
    provider: Optional[str] = None  # 'paypal' | 'wero' | 'stripe' (default based on env)
    currency: Optional[str] = 'EUR'


class CapturePaymentIn(BaseModel):
    provider: Optional[str] = None
    order_id: Optional[str] = None


def _paypal_base() -> str:
    # support both PAYPAL_MODE and legacy PAYPAL_ENV naming
    env = (os.getenv('PAYPAL_MODE') or os.getenv('PAYPAL_ENV') or 'sandbox').lower()
    if env == 'live':
        return 'https://api-m.paypal.com'
    return 'https://api-m.sandbox.paypal.com'


def _import_httpx():
    import importlib
    try:
        return importlib.import_module('httpx')
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail='httpx is required for PayPal integration') from exc


async def _paypal_get_access_token() -> str:
    """Obtain a PayPal OAuth2 access token using client credentials."""
    client_id = os.getenv('PAYPAL_CLIENT_ID')
    # support either PAYPAL_CLIENT_SECRET or PAYPAL_SECRET env var names
    client_secret = os.getenv('PAYPAL_CLIENT_SECRET') or os.getenv('PAYPAL_SECRET')
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail='PayPal not configured: set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET (or PAYPAL_SECRET)')
    httpx = _import_httpx()
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{_paypal_base()}/v1/oauth2/token",
            headers={'Authorization': f'Basic {auth}'},
            data={'grant_type': 'client_credentials'},
        )
        if resp.status_code >= 300:
            raise HTTPException(status_code=502, detail=f'PayPal token error: {resp.text[:200]}')
        data = resp.json()
        return data.get('access_token')


async def _paypal_create_order(amount_cents: int, currency: str, paympent_id: ObjectId) -> Dict[str, Any]:
    """Create a PayPal order and return JSON with id and approval link."""
    httpx = _import_httpx()
    token = await _paypal_get_access_token()
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
        raise HTTPException(status_code=502, detail=f'PayPal order error: {resp.text[:200]}')
    data = resp.json()
    approval_link = None
    for l in data.get('links', []) or []:
        if l.get('rel') == 'approve':
            approval_link = l.get('href')
            break
    return {'id': data.get('id'), 'approval_link': approval_link, 'raw': data}


async def _paypal_capture_order(order_id: str) -> Dict[str, Any]:
    httpx = _import_httpx()
    token = await _paypal_get_access_token()
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{_paypal_base()}/v2/checkout/orders/{order_id}/capture",
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        )
    if resp.status_code >= 300:
        raise HTTPException(status_code=502, detail=f'PayPal capture error: {resp.text[:200]}')
    return resp.json()


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
    except Exception as exc:  # noqa: BLE001 - validate ObjectId format only
        raise HTTPException(status_code=400, detail='Invalid registration_id') from exc

    # Enforce owner-or-admin for payment creation
    reg = await require_registration_owner_or_admin(current_user, reg_obj)
    if not reg:
        raise HTTPException(status_code=404, detail='Registration not found')

    # idempotency: if a payment with same idempotency_key exists, return it
    if payload.idempotency_key:
        existing = await db_mod.db.payments.find_one({"idempotency_key": payload.idempotency_key})
        if existing:
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
    except Exception:
        ev = None
    # Do not allow payment creation for events that aren't published
    if ev:
        await require_event_published(ev.get('_id'))
        # Ensure payment window is still open
        require_event_payment_open(ev)
    event_fee_cents = int((ev or {}).get('fee_cents') or 0)
    if event_fee_cents <= 0:
        # No payment required for this registration/event
        return {"status": "no_payment_required", "amount_cents": 0}

    # If client passed an explicit amount, ensure it matches the admin-configured fee
    if payload.amount_cents and payload.amount_cents != event_fee_cents:
        raise HTTPException(status_code=400, detail='Amount must match event fee configured by organizer')

    # Use event_fee_cents as the canonical amount for all providers
    canonical_amount_cents = event_fee_cents

    # Handle PayPal
    if provider == 'paypal':
        # idempotency by registration
        existing_by_reg = await db_mod.db.payments.find_one({"registration_id": reg_obj})
        if existing_by_reg:
            return {"payment_id": str(existing_by_reg.get('_id')), "payment_link": existing_by_reg.get('payment_link'), "status": existing_by_reg.get('status')}

        amount_cents = canonical_amount_cents

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
        order = await _paypal_create_order(canonical_amount_cents, payload.currency or 'EUR', payment_id)
        approval = order.get('approval_link')
        order_id = order.get('id')
        await db_mod.db.payments.update_one({"_id": payment_id}, {"$set": {"provider_payment_id": order_id, "payment_link": approval, "meta": {"create_order": order}}})
        try:
            await db_mod.db.registrations.update_one({"_id": reg_obj}, {"$set": {"payment_id": payment_id}})
        except Exception:
            pass
        return {"payment_id": str(payment_id), "payment_link": approval, "status": "pending"}

    # Handle Stripe (existing)
    stripe_key = os.getenv('STRIPE_API_KEY')
    if provider == 'stripe' and stripe_key:
        # if a payment already exists for this registration, return it
        existing_by_reg = await db_mod.db.payments.find_one({"registration_id": reg_obj})
        if existing_by_reg:
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

        # lazy import to avoid hard dependency in environments without stripe
        import stripe
        stripe.api_key = stripe_key
        try:
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{"price_data": {"currency": "eur", "product_data": {"name": "Event registration"}, "unit_amount": canonical_amount_cents}, "quantity": 1}],
                mode='payment',
                success_url=os.getenv('BACKEND_BASE_URL', 'http://localhost:8000') + f'/payments/{payment_id}/success',
                cancel_url=os.getenv('BACKEND_BASE_URL', 'http://localhost:8000') + f'/payments/{payment_id}/cancel',
                metadata={
                    "payment_db_id": str(payment_id),
                    "idempotency_key": payload.idempotency_key or '',
                },
            )
        except Exception as e:  # noqa: BLE001 - aggregate stripe errors
            try:
                await db_mod.db.payments.delete_one({"_id": payment_id, "provider_payment_id": {"$exists": False}})
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(status_code=500, detail=f'Stripe error: {str(e)}')

        # update payment with provider details
        await db_mod.db.payments.update_one({"_id": payment_id}, {"$set": {"provider_payment_id": session.id, "payment_link": session.url}})
        try:
            await db_mod.db.registrations.update_one({"_id": reg_obj}, {"$set": {"payment_id": payment_id}})
        except Exception:
            pass
        return {"payment_id": str(payment_id), "payment_link": session.url, "status": "pending"}

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
        # Build payment instructions
        iban = os.getenv('WERO_IBAN', 'DE02120300000000202051')
        bic = os.getenv('WERO_BIC', 'BYLADEM1001')
        name = os.getenv('WERO_BENEFICIARY', 'DinnerHopping')
        rem_prefix = os.getenv('WERO_PURPOSE_PREFIX', 'DH')
        remittance = f"{rem_prefix}-{str(payment_id)[-8:].upper()}"
        amount = f"{amount_cents/100:.2f}"
        currency = (payload.currency or 'EUR').upper()
        # EPC QR payload (basic)
        epc = "\n".join([
            "BCD",
            "001",
            "1",
            "SCT",
            bic,
            name,
            iban,
            f"EUR{amount}",
            "",
            remittance,
            ""
        ])
        instructions = {
            "iban": iban,
            "bic": bic,
            "beneficiary": name,
            "amount": amount,
            "currency": currency,
            "remittance": remittance,
            "epc_qr_payload": epc,
        }
        await db_mod.db.payments.update_one({"_id": payment_id}, {"$set": {"meta.instructions": instructions}})
        try:
            await db_mod.db.registrations.update_one({"_id": reg_obj}, {"$set": {"payment_id": payment_id}})
        except Exception:
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
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail='Invalid payment id')
    p = await db_mod.db.payments.find_one({"_id": oid})
    if not p:
        raise HTTPException(status_code=404, detail='Payment not found')
    await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "failed", "updated_at": datetime.datetime.utcnow()}})
    return {"status": "cancelled"}


@router.get('/{payment_id}/success')
async def payment_success(payment_id: str):
    """Generic success landing endpoint for providers that redirect. Does not mark paid by itself (Stripe uses webhook)."""
    try:
        oid = ObjectId(payment_id)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail='Invalid payment id')
    p = await db_mod.db.payments.find_one({"_id": oid})
    if not p:
        raise HTTPException(status_code=404, detail='Payment not found')
    return {"status": p.get('status')}


@router.get('/paypal/return')
async def paypal_return(payment_id: str, token: Optional[str] = None):
    """Return URL for PayPal. Captures the order and marks payment as paid if completed."""
    try:
        oid = ObjectId(payment_id)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail='Invalid payment id')
    pay = await db_mod.db.payments.find_one({"_id": oid})
    if not pay:
        raise HTTPException(status_code=404, detail='Payment not found')
    order_id = token or pay.get('provider_payment_id')
    if not order_id:
        raise HTTPException(status_code=400, detail='Missing PayPal order id')
    # capture
    capture = await _paypal_capture_order(order_id)
    status = (capture.get('status') or '').upper()
    now = datetime.datetime.utcnow()
    if status == 'COMPLETED':
        await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "succeeded", "paid_at": now, "meta.capture": capture}})
        await db_mod.db.registrations.update_one({"_id": pay.get('registration_id')}, {"$set": {"status": "paid", "paid_at": now, "updated_at": now}})
        return {"status": "paid"}
    else:
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
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail='Invalid payment id')
    pay = await db_mod.db.payments.find_one({"_id": oid})
    if not pay:
        raise HTTPException(status_code=404, detail='Payment not found')
    provider = (payload.provider or pay.get('provider') or '').lower()
    if provider == 'paypal':
        order_id = payload.order_id or pay.get('provider_payment_id')
        if not order_id:
            raise HTTPException(status_code=400, detail='Missing PayPal order id')
        capture = await _paypal_capture_order(order_id)
        status = (capture.get('status') or '').upper()
        now = datetime.datetime.utcnow()
        if status == 'COMPLETED':
            await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "succeeded", "paid_at": now, "meta.capture": capture}})
            await db_mod.db.registrations.update_one({"_id": pay.get('registration_id')}, {"$set": {"status": "paid", "paid_at": now, "updated_at": now}})
            return {"status": "paid"}
        else:
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
        await db_mod.db.registrations.update_one({"_id": pay.get('registration_id')}, {"$set": {"status": "paid", "paid_at": now, "updated_at": now}})
        return {"status": "paid"}
    elif provider == 'wero':
        # Wero manual confirmation stays admin-only via /{payment_id}/confirm
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
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f'Invalid signature: {str(e)}')
    else:
        # If no webhook secret, trust the payload (dev only). Parse as JSON.
        import json
        try:
            event = json.loads(payload)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail='Invalid payload')

    # handle checkout.session.completed
    typ = event.get('type') if isinstance(event, dict) else getattr(event, 'type', None)
    data = event.get('data', {}).get('object') if isinstance(event, dict) else getattr(event, 'data', {}).get('object')
    if typ == 'checkout.session.completed' and data:
        session_id = data.get('id')
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
                except Exception:  # noqa: BLE001
                    pay = None
            if not pay:
                # still not found; ignore
                return {"status": "not_found"}
        if pay.get('status') in ('succeeded', 'paid'):
            return {"status": "already_processed"}
        # mark as paid
        now = datetime.datetime.utcnow()
        await db_mod.db.payments.update_one({"_id": pay.get('_id')}, {"$set": {"status": "succeeded", "paid_at": now}})
        await db_mod.db.registrations.update_one({"_id": pay.get('registration_id')}, {"$set": {"status": "paid", "paid_at": now, "updated_at": now}})
        return {"status": "ok"}

    return {"status": "ignored"}


@router.post('/webhooks/paypal')
async def paypal_webhook(request: Request):
    """Minimal PayPal webhook handler. In dev, we trust the payload; in prod, consider verifying via Webhook Verify API.

    Handles PAYMENT.CAPTURE.COMPLETED and CHECKOUT.ORDER.APPROVED/COMPLETED.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail='Invalid payload')
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
        await db_mod.db.registrations.update_one({"_id": pay.get('registration_id')}, {"$set": {"status": "paid", "paid_at": now, "updated_at": now}})
        return {"status": "ok"}
    return {"status": "ignored"}


@router.post('/{payment_id}/confirm')
async def confirm_manual_payment(payment_id: str, current_user=Depends(require_admin)):
    """Manually confirm a bank transfer (Wero) payment. Admins only.

    Marks the payment as succeeded and the registration as paid.
    """
    try:
        oid = ObjectId(payment_id)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail='Invalid payment id')
    pay = await db_mod.db.payments.find_one({"_id": oid})
    if not pay:
        raise HTTPException(status_code=404, detail='Payment not found')
    now = datetime.datetime.utcnow()
    await db_mod.db.payments.update_one({"_id": oid}, {"$set": {"status": "succeeded", "paid_at": now}})
    await db_mod.db.registrations.update_one({"_id": pay.get('registration_id')}, {"$set": {"status": "paid", "paid_at": now, "updated_at": now}})
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
