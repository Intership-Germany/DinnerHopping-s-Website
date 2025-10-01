from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
import os

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp: Response = await call_next(request)
        # Basic security headers
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['X-Frame-Options'] = 'DENY'
        resp.headers['Referrer-Policy'] = 'no-referrer'
        resp.headers['X-XSS-Protection'] = '1; mode=block'
        # HSTS (enable only when HTTPS enforced)
        if os.getenv('ENFORCE_HTTPS', 'true').lower() in ('1', 'true', 'yes'):
            # 2 years + include subdomains; add preload if desired
            preload = '; preload' if os.getenv('HSTS_PRELOAD', 'false').lower() in ('1','true','yes') else ''
            resp.headers['Strict-Transport-Security'] = f'max-age=63072000; includeSubDomains{preload}'
        return resp


class CSRFMiddleware(BaseHTTPMiddleware):
    """Enforce CSRF double-submit for cookie-auth on unsafe methods.

    If a request uses a cookie-based access token (presence of access token cookie)
    and no Authorization header, require that header 'X-CSRF-Token' equals the
    CSRF cookie value. Exempt specific paths (login/logout/refresh/webhooks).
    """

    def __init__(self, app):
        super().__init__(app)
        # do not cache enforcement flag at init: allow tests to toggle via env var
        self.enabled = None
        self.exempt_prefixes = (
            '/login', '/logout', '/refresh', '/docs', '/openapi.json',
            '/payments/webhooks', '/payments/paypal/return', '/webhooks',
            '/verify-email', '/users/verify-email', '/resend-verification', '/invitations/',
            '/forgot-password', '/reset-password', '/resend-verification',
        )

    async def dispatch(self, request: Request, call_next):
        enabled = os.getenv('CSRF_ENFORCE', 'true').lower() in ('1', 'true', 'yes')
        if not enabled:
            return await call_next(request)

        method = request.method.upper()
        if method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            path = request.url.path or ''
            if any(path.startswith(p) for p in self.exempt_prefixes):
                return await call_next(request)
            # If using cookie auth and no explicit Authorization header, enforce CSRF
            authz = request.headers.get('authorization') or ''
            has_bearer = authz.lower().startswith('bearer ')
            cookies = request.cookies or {}
            has_cookie_token = ('__Host-access_token' in cookies) or ('access_token' in cookies)
            if has_cookie_token and not has_bearer:
                csrf_header = request.headers.get('x-csrf-token')
                csrf_cookie = cookies.get('__Host-csrf_token') or cookies.get('csrf_token')
                if not csrf_header or not csrf_cookie or csrf_header != csrf_cookie:
                    return JSONResponse({"detail": "Missing or invalid CSRF token"}, status_code=403)
        return await call_next(request)
