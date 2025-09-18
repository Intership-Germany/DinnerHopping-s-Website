from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel
import os
from .. import db as db_mod
from bson.objectid import ObjectId
from pymongo import ReturnDocument
import datetime

router = APIRouter()

class CreatePaymentIn(BaseModel):
    registration_id: str
    amount_cents: int  # client supplies minor units (cents)
    idempotency_key: str | None = None


@router.post('/create')
async def create_payment(payload: CreatePaymentIn):
    """Create a payment record and return a payment link.

    If STRIPE_API_KEY is configured we create a Stripe Checkout Session.
    Otherwise we create a dev-local payment record and return a local pay link
    that marks the payment as paid when visited (dev only).
    """
    # ensure registration exists
    try:
        reg_obj = ObjectId(payload.registration_id)
    except Exception:  # noqa: BLE001 - validate ObjectId format only
        raise HTTPException(status_code=400, detail='Invalid registration_id')

    reg = await db_mod.db.registrations.find_one({"_id": reg_obj})
    if not reg:
        raise HTTPException(status_code=404, detail='Registration not found')

    # idempotency: if a payment with same idempotency_key exists, return it
    if payload.idempotency_key:
        existing = await db_mod.db.payments.find_one({"idempotency_key": payload.idempotency_key})
        if existing:
            return {"payment_id": str(existing.get('_id')), "payment_link": existing.get('payment_link'), "status": existing.get('status')}

    stripe_key = os.getenv('STRIPE_API_KEY')
    if stripe_key:
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
            "amount": payload.amount_cents / 100.0,
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
                line_items=[{"price_data": {"currency": "eur", "product_data": {"name": "Event registration"}, "unit_amount": payload.amount_cents}, "quantity": 1}],
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

    # dev fallback: use atomic upsert to avoid duplicates
    if payload.idempotency_key:
        existing_idem = await db_mod.db.payments.find_one({"idempotency_key": payload.idempotency_key})
        if existing_idem:
            return {"payment_id": str(existing_idem.get('_id')), "payment_link": existing_idem.get('payment_link'), "status": existing_idem.get('status')}

    dev_doc = {
        "registration_id": reg_obj,
        "amount": payload.amount_cents / 100.0,
        "currency": 'EUR',
        "status": "pending",
        "provider": "dev-local",
        "idempotency_key": payload.idempotency_key,
        "meta": {},
        "created_at": datetime.datetime.utcnow(),
    }
    doc = await db_mod.db.payments.find_one_and_update(
        {"registration_id": reg_obj},
        {"$setOnInsert": dev_doc},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    payment_id = doc.get('_id')
    # if payment_link already present just return
    if doc.get('payment_link'):
        return {"payment_id": str(payment_id), "payment_link": doc.get('payment_link'), "status": doc.get('status')}

    pay_link = f"/payments/{str(payment_id)}/pay"
    await db_mod.db.payments.update_one({"_id": payment_id}, {"$set": {"payment_link": pay_link}})
    try:
        await db_mod.db.registrations.update_one({"_id": reg_obj}, {"$set": {"payment_id": payment_id}})
    except Exception:
        pass
    return {"payment_id": str(payment_id), "payment_link": pay_link, "status": "pending"}


@router.get('/{payment_id}/pay')
async def dev_pay(payment_id: str):
    """Dev-only: mark a payment as paid and update the registration. Returns simple HTML."""
    p = await db_mod.db.payments.find_one({"_id": ObjectId(payment_id)})
    if not p:
        raise HTTPException(status_code=404, detail='Payment not found')
    if p.get('status') == 'paid':
        return {"status": "already_paid"}

    # mark paid
    now = datetime.datetime.utcnow()
    await db_mod.db.payments.update_one({"_id": ObjectId(payment_id)}, {"$set": {"status": "succeeded", "paid_at": now}})
    await db_mod.db.registrations.update_one({"_id": p.get('registration_id')}, {"$set": {"status": "paid", "updated_at": now}})
    return {"status": "paid"}


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
        if pay.get('status') in ('paid', 'succeeded'):
            return {"status": "already_processed"}
        # mark as paid
        now = datetime.datetime.utcnow()
        await db_mod.db.payments.update_one({"_id": pay.get('_id')}, {"$set": {"status": "succeeded", "paid_at": now}})
        await db_mod.db.registrations.update_one({"_id": pay.get('registration_id')}, {"$set": {"status": "paid", "updated_at": now}})
        return {"status": "ok"}

    return {"status": "ignored"}
