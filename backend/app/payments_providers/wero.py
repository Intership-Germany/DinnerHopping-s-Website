import os
from typing import Dict, Any


def build_instructions(payment_id, amount_cents: int, currency: str = 'EUR') -> Dict[str, Any]:
    iban = os.getenv('WERO_IBAN', 'DE02120300000000202051')
    bic = os.getenv('WERO_BIC', 'BYLADEM1001')
    name = os.getenv('WERO_BENEFICIARY', 'DinnerHopping')
    rem_prefix = os.getenv('WERO_PURPOSE_PREFIX', 'DH')
    remittance = f"{rem_prefix}-{str(payment_id)[-8:].upper()}"
    amount = f"{amount_cents/100:.2f}"
    currency = (currency or 'EUR').upper()
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
