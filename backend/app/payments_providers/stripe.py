import os
from typing import Dict, Any


def create_checkout_session(amount_cents: int, payment_id, idempotency_key: str | None = None) -> Dict[str, Any]:
    stripe_key = os.getenv('STRIPE_API_KEY')
    if not stripe_key:
        raise RuntimeError('Stripe not configured')
    import stripe
    stripe.api_key = stripe_key

    # Build common kwargs for session creation
    # Use FRONTEND_BASE_URL for user-facing redirects, fallback to BACKEND_BASE_URL
    frontend_base = os.getenv('FRONTEND_BASE_URL') or os.getenv('BACKEND_BASE_URL', 'http://localhost:8000')
    session_kwargs = dict(
        payment_method_types=['card'],
        line_items=[
            {
                "price_data": {
                    "currency": "eur",
                    "product_data": {"name": "Event registration"},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }
        ],
        mode='payment',
        success_url=frontend_base.rstrip('/') + f'/payment-success.html?payment_id={payment_id}',
        cancel_url=frontend_base.rstrip('/') + f'/payment-success.html?payment_id={payment_id}&status=cancelled',
        metadata={
            "payment_db_id": str(payment_id),
            "idempotency_key": idempotency_key or '',
        },
    )

    # If an idempotency key is provided, pass it through to Stripe so provider-side
    # deduplication aligns with our server-normalized key.
    if idempotency_key:
        session = stripe.checkout.Session.create(**session_kwargs, idempotency_key=idempotency_key)
    else:
        session = stripe.checkout.Session.create(**session_kwargs)

    return {"id": session.id, "url": session.url, "raw": session}


def retrieve_checkout_session(session_id: str):
    """Fetch a Stripe Checkout Session using the secret API key."""
    stripe_key = os.getenv('STRIPE_API_KEY')
    if not stripe_key:
        raise RuntimeError('Stripe not configured')
    if not session_id:
        raise ValueError('session_id required')
    import stripe
    stripe.api_key = stripe_key
    return stripe.checkout.Session.retrieve(session_id)
