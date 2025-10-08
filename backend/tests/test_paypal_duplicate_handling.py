import os
import asyncio
import datetime
from bson.objectid import ObjectId

import pytest

os.environ['USE_FAKE_DB_FOR_TESTS'] = '1'

import app.db as db_mod
from app.payments_providers import paypal as paypal_provider


class DuplicateLikeError(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args)
        self.code = 11000


@pytest.mark.asyncio
async def test_ensure_paypal_handles_duplicate(monkeypatch):
    # connect uses the fake DB when USE_FAKE_DB_FOR_TESTS is set
    await db_mod.connect()
    fake_payments = db_mod.db.payments

    registration_oid = ObjectId()
    # pre-insert a payment document to simulate another request winning the race
    now = datetime.datetime.now(datetime.timezone.utc)
    existing = {
        '_id': ObjectId(),
        'registration_id': registration_oid,
        'amount': 10.0,
        'currency': 'EUR',
        'status': 'pending',
        'provider': 'paypal',
        'created_at': now,
    }
    await fake_payments.insert_one(existing)

    # Make find_one_and_update raise a Duplicate-like error to force the code path
    async def raise_dup(*args, **kwargs):
        raise DuplicateLikeError('E11000 duplicate key error')

    monkeypatch.setattr(fake_payments, 'find_one_and_update', raise_dup)

    # Now call ensure_paypal_payment; it should catch the duplicate and return the existing doc
    result = await paypal_provider.ensure_paypal_payment(registration_oid, amount_cents=1000, currency='EUR', idempotency_key='k')
    assert result is not None
    assert result.get('_id') == existing['_id']
    assert result.get('registration_id') == registration_oid
