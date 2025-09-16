from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .middleware.security import SecurityHeadersMiddleware
from .middleware.rate_limit import SimpleRateLimitMiddleware
import os
from .routers import users, events, admin, invitations
from .routers import payments
from .db import connect_to_mongo, close_mongo

app = FastAPI(title="DinnerHopping Backend")

ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', '*')
if ALLOWED_ORIGINS == '*':
    origins = ["*"]
else:
    origins = [o.strip() for o in ALLOWED_ORIGINS.split(',') if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# security middlewares
app.add_middleware(SecurityHeadersMiddleware)
# simple in-memory rate limiter (dev)
app.add_middleware(SimpleRateLimitMiddleware, max_requests=300, window_sec=60)

@app.on_event("startup")
async def startup():
    await connect_to_mongo()

@app.on_event("shutdown")
async def shutdown():
    await close_mongo()

app.include_router(users.router, prefix="/users", tags=["users"])
# Also expose the same users router at the root so endpoints like /register, /login, /profile exist
app.include_router(users.router, prefix="", tags=["auth"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(invitations.router, prefix="/invitations", tags=["invitations"])
app.include_router(payments.router, prefix="/payments", tags=["payments"])

# compatibility endpoint for personal plan
from fastapi import APIRouter, Depends
from .auth import get_current_user

api_router = APIRouter()

@api_router.get('/api/my-plan', tags=["plan"])  
async def my_plan(current_user=Depends(get_current_user)):
    # proxy to events module
    return await events.get_my_plan(current_user)

app.include_router(api_router)
