<div align="center">

# DinnerHopping

Modern social dining & event matching platform – backend (FastAPI + MongoDB) & static frontend.

_Work in Progress • Actively developed_

</div>

## 1. Overview

DinnerHopping lets users register, verify their email, browse & join dinner events, invite partners/teammates, and pay participation fees via Stripe Checkout or PayPal Orders. Admins manage events, review refund eligibility, and (future) launch matching logic to group participants.

Current focus: stable core flows (auth → event creation → registration → payment → cancellation/refund listing) with a clean foundation for deferred features (matching, chat, richer travel logic).

### Key Characteristics
- FastAPI backend with modular routers & settings
- MongoDB persistence (with optional in‑memory fake DB for tests / lightweight dev)
- Payments abstraction (Stripe Checkout, PayPal Orders API)
- Structured logging by category (auth, payments, email, request, etc.) + request IDs
- Simple static frontend (HTML + vanilla JS + Tailwind CDN) consuming the API
- Extensible notification & email system (stdout fallback in dev)

## 2. Architecture at a Glance

```
┌───────────┐   HTTPS / JSON    ┌────────────────┐
│ Frontend  │  <--------------> │ FastAPI Backend│
│ (Static)  │                   │  (app/*)       │
└─────┬─────┘                   └──────┬─────────┘
			│  Static assets (Nginx)        │
			│                                │ Async I/O
			▼                                ▼
	Browser                      MongoDB (collections: users, events, registrations,
																		invitations, payments, tokens, logs (future))
																			│
																			│ Webhooks / API calls
					┌──────────────┬────────────┴────────────┬──────────────┐
					│ Stripe       │ PayPal                  │ SMTP/Email   │
					│ (Checkout)   │ (Orders + Webhooks)     │ (optional)   │
					└──────────────┴─────────────────────────┴──────────────┘
```

Deferred modules (matching, chats) are scaffolded but not active in production flows.

## 3. Tech Stack

| Area | Technology |
|------|------------|
| Backend | Python 3.x, FastAPI, Pydantic, HTTPX (tests), Uvicorn |
| Database | MongoDB (or in‑memory fake implementation) |
| Auth | JWT access tokens (password auth + email verification) |
| Payments | Stripe Checkout, PayPal Orders API |
| Frontend | Static HTML, Vanilla JS, Tailwind via CDN, Nginx container |
| Testing | pytest + HTTP client fixtures |
| Containerization | Docker & docker-compose |
| Logging | Python stdlib logging with category-based rotating files |

## 4. Repository Structure

```
backend/                # FastAPI application
	app/
		routers/            # Modular API routes (users, events, payments, registrations, etc.)
		payments_providers/ # Stripe / PayPal integration modules
		middleware/         # Rate limiting, security headers, request ID
		notifications.py    # Email + notification helpers
		settings.py         # Centralized env-driven configuration
		db.py               # Mongo client factory & fake DB adapter
		auth.py             # Auth & JWT utilities
		schemas.py          # Pydantic models
		utils.py            # Helpers (email send, hashing, encryption, etc.)
	tests/                # Pytest suite (core flow)
	scripts/              # Data / migration / init scripts

frontend/
	public/               # HTML pages, partials, JS helpers
	generate-config.js    # Produces runtime config.js from .env

README.md (this)        # General project documentation
```

## 5. Quick Start (Full Stack via Docker Compose)

From repository root:

```bash
docker compose -f backend/docker-compose.yml up --build
```

This starts:
* Backend at http://localhost:8000 (Swagger UI at /docs)
* MongoDB container (if defined in compose)

To also run the frontend container (if you add it to the compose file) it would expose http://localhost.

### Lightweight Backend Only (In-Memory Fake DB)
```bash
cd backend
USE_FAKE_DB_FOR_TESTS=1 uvicorn app.main:app --reload
```

Data will not persist between restarts.

## 6. Backend Essentials

### Auth Flow
1. POST /register → creates user, prints verification email (dev fallback)
2. GET /verify-email?token=... → marks user verified
3. POST /login → returns `{ access_token }`
4. Authorized routes use Bearer token

### Core Domain Entities
| Entity | Purpose |
|--------|---------|
| User | Account with verification status & roles (admin flag) |
| Event | Public dinner event lifecycle (draft → coming_soon/open → matched/released) |
| Registration | Links user/team to event; status evolves (pending/paid/cancelled) |
| Invitation | Tokenized invite for partner seats / team formation |
| Payment | Provider details (amount, status, provider metadata) |

### Logging
Logs by category under `backend/logs/` (auth, payments, email, webhook, request, root, etc.). Each request is tagged with a UUID request ID.

## 7. Frontend Overview

Static site served by Nginx (Dockerfile in `frontend/`). Uses vanilla JS modules for:
- Auth guard & (future) refresh/CSRF support
- Dynamic header/footer includes (`public/includes.js`)
- Page scripts (`profile.js`, `event-page.js`, etc.)

Environment configuration compiled into `public/config.js` via:
```bash
cd frontend
cp .env.example .env   # edit BACKEND_BASE
node generate-config.js
```
Then serve container or any static server pointing to `public/`.

## 8. Configuration (Environment Variables)

All backend settings centralized in `app/settings.py` (Pydantic). Key variables:

| Category | Variable | Notes |
|----------|----------|-------|
| Core | ENVIRONMENT | Environment name |
| Core | APP_NAME | Display name |
| Auth | JWT_SECRET | Change in production |
| Auth | TOKEN_PEPPER | HMAC pepper for token hashes |
| Security | ENFORCE_HTTPS | Redirect to HTTPS in prod |
| CORS | ALLOWED_ORIGINS | Comma list; set explicit for cookies |
| Mongo | MONGO_URI / MONGO_URL | Connection string |
| Mongo | MONGO_DB | Database name |
| Email | SMTP_HOST / SMTP_* | Enable real email sending |
| Payments | STRIPE_API_KEY | Enables Stripe routes + webhook verification if STRIPE_WEBHOOK_SECRET set |
| Payments | STRIPE_WEBHOOK_SECRET | Verify Stripe webhooks |
| Payments | PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET | PayPal Orders API |
| Payments | PAYPAL_ENV | sandbox or live |
| Payments | PAYPAL_WEBHOOK_ID | Verify PayPal webhook signatures |
| Privacy | ADDRESS_KEY | Base64 AES-GCM key (32 bytes) |
| Testing | USE_FAKE_DB_FOR_TESTS | 1 = in-memory fake DB |
| Dev Cookies | ALLOW_INSECURE_COOKIES | Allow non-Secure cookies locally |

Frontend `.env` (compiled into JS):
| Variable | Example | Meaning |
|----------|---------|---------|
| BACKEND_BASE | http://localhost:8000 | API base URL |

## 9. Payments

### Stripe
- Creates Checkout Sessions with our internal payment ID in metadata
- Webhook (`/payments/webhooks/stripe`) marks payment succeeded on `checkout.session.completed`
- Idempotent: repeated attempts reuse existing payment record

### PayPal (Orders API)
Flow: create order → client approval → capture → update registration.
Key routes:
```
GET  /payments/paypal/config
POST /payments/paypal/orders
POST /payments/paypal/orders/{order_id}/capture
POST /payments/webhooks/paypal
```
Sandbox: set `PAYPAL_ENV=sandbox` and credentials; optional `PAYPAL_WEBHOOK_ID` for signature verification.

> **Note:** The legacy manual bank transfer (“Wero”) provider has been removed. Payments must be processed through Stripe or PayPal. Manual transfers, if needed, should be tracked outside the platform.

### Integrity & Security
- Server calculates authoritative amount from event fee * team size.
- Idempotency key support for create calls.
- Webhook signature verification when secrets/IDs configured.

## 10. Testing

Run core flow tests with in-memory DB:
```bash
cd backend
USE_FAKE_DB_FOR_TESTS=1 pytest -q
```
Adds coverage for: registration → verification → login → event creation → registration → cancellation/refund reporting.

Add new tests in `backend/tests/` using shared fixtures (`conftest.py`).

## 11. Development Tips

- Copy verification & invitation links from backend logs (dev email fallback)
- Use distinct databases per developer by suffixing `MONGO_DB`
- For rapid prototyping payments without Stripe key: dev local flow creates a `GET /payments/{id}/pay` link
- Set `ALLOWED_ORIGINS` explicitly (no wildcard) + `CORS_ALLOW_CREDENTIALS=true` when moving to cookie-based auth

## 12. Security & Hardening Checklist (Prod)

| Area | Action |
|------|--------|
| Secrets | Set strong `JWT_SECRET`, `TOKEN_PEPPER`, `ADDRESS_KEY` |
| HTTPS | Terminate TLS (reverse proxy) + keep `ENFORCE_HTTPS=true` |
| Rate Limiting | Replace in-memory limiter with Redis / gateway |
| Email | Use real SMTP or provider API + DKIM/SPF/DMARC |
| Payments | Configure webhook secrets & verify logs |
| Monitoring | Aggregate logs, alert on error spikes |
| Data | Consider Mongo replica set for transactions (future matching consistency) |
| Backups | Schedule periodic Mongo backups |

## 13. Roadmap

| Feature | Status |
|---------|--------|
| Matching algorithm & group travel | Deferred |
| Chat lifecycle & messaging persistence | Scaffold only |
| Automated provider refunds | Planned |
| Advanced admin dashboards | Planned |
| Rich HTML email templates | Planned |
| Cookie/CSRF auth full migration | In progress |

## 14. Contributing

1. Fork & branch (`feature/short-description`)
2. Ensure tests pass (`pytest -q` with fake DB)
3. Add/update docs when introducing new endpoints or env vars
4. Submit PR with clear description (include screenshots for frontend changes)

Coding style: follow existing FastAPI patterns; keep functions small & explicit. Prefer adding tests for bug fixes / new behavior.

## 15. License

No license file currently present. Add a LICENSE (MIT, Apache-2.0, etc.) before public distribution. Until then this code should be considered “All Rights Reserved.”

## 16. Maintainers

Keep this README updated when adding:
- New payment providers
- New environment variables
- Public endpoints or breaking schema changes

---

Questions / improvements welcome – open an issue or PR.

