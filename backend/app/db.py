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
            mongo_url = os.getenv('MONGO_URL', 'mongodb://mongo:27017')
            self.client = AsyncIOMotorClient(mongo_url)
            # default database name
            db_name = os.getenv('MONGO_DB', 'dinnerhopping')
            self.db = self.client[db_name]
            # update the module-level `db` variable so other modules can
            # reference `db_mod.db.<collection>` (e.g. `db_mod.db.users`).
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
            # geospatial location index (GeoJSON Point)
            try:
                await self.db.events.create_index([('location.point', '2dsphere')])
            except Exception:
                pass  # ignore if invalid existing docs

            # REGISTRATIONS
            await self.db.registrations.create_index('event_id')
            await self.db.registrations.create_index('user_id')
            await self.db.registrations.create_index('status')
            await self.db.registrations.create_index('user_email_snapshot')

            # INVITATIONS
            await self.db.invitations.create_index('token', unique=True)
            await self.db.invitations.create_index('registration_id')
            await self.db.invitations.create_index('invited_email')
            await self.db.invitations.create_index('expires_at')

            # PAYMENTS
            await self.db.payments.create_index('registration_id', unique=True)
            await self.db.payments.create_index('provider_payment_id', unique=True, sparse=True)
            await self.db.payments.create_index('idempotency_key')
            await self.db.payments.create_index('status')

            # MATCHES
            await self.db.matches.create_index('event_id')
            await self.db.matches.create_index('status')
            await self.db.matches.create_index('version')

            # PLANS (existing feature - per user per event)
            await self.db.plans.create_index('user_email')
            await self.db.plans.create_index('event_id')
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
