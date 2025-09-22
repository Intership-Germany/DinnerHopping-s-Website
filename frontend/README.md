# DinnerHopping Frontend

This is a static site for DinnerHopping. It now uses cookie-based auth with CSRF protection and a refresh flow handled on the frontend.

## Environment Variables

To configure environment variables for the frontend (e.g. API URLs), use a `.env` file in the `frontend/` directory. See `.env.example` for the format:

```
BACKEND_BASE=http://10.160.2.11:8000
```

**Do not commit your `.env` file.** It is ignored by git.

### Usage

1. Copy `.env.example` to `.env` and edit as needed.
2. Run `node generate-config.js` to generate `public/config.js` before serving the frontend.
3. Make sure your HTML files include `<script src="config.js"></script>` and `<script src="client.js"></script>` before any script that uses the variables.

### Auth, Refresh and CSRF

- All requests include credentials (cookies) via a shared client in `public/client.js`.
- A CSRF token is managed in memory and added as `X-CSRF-Token` for mutating requests (POST/PUT/PATCH/DELETE).
- On 401/419 responses, the client calls `/refresh` once and retries the request.

Endpoints expected by the client:

- `GET /csrf` → returns `{ csrf_token }` JSON or returns it via `X-CSRF-Token` header.
- `POST /refresh` → refreshes the session and may rotate CSRF.
- `POST /logout` (optional) → clears cookies.

Global helpers exposed:

- `window.initCsrf()` — prefetches CSRF token (best-effort).
- `window.apiFetch(path, options)` — wrapper around fetch with credentials, CSRF, and refresh.

Local dev tip: If you're running the backend over plain HTTP, set `ALLOW_INSECURE_COOKIES=true` in backend env so cookies are sent without `Secure`. Do not enable this in production.

Note: We are currently in a hybrid mode. The login page uses JWT tokens (stored in localStorage) because the backend hasn’t exposed `/csrf` and `/refresh` yet. Once those endpoints are available and CORS is configured with explicit origins, we can switch login to cookies+CSRF as well.

This service is a static website for the DinnerHopping project. It provides the user interface for account management, event browsing, chat, and admin dashboard.

This README explains how to build and run the frontend locally or in a container, and describes the project structure.

## Quick start (development)

From the `frontend` folder you can build and run the static site with Docker:

```bash
cd frontend
docker build -t dinnerhopping-frontend .
docker run -p 80:80 dinnerhopping-frontend
```

The frontend will be available at http://localhost

## Project structure

- `public/` — All static assets (HTML, images, etc.)
- `public/partials/` — Shared HTML partials like `header.html` and `footer.html`
- `*.html` — Main HTML pages (home, login, profile, etc.)
- `nginx.conf` — Nginx configuration for serving the static site
- `Dockerfile` — Container build instructions
- `.dockerignore` — Files/folders excluded from the Docker build context

## Customization

- Edit HTML files directly to update content or UI.
- Tailwind CSS is loaded via CDN in each HTML file for rapid prototyping.
- No build system or JavaScript frameworks are used at this stage.

### Shared header/footer includes

To keep headers and footers consistent, we now load them from partials using a tiny include helper (`public/includes.js`).

Usage inside a page:

1. Ensure the page includes the script: `<script src="includes.js" defer></script>`
2. Insert include markers where you want shared UI:
  - Header: `<div data-include="partials/header.html"></div>`
  - Footer: `<div data-include="partials/footer.html"></div>`

Notes:
- The special hero header on `index.html` stays custom. You can still include the shared footer there.
- The include loader also highlights the active link based on the current filename.

## Deployment

You can deploy the frontend as a static container using any container platform (Docker, Kubernetes, etc.), or serve the `public/` folder with any static file server.

## Environment variables

No environment variables are required for the frontend. All content is static and served as-is.

## Example Docker Compose (optional)

If you want to orchestrate the frontend with the backend, add a service to your root `docker-compose.yml`:

```yaml
  frontend:
    build: ./frontend
    ports:
      - "80:80"
    depends_on:
      - backend
```

---

For questions or contributions, see the main project repository.
