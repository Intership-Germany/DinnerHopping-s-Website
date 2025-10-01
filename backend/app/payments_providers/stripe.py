import os
from typing import Dict, Any


def create_checkout_session(amount_cents: int, payment_id, idempotency_key: str | None = None) -> Dict[str, Any]:
    stripe_key = os.getenv('STRIPE_API_KEY')
    if not stripe_key:
        raise RuntimeError('Stripe not configured')
    import stripe
    stripe.api_key = stripe_key
    session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[{"price_data": {"currency": "eur", "product_data": {"name": "Event registration"}, "unit_amount": amount_cents}, "quantity": 1}],
        mode='payment',
        success_url=os.getenv('BACKEND_BASE_URL', 'http://localhost:8000') + f'/payments/{payment_id}/success',
        cancel_url=os.getenv('BACKEND_BASE_URL', 'http://localhost:8000') + f'/payments/{payment_id}/cancel',
        metadata={
            "payment_db_id": str(payment_id),
            "idempotency_key": idempotency_key or '',
        },
    )
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
