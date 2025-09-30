"""Helpers to produce EPC-bank-transfer instructions for the Wero provider."""

import os
from typing import Dict, Any


def build_instructions(payment_id, amount_cents: int, currency: str = 'EUR') -> Dict[str, Any]:
    """Generate wire-transfer details for the given payment.

    The values default to environment configuration so operators can change IBAN,
    BIC or beneficiary without touching the code. The purpose prefix is used to
    build a unique remittance reference that the finance team can reconcile.
    """
    # Pull configuration from environment variables. Defaults keep backward
    # compatibility for local development while encouraging explicit overrides.
    iban = os.getenv('WERO_IBAN', 'DE02120300000000202051')
    bic = os.getenv('WERO_BIC', 'BYLADEM1001')
    name = os.getenv('WERO_BENEFICIARY', 'DinnerHopping')
    rem_prefix = os.getenv('WERO_PURPOSE_PREFIX', 'DH')
    # The remittance reference encodes the payment id suffix to aid matching.
    remittance = f"{rem_prefix}-{str(payment_id)[-8:].upper()}"
    amount = f"{amount_cents/100:.2f}"
    currency = (currency or 'EUR').upper()
    # Build a standard EPC QR payload so the frontend can render a QR code.
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
        "",
    ])
    # Return the data payload expected by the payment router/clients.
    instructions = {
        "iban": iban,
        "bic": bic,
        "beneficiary": name,
        "amount": amount,
        "currency": currency,
        "remittance": remittance,
        "epc_qr_payload": epc,
    }
    return instructions
