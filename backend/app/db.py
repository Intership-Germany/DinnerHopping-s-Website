"""MongoDB connection management and index creation."""
import os
import logging
from pymongo.errors import PyMongoError
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId

# ---------------- In-memory Fake DB (test mode) -----------------
if os.getenv('USE_FAKE_DB_FOR_TESTS'):
    import types
    import asyncio

    class _InsertOneResult:
        def __init__(self, inserted_id):
            self.inserted_id = inserted_id

    class _UpdateResult:
        def __init__(self, matched, modified):
            self.matched_count = matched
            self.modified_count = modified

    class FakeCollection:
        def __init__(self, name, store):
            self._name = name
            self._store = store  # list of dicts

        async def create_index(self, *args, **kwargs):  # no-op
            return None

        def _match(self, doc, filt):
            if not filt:
                return True
            for k, v in filt.items():
                if isinstance(v, dict) and '$in' in v:
                    if doc.get(k) not in v['$in']:
                        return False
                else:
                    if doc.get(k) != v:
                        return False
            return True

        async def find_one(self, filt: dict | None = None, projection=None):
            for d in self._store:
                if self._match(d, filt or {}):
                    return d.copy()
            return None

        async def insert_one(self, doc: dict):
            if '_id' not in doc:
                doc['_id'] = ObjectId()
            self._store.append(doc)
            return _InsertOneResult(doc['_id'])

        async def update_one(self, filt: dict, update: dict):
            modified = 0
            for d in self._store:
                if self._match(d, filt):
                    if '$set' in update:
                        d.update(update['$set'])
                    if '$unset' in update:
                        for k in update['$unset'].keys():
                            d.pop(k, None)
                    modified += 1
                    break
            return _UpdateResult(int(modified > 0), modified)

        async def delete_many(self, filt: dict):
            before = len(self._store)
            self._store[:] = [d for d in self._store if not self._match(d, filt)]
            return types.SimpleNamespace(deleted_count=before - len(self._store))

        def find(self, filt: dict | None = None, projection=None):
            filt = filt or {}
            matches = [d.copy() for d in self._store if self._match(d, filt)]

            class _Cursor:
                def __init__(self, docs):
                    self._docs = docs
                def __aiter__(self):
                    self._iter = iter(self._docs)
                    return self
                async def __anext__(self):
                    try:
                        return next(self._iter)
                    except StopIteration:
                        raise StopAsyncIteration
            return _Cursor(matches)

    class FakeDB:
        def __init__(self):
            self._collections = {}
        def __getattr__(self, item):
            if item.startswith('_'):
                raise AttributeError(item)
            if item not in self._collections:
                self._collections[item] = FakeCollection(item, [])
            return self._collections[item]

    # Pre-create commonly used collections for clarity (optional)
    _fake_db = FakeDB()


class MongoDB:
    """MongoDB connection manager."""
    def __init__(self):
        self.client: AsyncIOMotorClient | None = None
        self.db = None

    async def connect(self):
        """Connect to MongoDB and create necessary indexes."""
        if os.getenv('USE_FAKE_DB_FOR_TESTS'):
            # swap in fake DB and skip network
            self.client = None
            self.db = _fake_db  # type: ignore
            globals()['db'] = self.db
            return
        # establish real client/db if not already
        if not self.client:
            # Prefer explicit full URI if provided
            mongo_url = os.getenv('MONGO_URI') or os.getenv('MONGO_URL')

            # If a URI is provided but missing credentials, and creds exist in env, augment the URI.
            if mongo_url:
                try:
                    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote

                    parts = urlsplit(mongo_url)
                    # Only modify mongodb scheme URLs
                    if parts.scheme.startswith('mongodb') and '@' not in parts.netloc:
                        user = os.getenv('MONGO_USER') or os.getenv('MONGO_INITDB_ROOT_USERNAME')
                        password = os.getenv('MONGO_PASSWORD') or os.getenv('MONGO_INITDB_ROOT_PASSWORD')
                        if user and password:
                            userinfo = f"{quote(user)}:{quote(password)}@"
                            netloc = userinfo + parts.netloc
                            # Ensure authSource present (default admin when using root creds)
                            q = dict(parse_qsl(parts.query, keep_blank_values=True))
                            q.setdefault('authSource', 'admin')
                            mongo_url = urlunsplit((parts.scheme, netloc, parts.path, urlencode(q), parts.fragment))
                except Exception as _:
                    # Best-effort; if parsing fails, continue with given URI
                    pass

            # If no full URI, try to construct one from host/port and optional credentials.
            if not mongo_url:
                host = os.getenv('MONGO_HOST', 'mongo')
                port = os.getenv('MONGO_PORT', '27017')
                db_name = os.getenv('MONGO_DB', 'dinnerhopping')

                # Credentials: prefer explicit MONGO_USER/MONGO_PASSWORD, then the docker-style MONGO_INITDB_ROOT_* vars
                user = os.getenv('MONGO_USER') or os.getenv('MONGO_INITDB_ROOT_USERNAME')
                password = os.getenv('MONGO_PASSWORD') or os.getenv('MONGO_INITDB_ROOT_PASSWORD')

                if user and password:
                    # include authSource=admin for root user credentials in Docker images
                    mongo_url = f'mongodb://{user}:{password}@{host}:{port}/{db_name}?authSource=admin'
                else:
                    mongo_url = f'mongodb://{host}:{port}/{db_name}'

            # create client and select DB
            self.client = AsyncIOMotorClient(mongo_url)
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
