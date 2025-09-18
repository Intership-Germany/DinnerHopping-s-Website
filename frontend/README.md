# DinnerHopping Frontend

## Environment Variables

To configure environment variables for the frontend (e.g. API URLs), use a `.env` file in the `frontend/` directory. See `.env.example` for the format:

```
BACKEND_BASE=http://10.160.2.11:8000
```

**Do not commit your `.env` file.** It is ignored by git.

### Usage

1. Copy `.env.example` to `.env` and edit as needed.
2. Run `node generate-config.js` to generate `public/config.js` before serving the frontend.
3. Make sure your HTML files include `<script src="config.js"></script>` before any script that uses the variables.

This service is a static website for the DinnerHopping project. It provides the user interface for account management, event browsing, chat, and admin dashboard.

This README explains how to build and run the frontend locally or in a container, and describes the project structure.

## Quick start (development)

From the `frontend` folder you can build and run the static site with Docker:

```bash
cd frontend
docker build -t dinnerhopping-frontend .
docker run -p 8080:80 dinnerhopping-frontend
```

The frontend will be available at http://localhost:8080

## Project structure

- `public/` — All static assets (HTML, images, etc.)
- `*.html` — Main HTML pages (home, login, profile, etc.)
- `nginx.conf` — Nginx configuration for serving the static site
- `Dockerfile` — Container build instructions
- `.dockerignore` — Files/folders excluded from the Docker build context

## Customization

- Edit HTML files directly to update content or UI.
- Tailwind CSS is loaded via CDN in each HTML file for rapid prototyping.
- No build system or JavaScript frameworks are used at this stage.

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
      - "8080:80"
    depends_on:
      - backend
```

---

For questions or contributions, see the main project repository.
