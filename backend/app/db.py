import os
from motor.motor_asyncio import AsyncIOMotorClient

client: AsyncIOMotorClient | None = None
db = None

async def connect_to_mongo():
    global client, db
    mongo_uri = os.getenv('MONGO_URI', 'mongodb://localhost:27017/dinnerhopping')
    client = AsyncIOMotorClient(mongo_uri)
    db = client.get_default_database()
    # create some useful indexes to enforce uniqueness and speed lookups
    try:
        # users: unique email
        await db.users.create_index('email', unique=True)
        # registrations: lookups by event and by user
        await db.registrations.create_index('event_id')
        await db.registrations.create_index('user_email')
        # plans: lookup by user
        await db.plans.create_index('user_email')
        # events: date queries and unique event id to avoid duplicate imports
        await db.events.create_index('date')
        await db.events.create_index('event_id', unique=True)

        # invitations, payments, matches indexes
        await db.invitations.create_index('token', unique=True)
        await db.invitations.create_index('invited_email')
        await db.payments.create_index('registration_id', unique=True)
        # provider_payment_id is the id returned by an external provider (stripe session id)
        await db.payments.create_index('provider_payment_id', unique=True, sparse=True)
        # idempotency key to deduplicate payment creation attempts
        await db.payments.create_index('idempotency_key')
        await db.matches.create_index('event_id')
    except Exception:
        # index creation shouldn't crash startup; log and continue
        pass

    print('Connected to MongoDB')

async def close_mongo():
    global client
    if client:
        client.close()
        print('Closed MongoDB connection')
