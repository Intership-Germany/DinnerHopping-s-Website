import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

class SimpleRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 200, window_sec: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window_sec
        self.clients = {}  # ip -> [timestamps]

    async def dispatch(self, request: Request, call_next):
        client = request.client.host if request.client else 'unknown'
        now = time.time()
        arr = self.clients.get(client, [])
        # drop old
        arr = [t for t in arr if t > now - self.window]
        arr.append(now)
        self.clients[client] = arr
        if len(arr) > self.max_requests:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        return await call_next(request)
