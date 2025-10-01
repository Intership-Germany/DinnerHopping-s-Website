"""MongoDB connection management and index creation."""
import os
import logging
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl, quote

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
            # Support simple comparison operators and $expr for tests
            def _eval_expr(expr):
                # Evaluate a minimal set of expression operators used in the codebase
                # Supports: $lte, $gte, $add, $ifNull and literal / field refs like '$capacity'
                if isinstance(expr, (int, float, str)) and not (isinstance(expr, str) and expr.startswith('$')):
                    return expr
                if isinstance(expr, str) and expr.startswith('$'):
                    return doc.get(expr[1:])
                if isinstance(expr, dict):
                    if '$ifNull' in expr:
                        args = expr['$ifNull']
                        val = _eval_expr(args[0])
                        return val if val is not None else _eval_expr(args[1])
                    if '$add' in expr:
                        total = 0
                        for part in expr['$add']:
                            v = _eval_expr(part)
                            try:
                                total += (v or 0)
                            except Exception:
                                total += 0
                        return total
                    # Comparison operators return boolean
                    if '$lte' in expr:
                        left, right = expr['$lte']
                        return (_eval_expr(left) or 0) <= (_eval_expr(right) or 0)
                    if '$gte' in expr:
                        left, right = expr['$gte']
                        return (_eval_expr(left) or 0) >= (_eval_expr(right) or 0)
                # Unknown expression; fallback to None
                return None

            # Top-level special-case: $expr
            if '$expr' in filt:
                try:
                    return bool(_eval_expr(filt['$expr']))
                except Exception:
                    return False

            for k, v in filt.items():
                # Support simple operator dicts like {'attendee_count': {'$gte': 1}}
                if isinstance(v, dict):
                    if '$in' in v:
                        if doc.get(k) not in v['$in']:
                            return False
                    elif '$gte' in v:
                        if (doc.get(k) or 0) < v['$gte']:
                            return False
                    elif '$gt' in v:
                        if (doc.get(k) or 0) <= v['$gt']:
                            return False
                    elif '$lte' in v:
                        if (doc.get(k) or 0) > v['$lte']:
                            return False
                    elif '$lt' in v:
                        if (doc.get(k) or 0) >= v['$lt']:
                            return False
                    elif '$ne' in v:
                        if doc.get(k) == v['$ne']:
                            return False
                    else:
                        # Unknown operator dict: conservative mismatch
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

        async def find_one_and_update(self, filt: dict, update: dict, upsert: bool = False, return_document: ReturnDocument = ReturnDocument.BEFORE):
            for idx, d in enumerate(self._store):
                if self._match(d, filt):
                    original = d.copy()
                    if '$set' in update:
                        d.update(update['$set'])
                    if '$unset' in update:
                        for key in update['$unset'].keys():
                            d.pop(key, None)
                    if return_document == ReturnDocument.AFTER:
                        return d.copy()
                    return original
            if not upsert:
                return None
            new_doc = {}
            set_on_insert = update.get('$setOnInsert') if isinstance(update, dict) else None
            if isinstance(set_on_insert, dict):
                new_doc.update(set_on_insert)
            for key, value in (filt or {}).items():
                if isinstance(value, dict):
                    continue
                new_doc.setdefault(key, value)
            if '_id' not in new_doc:
                new_doc['_id'] = ObjectId()
            self._store.append(new_doc)
            if '$set' in update:
                new_doc.update(update['$set'])
            if return_document == ReturnDocument.AFTER:
                return new_doc.copy()
            return None

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

        async def delete_one(self, filt: dict):
            """Remove a single matching document and return an object with deleted_count."""
            for idx, d in enumerate(self._store):
                if self._match(d, filt):
                    del self._store[idx]
                    return types.SimpleNamespace(deleted_count=1)
            return types.SimpleNamespace(deleted_count=0)

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
            base_url = os.getenv('MONGO_URI') or os.getenv('MONGO_URL') or 'mongodb://mongo:27017/dinnerhopping'
            db_name = os.getenv('MONGO_DB', 'dinnerhopping')
            user = os.getenv('MONGO_USER')
            pwd = os.getenv('MONGO_PASSWORD')
            # Normalize quotes if values are provided as 'value' or "value"
            def _strip_quotes(s: str | None) -> str | None:
                if not s:
                    return s
                if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
                    return s[1:-1]
                return s
            user = _strip_quotes(user)
            pwd = _strip_quotes(pwd)
            # If user/pass provided separately and URI lacks credentials, inject them and set authSource
            if user and '@' not in base_url:
                p = urlparse(base_url)
                # ensure path has DB name
                path = p.path if p.path and p.path != '/' else f'/{db_name}'
                # rebuild netloc with credentials
                netloc = f"{quote(user)}:{quote(pwd or '')}@{p.hostname or 'localhost'}"
                if p.port:
                    netloc += f":{p.port}"
                # merge query params and set authSource if missing
                q = dict(parse_qsl(p.query, keep_blank_values=True))
                if 'authSource' not in q:
                    q['authSource'] = os.getenv('MONGO_AUTH_SOURCE', path.lstrip('/'))
                built_url = urlunparse((p.scheme or 'mongodb', netloc, path, '', urlencode(q), ''))
                mongo_url = built_url
            else:
                mongo_url = base_url
            self.client = AsyncIOMotorClient(mongo_url)
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
            try:
                await self.db.payments.create_index(
                    'idempotency_key',
                    unique=True,
                    sparse=True,
                    name='payments_idempotency_key_unique',
                )
            except PyMongoError:
                pass
                # WEBHOOK EVENTS - track processed provider events to avoid replay
                try:
                    await self.db.webhook_events.create_index([
                        ('provider', 1),
                        ('event_id', 1),
                    ], unique=True, name='webhook_events_provider_event_unique')
                except PyMongoError:
                    pass
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
