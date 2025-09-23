"""MongoDB connection management and index creation."""
import os
import logging
from pymongo.errors import PyMongoError
from motor.motor_asyncio import AsyncIOMotorClient

class MongoDB:
    """MongoDB connection manager."""
    def __init__(self):
        self.client: AsyncIOMotorClient | None = None
        self.db = None

    async def connect(self):
        """Connect to MongoDB and create necessary indexes."""
        # establish client/db if not already
        if not self.client:
            # Prefer MONGO_URI if provided (common env name), fallback to MONGO_URL, then default
            mongo_url = os.getenv('MONGO_URI') or os.getenv('MONGO_URL') or 'mongodb://mongo:27017/dinnerhopping'
            self.client = AsyncIOMotorClient(mongo_url)
            # default database name
            db_name = os.getenv('MONGO_DB', 'dinnerhopping')
            self.db = self.client[db_name]
            globals()['db'] = self.db

        # create some useful indexes to enforce uniqueness and speed lookups
        try:
            # USERS
            await self.db.users.create_index('email', unique=True)
            await self.db.users.create_index('email_verified')  # boolean lookup
            await self.db.users.create_index('deleted_at')       # soft delete filter

            # EVENTS
            # event organizer lookups & status/date filters
            await self.db.events.create_index('organizer_id')
            await self.db.events.create_index('status')
            await self.db.events.create_index('date')
            await self.db.events.create_index('registration_deadline')
            await self.db.events.create_index('payment_deadline')
            await self.db.events.create_index('fee_cents')
            await self.db.events.create_index('matching_status')
            # geospatial location index (GeoJSON Point)
            # create geospatial index best-effort; ignore validation issues from legacy docs
            try:
                await self.db.events.create_index([('location.point', '2dsphere')])
            except PyMongoError:
                pass

            # REGISTRATIONS
            await self.db.registrations.create_index('event_id')
            await self.db.registrations.create_index('user_id')
            await self.db.registrations.create_index('status')
            await self.db.registrations.create_index('user_email_snapshot')
            await self.db.registrations.create_index([('event_id', 1), ('user_id', 1)], unique=True, sparse=True)

            # INVITATIONS
            # store only a non-reversible token_hash in invitations
            await self.db.invitations.create_index('token_hash', unique=True)
            await self.db.invitations.create_index('registration_id')
            await self.db.invitations.create_index('invited_email')
            await self.db.invitations.create_index('expires_at')

            # EMAIL VERIFICATIONS (TTL index based on expires_at)
            await self.db.email_verifications.create_index('token_hash', unique=True)
            await self.db.email_verifications.create_index('email')
            await self.db.email_verifications.create_index('expires_at', expireAfterSeconds=0)

            # PASSWORD RESETS (TTL on expires_at)
            await self.db.password_resets.create_index('token_hash', unique=True)
            await self.db.password_resets.create_index('email')
            await self.db.password_resets.create_index('expires_at', expireAfterSeconds=0)

            # PAYMENTS
            await self.db.payments.create_index('registration_id', unique=True)
            await self.db.payments.create_index('provider_payment_id', unique=True, sparse=True)
            await self.db.payments.create_index('idempotency_key')
            await self.db.payments.create_index('status')

            # MATCHES
            await self.db.matches.create_index('event_id')
            await self.db.matches.create_index('status')
            await self.db.matches.create_index('version')
            await self.db.matches.create_index([('event_id', 1), ('version', -1)])

            # PLANS (existing feature - per user per event)
            await self.db.plans.create_index('user_email')
            await self.db.plans.create_index('event_id')
            # REFRESH TOKENS
            await self.db.refresh_tokens.create_index('token_hash', unique=True)
            await self.db.refresh_tokens.create_index('user_email')
            await self.db.refresh_tokens.create_index('expires_at')

            # teams, chats and messages collections
            await self.db.teams.create_index('event_id')
            await self.db.teams.create_index('status')
            await self.db.chat_groups.create_index('event_id')
            await self.db.chat_groups.create_index('created_at')
            await self.db.chat_messages.create_index([('group_id', 1), ('created_at', -1)])

        except PyMongoError as e:
            logging.warning("MongoDB index creation failed; continuing startup: %s", e)

    print('Connected to MongoDB')

    async def close(self):
        if self.client:
            self.client.close()
            print('Closed MongoDB connection')

mongo_db = MongoDB()


async def connect():
    """Module-level connect function used by the application startup event."""
    await mongo_db.connect()


async def close():
    """Module-level close function used by the application shutdown event."""
    await mongo_db.close()


# Expose `db` attribute for modules that import `db_mod.db` and rely on
# `db` containing the Motor database object. It will be set after connect.
def get_db():
    return mongo_db.db

# Helper alias used by code that expects `db_mod.db` as a variable. Some
# modules import `from .. import db as db_mod` and then reference `db_mod.db`.
# Expose a module-level `db` variable that will be set to the Motor
# database object after `connect()` is called. This lets callers do
# `from app import db as db_mod` and then `await db_mod.db.users.find_one(...)`.
db = None
