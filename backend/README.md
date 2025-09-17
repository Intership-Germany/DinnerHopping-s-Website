
# DinnerHopping backend

This service is a minimal FastAPI backend with MongoDB for the DinnerHopping proof-of-concept.
It exposes user authentication, event management, registrations/invitations and a payments flow
(Stripe integration plus a dev-local fallback).

This README explains how the API works, how to run it locally, environment variables, and
how to run the included end-to-end tests.

## Quick start (development)

From the `backend` folder you can build and run MongoDB + the FastAPI service with Docker Compose:

```bash
cd backend
docker compose up --build
```

The backend listens on port 8000 by default. In development many interactive helpers are used
(verification links and invitation links are printed to the backend container logs).

## Environment variables

Key environment variables (can be placed in a `.env` file or injected by Docker Compose).
An example file `backend/.env.example` is provided; copy it to `.env` and fill in secrets (never commit the filled `.env`).

- `MONGO_URI` — MongoDB connection string (default: `mongodb://localhost:27017/dinnerhopping`).
- `JWT_SECRET` — secret key for JWT signing (default: `change-me`).
- `ADMIN_TOKEN` — token used for protecting admin endpoints (set to something secret in prod).
- `BACKEND_BASE_URL` — base URL used in generated links (default: `http://localhost:8000`).
- `ALLOWED_ORIGINS` — comma-separated CORS origins (default: `*`).
- `STRIPE_API_KEY` — optional; when set the payments flow will create Stripe Checkout Sessions.
- `STRIPE_WEBHOOK_SECRET` — optional; when set webhook requests will be verified.
- `SMTP_HOST` — optional; hostname of SMTP server (e.g. `ssl0.ovh.net` for OVH mail).
- `SMTP_PORT` — optional; SMTP port (587 for STARTTLS, 465 for SSL). Default: 587 when `SMTP_HOST` set.
- `SMTP_USER` — SMTP username (usually the full email address).
- `SMTP_PASS` — SMTP password.
- `SMTP_FROM_ADDRESS` — from address used when sending verification emails (default: `info@acrevon.fr`).
- `SMTP_USE_TLS` — `true`/`false` whether to use STARTTLS (default: `true`).
 - `SMTP_TIMEOUT_SECONDS` — SMTP network timeout in seconds (default: `10`).
 - `SMTP_MAX_RETRIES` — retry attempts for transient send failures (default: `2`).

### Email sending abstraction

Email related utilities live in `app/utils.py`:

* `send_email(to, subject, body, ...)` – core async helper performing SMTP delivery with retries.
* `generate_and_send_verification(email)` – creates + stores a verification token then sends an email (prints link in dev).
* `send_notification(email, title, lines)` – convenience wrapper for future generic notifications.

If no SMTP config is present the backend prints emails to stdout so flows remain testable locally. When adding new notification types prefer calling `send_notification` or building a custom body and calling `send_email` with an appropriate `category`.

## High level API overview

All endpoints are mounted at the root of the service. Below are the primary endpoints and their
behaviour. Request/response shapes are intentionally flexible to reflect the proof-of-concept nature
of this project; the tests accept multiple common shapes.

Authentication
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

Events
- GET /events
	- Returns a list of public events (supports query filters in the code).

- POST /events/ (admin or protected)
	- Creates an event. Depending on your deployment this route may be admin-only.

- GET /events/{id}
	- Returns event details (location is anonymized by default in list views).

Registrations & Invitations
- POST /events/{id}/register
	- Registers the authenticated user (or invited user) for an event. Can create invitation
		records when the request includes invited_emails and prints invitation links to logs.

- POST /invitations/{token}/accept
	- Accept an invitation (supports account creation for unauthenticated users).

Payments
- POST /payments/create
	- Body: { registration_id, amount_cents, idempotency_key? }
	- Behaviour:
		- If `STRIPE_API_KEY` is set: creates (or returns) a payment record in DB and opens a
			Stripe Checkout Session. The code stores our DB payment id in Stripe session metadata
			so webhooks can map back reliably.
		- If `STRIPE_API_KEY` is not set (dev mode): creates a dev-local payment document and
			returns a local `/payments/{id}/pay` link which marks the payment as paid when visited.
	- The implementation uses a DB atomic upsert to avoid duplicate-key races and supports
		an optional `idempotency_key` to deduplicate client retries.

- GET /payments/{id}/pay
	- Dev-only: marks the payment as paid and updates the linked registration.

- POST /payments/webhooks/stripe
	- Receives Stripe webhooks. If `STRIPE_WEBHOOK_SECRET` is set it will verify signatures.
	- Processes `checkout.session.completed` events and marks the DB payment as paid.
	- The handler is resilient: it first looks up payments by provider id, and falls back to
		the Stripe session metadata (payment_db_id) if necessary.

Security & dev helpers
- Passwords are validated against a minimal policy and are hashed (bcrypt).
- Failed login attempts are counted and a short lockout applied after repeated failures.
- Basic security headers and a simple in-memory rate limiter are enabled for dev.
- Verification and invitation emails are printed to the backend logs (no real email sending).

## Testing

There is a small Node-based end-to-end test located at `tests/api.e2e.test.js`. It uses
`supertest` and `jest` to exercise the running backend at `http://localhost:8000`.

To run the tests locally:

1. Ensure the backend is running (Docker Compose):

```bash
cd backend
docker compose up --build
```

2. Install test dev dependencies and run the test suite (from `backend`):

```bash
npm install
npm test
```

Notes about the test harness:
- The test generates a unique email per run and tolerates multiple API response shapes (this
	makes it resilient across dev setups where some operations require admin or verified users).
- The test expects the backend to be reachable at `http://localhost:8000`.

## Example curl flows (dev)

# Register (prints verification link in logs)
curl -X POST http://localhost:8000/register -H "Content-Type: application/json" -d '{"name":"T","email":"t@example.com","password":"Testpass1"}'

# Visit the printed verification URL (from container logs) or call:
curl "http://localhost:8000/verify-email?token=<token>"

# Login
curl -X POST http://localhost:8000/login -H "Content-Type: application/json" -d '{"username":"t@example.com","password":"Testpass1"}'

# Create an event (may be admin-only depending on deployment)
curl -X POST http://localhost:8000/events/ -H "Content-Type: application/json" -H "Authorization: Bearer <token>" -d '{"title":"My Event","date":"2025-09-20T19:00:00Z","location":{"name":"Venue"},"capacity":10}'

# Register for an event
curl -X POST http://localhost:8000/events/<eventId>/register -H "Content-Type: application/json" -H "Authorization: Bearer <token>" -d '{}'

# Dev-pay a payment
curl -X GET http://localhost:8000/payments/<paymentId>/pay

## Production notes

- The current implementation uses an in-memory rate limiter and prints emails to logs — both
	should be replaced for production (use Redis or a proper rate limiter and a real email provider).

SMTP / OVH example
- To send real verification emails via OVH, configure the SMTP variables (do not commit credentials):

```
# .env (example - keep secrets out of source control)
SMTP_HOST=ssl0.ovh.net
SMTP_PORT=587
SMTP_USER=info@acrevon.fr
SMTP_PASS=YOUR_OVH_SMTP_PASSWORD
SMTP_FROM_ADDRESS=info@acrevon.fr
SMTP_USE_TLS=true
SMTP_TIMEOUT_SECONDS=10
SMTP_MAX_RETRIES=2
BACKEND_BASE_URL=http://localhost:8000
```

With these set, registering a user will attempt to send the verification email via OVH SMTP. If
sending fails (network, bad credentials), the service falls back to printing the verification URL
to logs so you can still verify accounts in development.
- For Stripe webhooks and strong idempotency you should run MongoDB as a replica set and/or
	use provider metadata and transactions where appropriate.

If you want, I can:
- Add automated tests for payments and webhook idempotency.
- Create a Docker Compose profile that spins up the backend and runs the Node/Jest tests
	automatically for CI.


