// Lightweight HTTP client for DinnerHopping frontend
// - Always sends credentials (cookies)
// - Keeps CSRF token in memory and attaches it for mutating requests
// - On 401/419, tries a refresh flow once, then retries the original request
// - Updates CSRF token from response header if provided
//
// Conventions (can be overridden via window.* before loading this file):
//   window.CSRF_HEADER = 'X-CSRF-Token'
//   window.CSRF_ENDPOINT = '/csrf'           // returns { csrf_token } or sends it via header
//   window.REFRESH_ENDPOINT = '/refresh'     // refreshes the session/rotation of tokens
(function () {
  // Guard: ensure BACKEND_BASE_URL is defined early (helps detect missing generated config.js)
  try {
    if (typeof window !== 'undefined' && typeof window.BACKEND_BASE_URL === 'undefined') {
      console.error('[client.js] window.BACKEND_BASE_URL is undefined. Did you run: node generate-config.js ?');
    }
  } catch { }
  const BASE = (typeof window !== 'undefined' && window.BACKEND_BASE_URL);
  let CROSS_ORIGIN = false;
  try {
    if (typeof window !== 'undefined' && BASE) {
      CROSS_ORIGIN = (new URL(BASE).origin !== window.location.origin);
    }
  } catch { }
  const CSRF_HEADER = (typeof window !== 'undefined' && window.CSRF_HEADER) || 'X-CSRF-Token';
  // Default to no CSRF endpoint; backend sets CSRF via cookie on login/refresh
  const CSRF_ENDPOINT = (typeof window !== 'undefined' ? window.CSRF_ENDPOINT : null) || null;
  const REFRESH_ENDPOINT = (typeof window !== 'undefined' && window.REFRESH_ENDPOINT) || '/refresh';

  let csrfToken = null;
  let refreshing = null; // Promise gate to avoid parallel refreshes
  let FORCE_BEARER_MODE = false; // toggled when CORS credential flow fails

  function getBearerToken() {
    // Priority: explicit override, localStorage, cookie
    try {
      if (typeof window !== 'undefined') {
        const ls = window.localStorage && window.localStorage.getItem('dh_access_token');
        if (ls) return ls;
      }
    } catch { }
    // Fallback to legacy cookie set by login-page.js
    try {
      const m = document.cookie.match(/(?:^|; )dh_token=([^;]+)/);
      if (m) return decodeURIComponent(m[1]);
    } catch { }
    return null;
  }

  function decodeJwtExp(token) {
    try {
      const parts = token.split('.');
      if (parts.length !== 3) return null;
      const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
      return payload.exp ? Number(payload.exp) : null;
    } catch { return null; }
  }

  function isTokenExpiredSoon(token, skewSec = 30) {
    const exp = decodeJwtExp(token);
    if (!exp) return false; // unknown => assume valid
    const now = Math.floor(Date.now() / 1000);
    return (exp - now) < skewSec;
  }

  function readCsrfFromResponse(resp) {
    // Try header first
    const h = resp.headers.get(CSRF_HEADER) || resp.headers.get(CSRF_HEADER.toLowerCase());
    if (h) {
      csrfToken = h;
      return h;
    }
    return null;
  }

  function getCookie(name) {
    try {
      if (window.auth && typeof window.auth.getCookie === 'function') {
        return window.auth.getCookie(name) || '';
      }
      return document.cookie
        .split('; ')
        .map(v => v.split('='))
        .reduce((acc, [k, v]) => (k === name ? decodeURIComponent(v || '') : acc), '');
    } catch { return ''; }
  }

  async function fetchCsrf() {
    // Primary: read CSRF from cookie set at login/refresh
    const cookieCsrf = getCookie('__Host-csrf_token') || getCookie('csrf_token');
    if (cookieCsrf) {
      csrfToken = cookieCsrf;
      return csrfToken;
    }
    // Optional: if an explicit endpoint is configured, try it
    if (CSRF_ENDPOINT) {
      try {
        const res = await fetch(`${BASE}${CSRF_ENDPOINT}`, {
          method: 'GET',
          credentials: 'include',
          headers: { 'Accept': 'application/json' }
        });
        const fromHeader = readCsrfFromResponse(res);
        if (fromHeader) return fromHeader;
        if (res.ok) {
          const data = await res.clone().json().catch(() => ({}));
          if (data && data.csrf_token) {
            csrfToken = data.csrf_token;
            return csrfToken;
          }
        }
      } catch {
        // ignore
      }
    }
    return null;
  }

  async function ensureCsrfFor(method) {
    const needs = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(String(method || 'GET').toUpperCase());
    if (!needs) return null;
    if (!csrfToken) await fetchCsrf();
    return csrfToken;
  }

  async function doRefresh() {
    // Avoid attempting refresh if no refresh cookie present (initial unauth state)
    const hasRefresh = () => {
      try {
        return /(?:^|; )(__Host-)?refresh_token=/.test(document.cookie);
      } catch { return false; }
    };
    if (!hasRefresh()) {
      // No refresh cookie => user not logged in yet; propagate a 401-like failure
      return Promise.reject(new Error('No refresh cookie present'));
    }
    if (!refreshing) {
      refreshing = (async () => {
        try {
          // Try sending along current CSRF (some servers require it on refresh)
          const headers = { 'Accept': 'application/json' };
          if (csrfToken) headers[CSRF_HEADER] = csrfToken;
          const res = await fetch(`${BASE}${REFRESH_ENDPOINT}`, {
            method: 'POST',
            credentials: 'include',
            headers
          });
          readCsrfFromResponse(res);
          // also accept JSON-based CSRF token
          if (res.ok) {
            const data = await res.clone().json().catch(() => ({}));
            if (data && data.csrf_token) csrfToken = data.csrf_token;
          }
          if (!res.ok) throw new Error(`Refresh failed (${res.status})`);
          return true;
        } finally {
          // release the gate; ensure sequential refreshes
          const done = refreshing;
          refreshing = null;
          return done;
        }
      })();
    }
    return refreshing;
  }

  // Main helper
  async function apiFetch(path, opts) {
    const options = Object.assign({ method: 'GET' }, opts || {});
    options.method = (options.method || 'GET').toUpperCase();
    // Decide auth mode: if explicitly provided bearer header, honor it. Else if cross-origin and we have a token, prefer bearer to avoid CORS credential restrictions.
    const existingBearer = options.headers && (options.headers.Authorization || options.headers.authorization);
    const storedToken = getBearerToken();
    const preferBearer = existingBearer || FORCE_BEARER_MODE || (CROSS_ORIGIN && storedToken);
    if (preferBearer) {
      options.credentials = 'omit'; // do not send cookies to bypass wildcard+credentials CORS restriction
    } else if (typeof options.credentials === 'undefined') {
      // default cookie mode only if not preferring bearer
      options.credentials = 'include';
    }
    options.headers = Object.assign({}, options.headers || {});

    if (preferBearer && !existingBearer && storedToken) {
      // Attach bearer header
      options.headers['Authorization'] = `Bearer ${storedToken}`;
      // Simple proactive expiry check; if nearly expired, we can't refresh (needs cookies) so just clear to force re-login soon
      if (isTokenExpiredSoon(storedToken, 10)) {
        // Mark so that next 401 triggers redirect quickly
        options.headers['X-Token-Expiring'] = '1';
      }
    }

    // Attach CSRF for mutating verbs
    if (!preferBearer) { // CSRF only relevant for cookie-based flows
      const token = await ensureCsrfFor(options.method);
      if (token) options.headers[CSRF_HEADER] = token;
    }

    const url = `${BASE}${path}`;
    let res;
    try {
      res = await fetch(url, options);
    } catch (e) {
      // Network-level failure (possibly CORS rejection before headers) — attempt bearer fallback once if we haven't tried it
      if (!preferBearer && CROSS_ORIGIN) {
        const bt = getBearerToken();
        if (bt) {
          FORCE_BEARER_MODE = true;
          options.credentials = 'omit';
          options.headers['Authorization'] = `Bearer ${bt}`;
          res = await fetch(url, options);
        } else {
          throw e;
        }
      } else {
        throw e;
      }
    }
    readCsrfFromResponse(res);

    // If unauthorized and we used cookie-based auth (no explicit Bearer header), try one refresh-then-retry
    const usedBearer = !!options.headers['Authorization'] || !!options.headers['authorization'];
    if ((res.status === 401 || res.status === 419) && !usedBearer && !preferBearer) {
      try {
        await doRefresh();
        // Re-attach CSRF (it may have rotated)
        const token2 = await ensureCsrfFor(options.method);
        if (token2) options.headers[CSRF_HEADER] = token2;
        // Retry once
        res = await fetch(url, options);
        readCsrfFromResponse(res);
      } catch (e) {
        // fall through to unauthorized handler below
      }
    }

    if ((res.status === 401 || res.status === 419) && preferBearer) {
      // In bearer mode we cannot refresh (needs cookies). Force redirect.
      try {
        if (typeof window !== 'undefined' && typeof window.handleUnauthorized === 'function') {
          window.handleUnauthorized({ autoRedirect: true, delayMs: 500 });
        } else if (typeof window !== 'undefined') {
          setTimeout(() => { window.location.href = 'login.html'; }, 500);
        }
      } catch { }
    } else if (res.status === 401 || res.status === 419) {
      try {
        if (typeof window !== 'undefined' && typeof window.handleUnauthorized === 'function') {
          window.handleUnauthorized({ autoRedirect: true, delayMs: 1200 });
        }
      } catch { }
    }
    return res;
  }

  // Convenience JSON helpers (thin wrappers)
  function buildJsonOptions(method, data, opts) {
    const base = Object.assign({}, opts || {});
    base.method = method;
    base.headers = Object.assign({}, base.headers || {}, { 'Accept': 'application/json' });
    if (data !== undefined) {
      base.headers['Content-Type'] = base.headers['Content-Type'] || 'application/json';
      base.body = (typeof data === 'string') ? data : JSON.stringify(data);
    }
    return base;
  }

  async function parseJson(res) {
    const ct = res.headers.get('Content-Type') || '';
    if (/json/i.test(ct)) {
      try { return await res.clone().json(); } catch { return null; }
    }
    return null;
  }

  async function apiGet(path, opts) {
    const res = await apiFetch(path, buildJsonOptions('GET', undefined, opts));
    const data = await parseJson(res);
    return { res, data };
  }
  async function apiDelete(path, opts) {
    const res = await apiFetch(path, buildJsonOptions('DELETE', undefined, opts));
    const data = await parseJson(res);
    return { res, data };
  }
  async function apiPost(path, data, opts) {
    const res = await apiFetch(path, buildJsonOptions('POST', data, opts));
    const body = await parseJson(res);
    return { res, data: body };
  }
  async function apiPut(path, data, opts) {
    const res = await apiFetch(path, buildJsonOptions('PUT', data, opts));
    const body = await parseJson(res);
    return { res, data: body };
  }
  async function apiPatch(path, data, opts) {
    const res = await apiFetch(path, buildJsonOptions('PATCH', data, opts));
    const body = await parseJson(res);
    return { res, data: body };
  }

  // Expose helpers globally
  if (typeof window !== 'undefined') {
    window.apiFetch = apiFetch;
    window.initCsrf = fetchCsrf;
    window.apiGet = apiGet;
    window.apiPost = apiPost;
    window.apiPut = apiPut;
    window.apiPatch = apiPatch;
    window.apiDelete = apiDelete;
  }
})();
