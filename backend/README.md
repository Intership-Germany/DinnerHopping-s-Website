# DinnerHopping Backend

FastAPI + (MongoDB or in‑memory fake DB) application providing:

* User registration, email verification, login / refresh / logout
* Profile & role (admin) management
* Event lifecycle (draft → coming_soon/open → matched/released, cancellation flag)
* Solo & team registrations, invitations, partner replacement, cancellations with refund flags
* Payments (Stripe Checkout, PayPal Orders API, manual SEPA “Wero”) with idempotent finalization
* Refund reporting endpoint for admins
* Modular notification + verification email system (console fallback)
* Centralized settings module, structured request logging with request IDs, global error schema
* Matching / travel scaffolding (currently deferred – not active in production flow)

This document focuses on running, configuring, and understanding the backend. Frontend integration lives in the `frontend/` folder. Matching features are intentionally paused and excluded from current docs except where referenced in code.

docker compose up --build
## Quick Start (Development)

### Option A: Full stack with MongoDB (recommended for realistic flows)
```bash
cd backend
docker compose up --build
```
Service: http://localhost:8000 (Swagger UI at /docs)

### Option B: Lightweight test mode (no Mongo required)
Uses an in‑memory fake persistence layer for faster iteration & CI.
```bash
USE_FAKE_DB_FOR_TESTS=1 uvicorn app.main:app --reload
```
Data is ephemeral and not shared across processes.

### Emails in Dev
If no SMTP settings are present, verification & notification emails are printed to stdout (`[email dev-fallback]`). Copy verification links directly from logs.

## Configuration & Settings

Centralized in `app/settings.py` using `pydantic-settings`. All env vars have sane defaults for dev.

| Category | Key | Default | Notes |
|----------|-----|---------|-------|
| Core | `ENVIRONMENT` | development | Free form environment name |
| Core | `APP_NAME` | DinnerHopping Backend | Display title |
| Mongo | `MONGO_URI` | mongodb://mongo:27017/dinnerhopping | Overridden by `MONGO_URL` if not set |
| Mongo | `MONGO_DB` | dinnerhopping | DB name |
| Auth | `JWT_SECRET` | change-me | MUST change in prod |
| Auth | `TOKEN_PEPPER` | (empty) | HMAC pepper for stored token hashes |
| Security | `ENFORCE_HTTPS` | true | Adds HTTPS redirect middleware (disable in local http) |
| CORS | `ALLOWED_ORIGINS` | * | Comma list. To use cookies set explicit origins + `CORS_ALLOW_CREDENTIALS=true` |
| Email | `SMTP_HOST` + related | — | If unset, dev print fallback used |
| Payments | `STRIPE_API_KEY` | — | Enables Stripe Checkout when present |
| Payments | `STRIPE_PUBLISHABLE_KEY` | — | Frontend key exposed via `/payments/stripe/config` |
| Payments | `STRIPE_WEBHOOK_SECRET` | — | Signature verification for webhooks |
| Payments | `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` | — | Enables PayPal Orders API |
| Payments | `PAYPAL_ENV` | sandbox | sandbox or live |
| Payments | `PAYPAL_WEBHOOK_ID` | — | (Optional) Enables signature verification for webhooks |
| Manual Pay | `WERO_*` | — | IBAN/BIC beneficiary + purpose prefix |
| Privacy | `ADDRESS_KEY` | — | Base64 AES-GCM key for address encryption |
| Testing | `USE_FAKE_DB_FOR_TESTS` | 0 | Set to 1 to use in-memory DB |

Access settings via:
```python
from app.settings import get_settings
settings = get_settings()
```

Settings are cached, so reading them is inexpensive.

### PayPal Integration (Sandbox & Production)

The payment API uses PayPal's Orders API (v2) in "Server-side create → client approve → server capture" mode.

Required environment variables (sandbox):

```
PAYPAL_ENV=sandbox
PAYPAL_CLIENT_ID=YourSandboxClientId
PAYPAL_CLIENT_SECRET=YourSandboxSecret
# Optional to secure webhooks:
PAYPAL_WEBHOOK_ID=WH-XXX...  # ID returned by PayPal when creating the webhook
```

In production, set `PAYPAL_ENV=live` and replace the credentials with those of the live account.

Main endpoints:
* `GET /payments/paypal/config` → `{ clientId, currency, env }` to load the JS SDK.
* `POST /payments/paypal/orders` → creates a PayPal Order and returns `{ id }` (order id).
* `POST /payments/paypal/orders/{order_id}/capture` → captures the order and marks the registration as paid.
* `POST /payments/webhooks/paypal` → processes `PAYMENT.CAPTURE.COMPLETED` and `CHECKOUT.ORDER.COMPLETED` (signature verified if `PAYPAL_WEBHOOK_ID` is configured).

Standard flow (Buttons JS SDK):
1. Frontend calls `GET /payments/paypal/config` to retrieve `clientId`.
2. Loads the script `https://www.paypal.com/sdk/js?client-id=...&currency=EUR`.
3. `createOrder` (JS callback) calls `POST /payments/paypal/orders` with `registration_id`.
4. The user approves on PayPal.
5. `onApprove` calls `POST /payments/paypal/orders/{orderID}/capture`.
6. Backend updates Payment + Registration (status `succeeded`).

Webhook security:
* Configure a webhook in the PayPal Dashboard (events: `CHECKOUT.ORDER.COMPLETED`, `PAYMENT.CAPTURE.COMPLETED`).
* Provide the public URL: `https://<your-domain>/payments/webhooks/paypal`.
* Copy the webhook ID (`PAYPAL_WEBHOOK_ID`) into the environment.
* The route will verify the signature via `/v1/notifications/verify-webhook-signature`.

Quick Sandbox Test:
1. Create an event with a `fee_cents > 0` and status `open`.
2. Create/validate a user and then register them (`/registrations/solo`).
3. Note the returned `registration_id`.
4. Open `frontend/public/paypal-example.html` (serve the `frontend/public` folder via the container or a static server) and enter the `registration_id`.
5. Pay with a sandbox "Buyer" account. After redirection/approval, the capture should return `{ "status": "COMPLETED" }` and the payment in the database should change to `succeeded`.
6. Verify in the database (Mongo) the `payments` document or call `GET /payments/{payment_id}`.

Notes:
* The amount is not taken from the client: it is recalculated from `event.fee_cents * team_size` and rejected if inconsistent.
* Idempotence: only one PayPal order per registration; repeated attempts return the existing `order_id`.
* To change the currency, set `PAYMENT_CURRENCY` (default `EUR`).
* Captures can be done either via `/payments/paypal/orders/{id}/capture` (JS SDK) or via `GET /payments/paypal/return` (classic redirection flow).
* **Redirect behavior**: After payment completion or cancellation, PayPal redirects users to the frontend (`FRONTEND_BASE_URL/payment-success.html`) instead of returning backend JSON. Set `FRONTEND_BASE_URL` in the environment to specify the frontend URL (defaults to `BACKEND_BASE_URL` if not set).

### Stripe Checkout (Test & Live)

Stripe Checkout relies on server-created sessions and a lightweight frontend redirect. Setup checklist:

1. **Environment variables** — set `STRIPE_API_KEY` and `STRIPE_PUBLISHABLE_KEY`.
	These can live in `backend/app/.env` during development; switch to live keys before production cutover. Optionally set `STRIPE_WEBHOOK_SECRET` to verify webhooks.
2. **Frontend initialization** — call `GET /payments/stripe/config` to retrieve `{ publishableKey, currency, mode }`. Initialize Stripe.js with the publishable key and use the returned Checkout Session URL to redirect users.
3. **Create payments** — send `POST /payments/create` with `provider="stripe"`. The backend stores the payment document, creates a Checkout Session, and responds with a redirect URL. The existing frontend modal already redirects when it receives `payment_link`.
4. **Handle completions** — configure a Stripe webhook (e.g. `checkout.session.completed`) pointing to `/payments/webhooks/stripe`. Provide `STRIPE_WEBHOOK_SECRET` so signatures are validated. The webhook handler marks the payment and registration as succeeded.
	- In sandbox, the current secret used in development is `whsec_` (set this in `STRIPE_WEBHOOK_SECRET`). Rotate it before going live.

Notes:
- The backend auto-selects the default provider based on configured credentials; if PayPal is absent but Stripe keys are present, Stripe will be offered automatically to users.
- `mode` in the config payload is inferred from the key prefixes (`pk_test` / `sk_test`).
- For local testing use https tunnel tooling (ngrok, Cloudflare tunnel) so Stripe can reach your webhook endpoint.
- The admin fallback `POST /payments/{payment_id}/capture` checks the Checkout Session status via the Stripe API before confirming a payment, guaranteeing that “payment completed” only occurs when Stripe reports the session as paid.
- **Redirect behavior**: After payment success or cancellation, Stripe redirects users to the frontend (`FRONTEND_BASE_URL/payment-success.html`) instead of backend endpoints. Set `FRONTEND_BASE_URL` in environment to specify the frontend URL (defaults to `BACKEND_BASE_URL` if not set).

### Wero Manual SEPA Flow

Wero is implemented as a manual SEPA transfer option that emits EPC QR payloads so guests can pay via their banking app. Implementation steps:

1. **Set environment variables** — provide at least `WERO_IBAN`, `WERO_BIC`, `WERO_BENEFICIARY` and `WERO_PURPOSE_PREFIX` (see `deploy/example.env`). The backend falls back to demo values if unset, but production deployments must override them.
2. **Expose the provider in the UI** — call `POST /payments/create` with `provider="wero"`. When PayPal/Stripe are not configured the backend auto-selects Wero, so legacy forms work without changes.
3. **Display instructions to the user** — the response returns `{ instructions: { iban, bic, beneficiary, amount, currency, remittance, epc_qr_payload } }`. Render the IBAN/BIC and optionally convert `epc_qr_payload` into a QR code (any EPC QR generator works).
4. **Confirm incoming transfers** — once the wire hits the bank account, an admin calls `POST /payments/{payment_id}/confirm` (existing endpoint) to mark the payment as `succeeded`. The confirmation flow is identical to other manual transfers in the system.

The helper `app/payments_providers/wero.py` now contains inline comments describing how the remittance reference is generated and how the EPC payload is assembled. Adjusting the env vars is all that is required to switch beneficiaries between environments.


### Email & Notifications
Primitive templates are in `app/notifications.py` and low-level delivery in `app/utils.py` (`send_email`). In absence of SMTP configuration, mail bodies are printed. Add new categories by reusing `send_email(category="your_feature")`.

## Editable Email Templates (New)

Email notifications now support dynamic, database-backed templates editable via the admin UI.

### Storage Model
Collection: `email_templates`
Fields:
- key (string, unique, e.g. `payment_confirmation`)
- subject (string with {{placeholders}})
- html_body (HTML or plaintext with {{placeholders}})
- description (admin help text)
- variables (array[str])
- updated_at (datetime)

Placeholders use the form `{{variable_name}}` and are replaced with simple string values. Nested lookup using dot notation is supported (e.g. `{{user.first_name}}`). Missing variables become an empty string. The current mail sender downgrades HTML to plain text by stripping tags until full multipart support is implemented.

Auto-available variables:
- `email`: recipient email (first recipient when multiple)
- `first_name`, `last_name`, `full_name`: looked up from the recipient user in DB (best-effort)
- `user.*`: a nested map with `email`, `first_name`, `last_name`, `full_name`
- `current_date`, `current_time`, `current_datetime`, `current_year`: timestamps in UTC

You can still override any of these by providing explicit `template_vars` in code. Caller-provided values take precedence over auto-enriched values.

**Automatic Variables**: The following variables are automatically available in all templates:
- `{{current_date}}` - Current date in YYYY-MM-DD format
- `{{current_time}}` - Current time in HH:MM:SS format  
- `{{current_datetime}}` - Full ISO datetime string
- `{{current_year}}` - Current year (e.g., 2024)

### Admin Management Page
A lightweight management interface lives at `frontend/public/admin-email-templates.html` (serve this statically behind admin auth or embed in existing admin dashboard). It lists templates, allows creation, editing and deletion.

API Endpoints (admin role required):
- GET `/admin/email-templates` – list
- GET `/admin/email-templates/{key}` – fetch single
- POST `/admin/email-templates` – create
- PUT `/admin/email-templates/{key}` – update (key immutable)
- DELETE `/admin/email-templates/{key}` – delete

### Fallback Behavior
If a template key is missing, the system falls back to built-in plaintext lines defined in `app/notifications.py`. This ensures no outage if the DB is empty. A seeding script `scripts/seed_email_templates.py` can pre-populate defaults:
```bash
cd backend
python -m scripts.seed_email_templates
```

### Verification Landing Page
Verification emails now link to a frontend landing page: `verify-email.html` (instead of the raw JSON endpoint). The backend still exposes `GET /users/verify-email?token=...` for programmatic flows. Environment variables:
- `FRONTEND_BASE_URL` (optional) – when set, verification links use this origin.

### Adding a New Template
1. Create it via the admin page or POST endpoint with a unique `key`.
2. In code, when sending, pass `template_key='your_key'` and supply variables.
3. Provide a reasonable fallback subject/body in case the template does not exist.

Example (conceptual):
```python
await _send(user_email, 'Default Subject', ['Plain fallback'], 'category', template_key='your_key', variables={'user_email': user_email})
```

### Security Considerations
- No script sanitization is enforced; only admins can edit templates. Avoid embedding untrusted user input directly into templates; variable values are escaped during placeholder substitution before HTML stripping.
- Future improvement: store both HTML and plaintext versions and send multipart emails.

## High-Level API Map

Routers: users (`/register`, `/login`, `/logout`, `/refresh`, profile), events (`/events`), registrations (`/registrations/solo|/team`), invitations, payments, admin, matching (scaffold only), chats (scaffold), plus plan convenience endpoint.

### Authentication
- POST /register
	- Body: { name, email, password }
	- Creates a user, stores an email verification token and prints a verification link to logs.
	- Returns the created user's id and basic profile.

- GET /verify-email?token=<token>
	- Marks the user's email as verified (dev helper; token is one-time).

- POST /login
	- Body: { username: <email>, password: <password> }
	- Requires the user's email to be verified. Returns a JWT access token: { access_token }

- GET /me (or GET /profile)
	- Authorization: Bearer <token>
	- Returns the authenticated user's profile.

### Events
- GET /events
	- Returns a list of public events (supports query filters in the code).

- POST /events/ (admin or protected)
	- Creates an event. Depending on your deployment this route may be admin-only.

- GET /events/{id}
	- Returns event details (location is anonymized by default in list views).

### Registrations & Invitations
- POST /events/{id}/register
	- Registers the authenticated user (or invited user) for an event. Can create invitation
		records when the request includes invited_emails and prints invitation links to logs.

- POST /invitations/{token}/accept
	- Accept an invitation (supports account creation for unauthenticated users).

### Payments
- POST /payments/create
	- Body: { registration_id, amount_cents, idempotency_key?, provider? }
	- Behaviour:
		- If provider is 'paypal' (and PayPal env vars set): creates an Order and returns the
			approval URL; redirects back to /payments/paypal/return which captures the order and
			marks the registration as paid.
		- If provider is 'stripe' (and `STRIPE_API_KEY` is set): creates (or returns) a payment record in DB and opens a
			Stripe Checkout Session. The code stores our DB payment id in Stripe session metadata so webhooks can map back reliably.
		- If provider is 'wero': returns bank transfer instructions including an EPC QR payload the app can render; an admin can confirm with POST /payments/{id}/confirm.
		- If `STRIPE_API_KEY` is not set (dev mode): creates a dev-local payment document and
			returns a local `/payments/{id}/pay` link which marks the payment as paid when visited.
	- The implementation uses a DB atomic upsert to avoid duplicate-key races and supports
		an optional `idempotency_key` to deduplicate client retries.

#### PayPal Orders API
- GET /payments/paypal/config — returns `{ clientId, currency, env }` for initializing the JS SDK.
- POST /payments/paypal/orders — body `{ registration_id, amount_cents?, idempotency_key?, currency? }` creates a PayPal order and returns `{ id }`.
- POST /payments/paypal/orders/{order_id}/capture — captures an order; marks the linked registration as paid if completed.
- GET /payments/paypal/orders/{order_id} — returns raw order details from PayPal (useful for debugging).
- GET /payments/paypal/return?payment_id=...&token=... — alternative redirect-based capture flow (already supported).

	Note: The payment amount is authoritative from the event settings (`events.fee_cents`). The backend ignores client-supplied amounts and will return an error if a mismatched amount is provided. If an event has no fee (`fee_cents` == 0) the API returns {"status":"no_payment_required"}.

- GET /payments/{id}/pay
	- Dev-only: marks the payment as paid and updates the linked registration.

- POST /payments/webhooks/stripe
 - GET /payments/providers — Lists enabled providers and default selection.
 - GET /payments/paypal/return?payment_id=...&token=... — Captures PayPal order and marks paid.
 - POST /payments/{id}/confirm — Admin-only manual confirmation for bank transfers (Wero).

#### Invited Users & Payments
- When an invitation is accepted, a Registration with status "invited" is created for the invited email. Invited participants must pay to be confirmed. The frontend can call POST /payments/create with that registration_id and present either PayPal, Stripe, or Wero options. On success, the Registration status becomes "paid".
	- Receives Stripe webhooks. If `STRIPE_WEBHOOK_SECRET` is set it will verify signatures.
	- Processes `checkout.session.completed` events and marks the DB payment as paid.
	- The handler is resilient: it first looks up payments by provider id, and falls back to
		the Stripe session metadata (payment_db_id) if necessary.

### Security & Operational Hardening
* Structured logging with per-request `X-Request-ID` header + middleware (logs `request.start` / `request.end`).
* Global validation (422) and unhandled (500) handlers return JSON:
```json
{ "error": "validation_error", "detail": [...], "request_id": "uuid" }
```
```json
{ "error": "internal_server_error", "detail": "An unexpected error occurred", "request_id": "uuid" }
```
* Security headers + CSRF double-submit cookie/header pattern when using cookie-based auth.
* In-memory rate limit (development only; replace in prod).

### Refund Reporting Endpoint
`GET /payments/admin/events/{event_id}/refunds` (admin) returns registrations flagged eligible for refunds (based on event `refund_on_cancellation` + cancellation state). Use this to drive payout or refund processes externally.

### Matching (Deferred)
Code scaffolds exist (`/matching` router, distance utilities) but matching logic, grouping, and chat orchestration are intentionally postponed. These endpoints should be considered unstable/hidden until resumed.
- Passwords are validated against a minimal policy and are hashed (bcrypt).
- Failed login attempts are counted and a short lockout applied after repeated failures.
- Basic security headers and a simple in-memory rate limiter are enabled for dev.
- Verification and invitation emails are printed to the backend logs (no real email sending).

docker compose up --build
## Testing

Python tests (pytest + httpx + in-memory DB) live under `backend/tests/`.
Core flow example executed by CI / local run:
```bash
cd backend
USE_FAKE_DB_FOR_TESTS=1 pytest -q
```
This performs: user registration → forced verification → login → admin creates event → registration → cancellation attempt → refund report query.

When running against a real Mongo instance omit `USE_FAKE_DB_FOR_TESTS` (data persists; ensure a clean DB or use a dedicated database name per test run).

Add new tests by reusing fixtures in `tests/conftest.py` (admin token, verified user, async client).

## Example Curl Flows (Dev)
```bash
# Register (verification link printed)
curl -X POST http://localhost:8000/register \
	-H 'Content-Type: application/json' \
	-d '{"email":"t@example.com","password":"Testpass1","password_confirm":"Testpass1","first_name":"Test","last_name":"User","street":"S","street_no":"1","postal_code":"12345","city":"Town","gender":"prefer_not_to_say"}'

# After printing link, verify (replace <token>)
curl "http://localhost:8000/verify-email?token=<token>"

# Login
curl -X POST http://localhost:8000/login -H 'Content-Type: application/json' -d '{"username":"t@example.com","password":"Testpass1"}'

# Create event (admin JWT required)
curl -X POST http://localhost:8000/events \
	-H 'Authorization: Bearer <admin_token>' -H 'Content-Type: application/json' \
	-d '{"title":"My Event","date":"2030-01-01","capacity":20,"fee_cents":0,"status":"open"}'

# Solo registration (provide user token)
curl -X POST http://localhost:8000/registrations/solo \
	-H 'Authorization: Bearer <user_token>' -H 'Content-Type: application/json' \
	-d '{"event_id":"<event_id>"}'
```

## Production Notes
* Replace in-memory rate limiter with Redis / gateway policy.
* Use real SMTP (or transactional provider API) – set all `SMTP_*` variables; enable DKIM/SPF.
* Set strong values: `JWT_SECRET`, `TOKEN_PEPPER`, `ADDRESS_KEY` (32 bytes base64).
* Run MongoDB as a replica set for transaction support (future matching & payment consistency).
* Monitor logs: each response includes `X-Request-ID` and JSON errors embed `request_id`.
* Consider structured JSON logging (current plain format is easy to parse already).

## Roadmap / Deferred
| Area | Status |
|------|--------|
| Matching algorithm & travel optimization | Deferred (scaffold only) |
| Chat group creation & message lifecycle | Partial scaffold |
| Advanced refund automation (actual provider refund calls) | Future |
| Rich HTML email templates | Future |

---
Maintainers: update this file when adding publicly reachable endpoints, new settings, or changing error schemas. Keep examples minimal and current.


