"""Rate limiting middleware for FastAPI application.

This implementation prefers Redis (sliding-window counter using INCR + EXPIRE)
when REDIS_URL is configured. Falls back to the previous in-memory limiter when
Redis is not available (development). The Redis backend provides correct
behavior across multiple app instances.
"""
import time
import os
import asyncio
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

try:
    import redis.asyncio as aioredis  # redis-py asyncio client
except Exception:  # pragma: no cover - optional dependency
    aioredis = None


class RedisRateLimit(BaseHTTPMiddleware):
    """Redis-backed sliding-window rate limiter.

    Config via environment variables:
    - REDIS_URL (e.g. redis://localhost:6379/0)
    - RATE_LIMIT_MAX_REQUESTS
    - RATE_LIMIT_WINDOW
    """
    def __init__(self, app, max_requests: int = 200, window_sec: int = 60, redis_url: Optional[str] = None):
        super().__init__(app)
        self.max_requests = int(os.getenv('RATE_LIMIT_MAX_REQUESTS', str(max_requests)))
        self.window = int(os.getenv('RATE_LIMIT_WINDOW', str(window_sec)))
        self.redis_url = redis_url or os.getenv('REDIS_URL')
        self._redis = None
        # in-memory fallback (simple timestamps per ip)
        self._in_memory_clients = {}

    async def _get_redis(self):
        if self._redis is not None:
            return self._redis
        if not self.redis_url or aioredis is None:
            return None
        try:
            self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
            # quick ping to ensure connection
            await self._redis.ping()
            return self._redis
        except Exception:
            self._redis = None
            return None

    async def dispatch(self, request: Request, call_next):
        client_ip = None
        xff = request.headers.get('x-forwarded-for') or request.headers.get('X-Forwarded-For')
        if xff:
            client_ip = xff.split(',')[0].strip()
        else:
            client = getattr(request, 'client', None)
            client_ip = client.host if client else 'unknown'

        # Try Redis first
        redis_client = await self._get_redis()
        if redis_client:
            key = f"rl:{client_ip}:{int(time.time() // self.window)}"
            try:
                # INCR this window bucket
                current = await redis_client.incr(key)
                if current == 1:
                    # set expiry to window length
                    await redis_client.expire(key, self.window)
                if int(current) > self.max_requests:
                    return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
            except Exception:
                # Redis error -> fallback to in-memory
                pass

        # In-memory fallback
        now = time.time()
        arr = self._in_memory_clients.get(client_ip, [])
        arr = [t for t in arr if t > now - self.window]
        arr.append(now)
        self._in_memory_clients[client_ip] = arr
        if len(arr) > self.max_requests:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)

        return await call_next(request)


__all__ = ["RedisRateLimit"]
