import os
from typing import Dict, Any


def create_checkout_session(
    amount_cents: int,
    payment_id,
    idempotency_key: str | None = None,
    *,
    payer_name: str | None = None,
    registration_type: str | None = None,
) -> Dict[str, Any]:
    """Create a Stripe Checkout Session.

    New optional keyword-only args:
    - payer_name: display name to include in the product description/metadata
    - registration_type: string like 'team' or 'solo' to include in metadata
    """
    stripe_key = os.getenv('STRIPE_API_KEY')
    if not stripe_key:
        raise RuntimeError('Stripe not configured')
    import stripe
    stripe.api_key = stripe_key

    # Build common kwargs for session creation
    # Prefer FRONTEND_BASE_URL for user-facing redirects (Stripe Checkout expects public URLs),
    # fall back to BACKEND_BASE_URL if frontend base not configured.
    frontend_base = os.getenv('FRONTEND_BASE_URL') or os.getenv('BACKEND_BASE_URL') or 'http://localhost:8000'
    product_name = 'Event registration'
    if payer_name:
        # include payer name to make the checkout clearer for the user
        product_name = f"Event registration — {payer_name}"
    session_metadata = {
        "payment_db_id": str(payment_id),
        "idempotency_key": idempotency_key or '',
    }
    if registration_type:
        session_metadata['registration_type'] = registration_type

    session_kwargs = dict(
        payment_method_types=['card'],
        line_items=[
            {
                "price_data": {
                    "currency": "eur",
                    "product_data": {"name": product_name},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }
        ],
        mode='payment',
        success_url=frontend_base.rstrip('/') + f'/payement?payment_id={payment_id}',
        cancel_url=frontend_base.rstrip('/') + f'/payement?payment_id={payment_id}&status=cancelled',
        metadata=session_metadata,
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
