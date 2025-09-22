"""
FastAPI application for the DinnerHopping backend.
"""
import os
from fastapi import FastAPI, APIRouter, Depends
from fastapi.middleware.cors import CORSMiddleware
from .auth import get_current_user
from .middleware.rate_limit import RateLimit
from .middleware.security import SecurityHeadersMiddleware, CSRFMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from .db import close as close_mongo, connect as connect_to_mongo
from .routers import admin, events, invitations, payments, users, matching, chats

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


app = FastAPI(title="DinnerHopping Backend")

ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', '*')
if ALLOWED_ORIGINS == '*':
    origins = ["*"]
else:
    origins = [o.strip() for o in ALLOWED_ORIGINS.split(',') if o.strip()]

# If using cookies for auth (frontend + backend on different domains), you must
# set specific origins and allow_credentials=True. Browsers reject wildcard
# origins when allow_credentials is true.
allow_credentials = os.getenv('CORS_ALLOW_CREDENTIALS', 'true').lower() in ('1','true','yes')
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# security middlewares
if os.getenv('ENFORCE_HTTPS', 'true').lower() in ('1','true','yes'):
    # Redirect HTTP to HTTPS (behind a proxy, ensure X-Forwarded-Proto is set)
    app.add_middleware(HTTPSRedirectMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# CSRF double-submit protection for cookie-auth clients
app.add_middleware(CSRFMiddleware)
# simple in-memory rate limiter (dev)
app.add_middleware(RateLimit, max_requests=300, window_sec=60)

@app.on_event("startup")
async def startup():
    """Connect to MongoDB on startup."""
    await connect_to_mongo()

@app.on_event("shutdown")
async def shutdown():
    """Disconnect from MongoDB on shutdown."""
    await close_mongo()

# Expose the users router at the root so endpoints like /register, /login, /profile exist
app.include_router(users.router, prefix="", tags=["users"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(invitations.router, prefix="/invitations", tags=["invitations"])
app.include_router(payments.router, prefix="/payments", tags=["payments"])
app.include_router(matching.router, prefix="/matching", tags=["matching"])
app.include_router(chats.router, prefix="/chats", tags=["chats"])

api_router = APIRouter()

@api_router.get('/api/my-plan', tags=["plan"]) 
async def my_plan(current_user=Depends(get_current_user)):
    """Get the current user's plan."""
    # proxy to events module
    return await events.get_my_plan(current_user)

app.include_router(api_router)
