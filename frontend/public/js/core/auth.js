// Centralized auth utility for DinnerHopping frontend
// Provides: login, logout, getToken, setToken, clearToken, isBearerFallback, ensureBanner
// Intent: unify token handling & show a warning banner when operating in bearer fallback mode (no HttpOnly cookies)
(function () {
  if (typeof window === 'undefined') return;
  const BANNER_ID = 'auth-mode-banner';
  const ACCESS_COOKIE_RX = /(?:^|; )(__Host-)?access_token=/;
  const REFRESH_COOKIE_RX = /(?:^|; )(__Host-)?refresh_token=/;
  const CSRF_COOKIE_RX = /(?:^|; )(?:__Host-)?csrf_token=/;

  function originOf(url) {
    try {
      return new URL(url, window.location.href).origin;
    } catch {
      return null;
    }
  }
  function backendOrigin() {
    return originOf(window.BACKEND_BASE_URL || '');
  }
  function sameOrigin() {
    const bo = backendOrigin();
    return bo && bo === window.location.origin;
  }
  function readCookie(name) {
    try {
      const m = document.cookie.match(
        new RegExp('(?:^|; )' + name.replace(/[-./?^*$()|[\]{}]/g, '\\$&') + '=([^;]*)')
      );
      return m ? decodeURIComponent(m[1]) : '';
    } catch {
      return '';
    }
  }
  function setCookie(name, value, days) {
    try {
      const maxAge = days ? '; Max-Age=' + days * 86400 : '';
      const attrs =
        'Path=/; SameSite=Strict' + (location.protocol === 'https:' ? '; Secure' : '') + maxAge;
      document.cookie = name + '=' + encodeURIComponent(value) + '; ' + attrs;
    } catch {}
  }

  function storeToken(token) {
    // Intentionally do not persist access tokens to JS storage for security.
    // Sessions should rely on HttpOnly cookies. If callers attempt to store a
    // token, we silently ignore it and remove any legacy stored token.
    try {
      localStorage.removeItem(LS_KEY);
    } catch {}
  }
  function clearStored() {
    try {
      localStorage.removeItem(LS_KEY);
    } catch {}
  }
  function getStored() {
    // Disable bearer fallback: do not expose any in-JS access token.
    // Prefer HttpOnly cookies. Return token only if present in a non-HttpOnly
    // cookie named 'dh_token' (legacy). In normal operation this will be null.
    try {
      const c = readCookie('dh_token');
      return c || null;
    } catch {
      return null;
    }
  }

  function hasHttpOnlySessionCookies() {
    return ACCESS_COOKIE_RX.test(document.cookie) || REFRESH_COOKIE_RX.test(document.cookie);
  }
  function hasAuth() {
    try {
      if (getStored()) return true;
    } catch {}
    return hasHttpOnlySessionCookies();
  }
  function isBearerFallback() {
    // in fallback if cross-origin OR missing refresh/access cookies while token exists
    const token = getStored();
    if (!token) return false; // not logged in
    if (!hasHttpOnlySessionCookies()) return true; // token only, no secure cookies
    if (!sameOrigin()) return true; // cross-origin always implies bearer mode for protected cookies
    return false;
  }

  function injectBanner() {
    // Only show a gentle informational banner if the app is truly running
    // without HttpOnly cookies. By default we will not display the insecure
    // banner since storing access tokens in JS is disabled.
    if (!isBearerFallback()) {
      removeBanner();
      return;
    }
    if (document.getElementById(BANNER_ID)) return;
    const div = document.createElement('div');
    div.id = BANNER_ID;
    div.textContent =
      'Security notice: running in reduced-security mode (no HttpOnly session cookies). Consider switching to a secure environment.';
    div.style.cssText =
      'background:#fef3c7;color:#92400e;padding:8px 12px;font-size:12px;font-weight:600;font-family:system-ui, Inter, sans-serif;text-align:center;border-bottom:1px solid #fcd34d;';
    const target = document.body;
    target.insertBefore(div, target.firstChild);
  }
  function removeBanner() {
    const el = document.getElementById(BANNER_ID);
    if (el) el.remove();
  }
  function ensureBanner() {
    // delay until DOM ready
    if (document.readyState === 'loading')
      document.addEventListener('DOMContentLoaded', injectBanner, { once: true });
    else injectBanner();
  }

  async function login(email, password) {
    const base = window.BACKEND_BASE_URL;
    if (!base) throw new Error('BACKEND_BASE_URL not configured');
    let same = sameOrigin();
    const body = JSON.stringify({ username: email, password });
    const opts = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body };
    if (same) opts.credentials = 'include';
    const res = await fetch(base + '/login', opts);
    let data = null;
    try {
      data = await res.clone().json();
    } catch {
      data = {};
    }
    if (!res.ok) {
      const detail = data && data.detail;
      throw new Error(typeof detail === 'string' ? detail : 'Login failed');
    }
    const token = data.access_token || data.token || data.accessToken;
    if (token) storeToken(token);
    ensureBanner();
    return data;
  }

  async function logout() {
    const base = window.BACKEND_BASE_URL;
    try {
      const headers = {};
      const token = getStored();
      if (token) headers.Authorization = 'Bearer ' + token;
      // Always include credentials so the backend can clear HttpOnly cookies even when cross-origin.
      // CSRF is exempt for /logout on the backend.
      const opts = { method: 'POST', headers, credentials: 'include' };
      await fetch(base + '/logout', opts);

      // Clear client-side tokens and local storage
      if (window.localStorage) {
        window.localStorage.removeItem('dh_access_token');
      }
      document.cookie = 'dh_token=; Max-Age=0; path=/;';
    } catch (error) {
      console.error('Logout failed:', error);
    }
    clearStored();
    // Best-effort: clear non-HttpOnly CSRF cookies client-side too (prevent reuse).
    // Note: HttpOnly cookies (access_token, refresh_token) can ONLY be cleared by the server.
    // We can only clear non-HttpOnly cookies here (like csrf_token).
    try {
      setCookie('csrf_token', '', -1);
      setCookie('__Host-csrf_token', '', -1);
    } catch {}
    removeBanner();
  }

  function decodeJwtExp(tok) {
    try {
      const p = tok.split('.');
      if (p.length !== 3) return null;
      return JSON.parse(atob(p[1].replace(/-/g, '+').replace(/_/g, '/'))).exp || null;
    } catch {
      return null;
    }
  }
  function tokenExpiresIn() {
    const t = getStored();
    if (!t) return null;
    const exp = decodeJwtExp(t);
    if (!exp) return null;
    return exp - Math.floor(Date.now() / 1000);
  }

  window.auth = Object.assign(window.auth || {}, {
    login,
    logout,
    getToken: getStored,
    setToken: storeToken,
    clearToken: clearStored,
    getCookie: readCookie,
    hasAuth,
    isBearerFallback,
    ensureBanner,
    tokenExpiresIn,
  });

  // auto banner on load if already logged with fallback token
  ensureBanner();
})();
