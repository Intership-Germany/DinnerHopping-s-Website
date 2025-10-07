"""
FastAPI application for the DinnerHopping backend.
"""
import json
import os

# Load environment variables from local .env before other imports that read os.getenv
try:
    from pathlib import Path

    from dotenv import load_dotenv  # type: ignore
    _ENV_PATH = Path(__file__).resolve().parent / '.env'
    if _ENV_PATH.exists():
        load_dotenv(dotenv_path=_ENV_PATH, override=False)
except Exception:
    # best-effort; continue if python-dotenv not installed at runtime
    pass
import contextvars
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.proxy_headers import ProxyHeadersMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import get_current_user
from .db import close as close_mongo
from .db import connect as connect_to_mongo
from .logging_config import configure_logging
from .middleware.rate_limit import RateLimit
from .middleware.security import CSRFMiddleware, SecurityHeadersMiddleware
from .routers import (admin, chats, events, invitations, matching, payments,
                      registrations, users)
from .settings import get_settings

# Context variables for request-scoped logging
_ctx_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)
_ctx_client_ip: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("client_ip", default=None)

# Install a LogRecord factory to automatically attach request context to LogRecords.
_original_factory = logging.getLogRecordFactory()

def _record_factory(*args, **kwargs):
    record = _original_factory(*args, **kwargs)
    try:
        rid = _ctx_request_id.get()
        cip = _ctx_client_ip.get()
        if rid is not None:
            setattr(record, 'request_id', rid)
        if cip is not None:
            setattr(record, 'client_ip', cip)
    except Exception:
        # best-effort; never fail logging
        pass
    return record

logging.setLogRecordFactory(_record_factory)

configure_logging()
settings = get_settings()

# Compatibility shim: some bcrypt distributions expose `__version__` but not
# `__about__.__version__`. passlib sometimes attempts to read
# `bcrypt.__about__.__version__` and this can trigger noisy tracebacks.
# Detect and populate a minimal `__about__` object from `__version__` if needed.
try:
    import bcrypt as _bcrypt
    if not hasattr(_bcrypt, '__about__') and hasattr(_bcrypt, '__version__'):
        class _About:
            pass
        _about = _About()
        setattr(_about, '__version__', getattr(_bcrypt, '__version__'))
        setattr(_bcrypt, '__about__', _about)
except (ImportError, AttributeError):
    # best-effort only; don't fail startup for environments without bcrypt
    pass

@asynccontextmanager
async def _lifespan(app: FastAPI):
        # startup
        await connect_to_mongo()
        # ensure default email templates exist (best-effort)
        try:
            from . import email_templates as _email_templates
            await _email_templates.ensure_default_templates()
        except Exception:
            # best-effort: don't fail startup if templates can't be ensured
            pass
        try:
                yield
        finally:
                # shutdown
                await close_mongo()


app = FastAPI(title=settings.app_name,
                            debug=settings.debug,
                            version="1.0.0",
                            root_path=os.getenv('BACKEND_ROOT_PATH', ''),
                            docs_url=None if os.getenv('DISABLE_DOCS', '0') == '1' else '/docus',
                            redoc_url=None if os.getenv('DISABLE_DOCS', '0') == '1' else '/redocu',
                            openapi_url=None if os.getenv('DISABLE_DOCS', '0') == '1' else '/openapi.json',
                            lifespan=_lifespan,
                            redirect_slashes=False
                        )

app.add_middleware(TrustedHostMiddleware)

######## Structured Logging & Request ID Middleware ########
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get('X-Request-ID', str(uuid.uuid4()))
        # Extract client IP from X-Forwarded-For or remote
        xff = request.headers.get('X-Forwarded-For')
        client_ip = None
        if xff:
            # take first IP in the list
            client_ip = xff.split(',')[0].strip()
        else:
            # starlette request.client may be None in some test contexts
            client = getattr(request, 'client', None)
            client_ip = client.host if client else None
        # set into request.state and contextvars so log records can pick them up
        request.state.request_id = request_id
        request.state.client_ip = client_ip
        _ctx_request_id.set(request_id)
        _ctx_client_ip.set(client_ip)
        start = time.time()
        logger = logging.getLogger('request')
        # include extras as structured fields via logger.info extras
        logger.info('request.start method=%s path=%s', request.method, request.url.path)
        try:
            response = await call_next(request)
        except Exception as exc:  # let exception handlers format
            logger.exception('request.error')
            raise exc
        duration_ms = int((time.time() - start) * 1000)
        response.headers['X-Request-ID'] = request_id
        logger.info('request.end status=%s dur_ms=%s', response.status_code, duration_ms)
        return response

app.add_middleware(RequestIDMiddleware)


######## Global Exception Handlers ########

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={
        'error': 'validation_error',
        'detail': exc.errors(),
        'request_id': getattr(request.state, 'request_id', None),
    })


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logging.getLogger('app').exception('unhandled exception rid=%s', getattr(request.state, 'request_id', None))
    return JSONResponse(status_code=500, content={
        'error': 'internal_server_error',
        'detail': 'An unexpected error occurred',
        'request_id': getattr(request.state, 'request_id', None),
    })

# Customize Swagger UI so that the OpenAPI /docs interface sends credentials
# (cookies) and automatically injects the X-CSRF-Token header read from the
# CSRF cookie. This allows testing CSRF-protected endpoints from /docs.
from fastapi.openapi.docs import get_swagger_ui_html


def custom_swagger_ui_html(*, openapi_url: str, title: str):
    swagger_js = """
    window.onload = function() {
        const ui = SwaggerUIBundle({
            url: %s,
            dom_id: '#swagger-ui',
            presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
            layout: 'StandaloneLayout',
            requestInterceptor: (req) => {
                // include credentials so cookies are sent
                req.credentials = 'include';
                try {
                    // read csrf cookie (attempt __Host- first then fallback)
                    const getCookie = (name) => document.cookie.split('; ').reduce((r, v) => {
                        const parts = v.split('='); return parts[0] === name ? decodeURIComponent(parts[1]) : r
                    }, '')
                    const csrf = getCookie('__Host-csrf_token') || getCookie('csrf_token');
                    if (csrf && ['POST','PUT','PATCH','DELETE'].includes(req.method)) {
                        req.headers['X-CSRF-Token'] = csrf;
                    }
                } catch (e) {
                    // ignore
                }
                return req;
            }
        })
        window.ui = ui
    }
    """ % json.dumps(openapi_url)
    # generate the standard Swagger UI HTML and inject our custom script before </body>
    resp = get_swagger_ui_html(openapi_url=openapi_url, title=title)
    content = resp.body.decode(errors="ignore")
    content = content.replace("</body>", f"<script>{swagger_js}</script></body>")
    # Convert headers to a plain dict for HTMLResponse
    headers = dict(resp.headers)
    return HTMLResponse(content=content, status_code=resp.status_code, headers=headers)

@app.get('/', include_in_schema=False)
async def root():
    return {"message": "Hello! If you're seeing this, there are two possibilities: - Something went really wrong - or - You're trying to do something you shouldn't be doing."}

@app.get('/docs', include_in_schema=False)
async def overridden_swagger(request: Request):
    root_path = (request.scope.get('root_path') or '').rstrip('/')
    openapi_url = f"{root_path}{app.openapi_url}" if root_path else app.openapi_url
    return custom_swagger_ui_html(openapi_url=openapi_url, title=app.title + ' - Swagger UI')

ALLOWED_ORIGINS = settings.allowed_origins
if ALLOWED_ORIGINS == '*':
    origins = ["*"]
else:
    origins = [o.strip() for o in ALLOWED_ORIGINS.split(',') if o.strip()]

# If using cookies for auth (frontend + backend on different domains), you must
# set specific origins and allow_credentials=True. Browsers reject wildcard
# origins when allow_credentials is true.
allow_credentials = settings.cors_allow_credentials
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# security middlewares
# Add HTTPS redirect first, then ProxyHeaders last so it runs outermost
if settings.enforce_https:
    # Redirect HTTP to HTTPS (behind a proxy, ensure X-Forwarded-Proto is set)
    app.add_middleware(HTTPSRedirectMiddleware)
# Honor X-Forwarded-* headers from nginx so scheme/host are correct behind the proxy
# Trust all proxy headers (dev/local env behind nginx)
app.add_middleware(ProxyHeadersMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# CSRF double-submit protection for cookie-auth clients
app.add_middleware(CSRFMiddleware)
# simple in-memory rate limiter (dev)
app.add_middleware(RateLimit, max_requests=300, window_sec=60)

# Lifespan provided above via asynccontextmanager (_lifespan)

# Expose the users router at the root so endpoints like /register, /login, /profile exist
app.include_router(users.router, prefix="", tags=["users"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(invitations.router, prefix="/invitations", tags=["invitations"])
app.include_router(payments.router, prefix="/payments", tags=["payments"])
app.include_router(matching.router, prefix="/matching", tags=["matching"])
app.include_router(chats.router, prefix="/chats", tags=["chats"])
app.include_router(registrations.router, prefix="/registrations", tags=["registrations"])

api_router = APIRouter()

@api_router.get('/api/my-plan', tags=["plan"]) 
async def my_plan(current_user=Depends(get_current_user)):
    """Get the current user's plan."""
    # proxy to events module
    return await events.get_my_plan(current_user)

app.include_router(api_router)

# Fast healthcheck (no heavy DB access). Optionally: add a DB ping.
@app.get('/health', tags=["health"], include_in_schema=False)
async def health():
    return {"status": "ok"}
