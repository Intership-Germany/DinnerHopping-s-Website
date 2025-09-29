# DinnerHopping Frontend

Static, no-build (vanilla JS + Tailwind CDN) frontend for DinnerHopping.

Recent refactor introduces a normalized JS architecture, a single global namespace (`window.dh`), reusable UI components, ESLint + Prettier, and a central network error banner.

## Environment Variables

To configure environment variables for the frontend (e.g. API URLs), use a `.env` file in the `frontend/` directory. See `.env.example` for the format:

```
BACKEND_BASE=http://10.160.2.11:8000
```

**Do not commit your `.env` file.** It is ignored by git.

### Usage

1. Copy `.env.example` to `.env` and edit as needed.
2. Run `node generate-config.js` to produce `public/js/config.js` (now loaded as `js/config.js`).
3. Include scripts in this order (example for most pages):

```html
<script src="js/config.js"></script>
<script src="js/core/client.js"></script>
<script src="js/utils/network-errors.js"></script>
<!-- components (optional, can be deferred) -->
<script src="js/components/password-toggle.js" defer></script>
<script src="js/components/password-strength.js" defer></script>
<script src="js/components/address-autocomplete.js" defer></script>
<!-- includes loader + page script -->
<script src="js/core/includes.js" defer></script>
<script src="js/pages/login.js" defer></script>
```

### Auth, Refresh and CSRF

- All requests go through `window.dh.apiFetch()` (namespaced client under `public/js/core/client.js`).
- Adds `X-CSRF-Token` automatically for mutating requests when using cookie auth.
- On 401/419 (cookie flow), performs one refresh attempt (`POST /refresh`) then retries.
- Supports auto-fallback to Bearer token mode when CORS credential flow is not feasible.

Endpoints expected by the client:

- `GET /csrf` → returns `{ csrf_token }` JSON or returns it via `X-CSRF-Token` header.
- `POST /refresh` → refreshes the session and may rotate CSRF.
- `POST /logout` (optional) → clears cookies.

Global helpers (under `window.dh`):

- `dh.apiFetch(path, options)` base wrapper.
- `dh.initCsrf()` prefetches CSRF token.
- Convenience: `dh.apiGet|Post|Put|Patch|Delete`.
- Components namespace: `dh.components.*` (password toggle, strength meter, address autocomplete).

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

The frontend will be available at <http://localhost>

## Project structure (post-refactor)

```text
frontend/
  public/
    index.html, login.html, profile.html, event.html, ...
    js/
      core/          # Core framework-like utilities
        client.js        (api + CSRF + refresh, exposes window.dh.apiFetch)
        auth-guard.js    (early redirect for protected pages)
        includes.js      (HTML partial loader)
        header.js        (header partial behavior)
      components/    # Reusable UI widgets
        password-toggle.js
        password-strength.js
        address-autocomplete.js
      pages/         # Page-specific controllers
        login.js
        profile.js
        event.js
        ...
      utils/         # Cross-cutting utilities
        network-errors.js  (central error banner + event)
      config.js      # Generated (ignored in VCS)
    partials/        # header.html / footer.html
  generate-config.js # Builds js/config.js from .env
  package.json       # Lint/format scripts (no bundler)
  .eslintrc.cjs / .prettierrc.json
```

Removed (legacy) flat scripts: `login-page.js`, `event-page.js`, `profile.js` — replaced by namespaced versions under `public/js/pages/`.

## Customization

- Edit HTML files directly to update content or UI.
- Tailwind CSS is loaded via CDN in each HTML file for rapid prototyping.
- No build system or JavaScript frameworks are used at this stage.

### Shared header/footer includes

Use `<div data-include="partials/header.html"></div>` and `<div data-include="partials/footer.html"></div>`. Loader is now `js/core/includes.js` and still highlights active links.

### JavaScript conventions

- No inline script logic (except config snippet if needed). Place code in page modules under `js/pages/`.
- Protected pages: load `js/core/auth-guard.js` before rendering body content.
- All shared UI bits belong to `js/components/`.
- Global scope pollution avoided; use `window.dh` only.
- Add new utilities under `js/utils/` or components under `js/components/`.

### Components

| Component | Purpose | Init Example |
|-----------|---------|--------------|
| password-toggle.js | Eye toggle for password inputs | `dh.components.initPasswordToggle('#pwd')` |
| password-strength.js | Strength bar + labels | `dh.components.initPasswordStrength('#pwd')` |
| address-autocomplete.js | Pelias-based address fields | `dh.components.initAddressAutocomplete({ selectors:{ street:'#s', number:'#n', postal:'#p', city:'#c' }})` |

### Central network error handling

`js/utils/network-errors.js` wraps `dh.apiFetch` and dispatches `dh:network-error` events; shows a dismissing banner for server (>=500) or network failures. Listen with:

```js
window.addEventListener('dh:network-error', e => console.log('Network issue', e.detail));
```

### Tailwind brand colors

`tailwind.config.js` defines brand colors. When using the CDN you can inline:

```html
<script>
  tailwind.config = { theme:{ extend:{ colors:{ brand:{ primary:'#f46f47', dark:'#172a3a', accent:'#008080', warning:'#ffc241' }}}}};
</script>
```

## Linting & formatting

Install once:
```bash
cd frontend
npm install
```

Run:
```bash
npm run lint       # ESLint (no warnings allowed)
npm run lint:fix   # Auto-fix
npm run format     # Prettier write
npm run check      # Lint + Prettier check
```


## Deployment

Serve the `public/` directory with any static server (Apache/Nginx/httpd container). Dockerfile already serves it via Apache.

## Environment variables

All runtime config is baked into `js/config.js` by `generate-config.js` using `.env`. No dynamic server-side templating required.

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
