// Lightweight HTTP client for DinnerHopping frontend (namespaced)
// Exposes under window.dh: apiFetch, initCsrf, apiGet/Post/Put/Patch/Delete
(function () {
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  const BASE = window.BACKEND_BASE_URL;
  let CROSS_ORIGIN = false;
  try {
    CROSS_ORIGIN = BASE ? new URL(BASE).origin !== window.location.origin : false;
  } catch {}
  const CSRF_HEADER = window.CSRF_HEADER || 'X-CSRF-Token';
  const CSRF_ENDPOINT = window.CSRF_ENDPOINT || null; // usually null (CSRF via cookies)
  const REFRESH_ENDPOINT = window.REFRESH_ENDPOINT || '/refresh';
  let csrfToken = null;
  let refreshing = null;
  let FORCE_BEARER_MODE = false;

  function getBearerToken() {
    try {
      const ls = localStorage.getItem('dh_access_token');
      if (ls) return ls;
    } catch {}
    try {
      const m = document.cookie.match(/(?:^|; )dh_token=([^;]+)/);
      if (m) return decodeURIComponent(m[1]);
    } catch {}
    return null;
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
  function isTokenExpiredSoon(tok, skew = 30) {
    const exp = decodeJwtExp(tok);
    if (!exp) return false;
    const now = Math.floor(Date.now() / 1000);
    return exp - now < skew;
  }
  function readCsrfFromResponse(resp) {
    const h = resp.headers.get(CSRF_HEADER) || resp.headers.get(CSRF_HEADER.toLowerCase());
    if (h) csrfToken = h;
    return h || null;
  }
  function getCookie(name) {
    try {
      return document.cookie
        .split('; ')
        .map((v) => v.split('='))
        .reduce((a, [k, v]) => (k === name ? decodeURIComponent(v || '') : a), '');
    } catch {
      return '';
    }
  }
  async function fetchCsrf() {
    const c = getCookie('__Host-csrf_token') || getCookie('csrf_token');
    if (c) {
      csrfToken = c;
      return c;
    }
    if (CSRF_ENDPOINT) {
      try {
        const res = await fetch(`${BASE}${CSRF_ENDPOINT}`, {
          credentials: 'include',
          headers: { Accept: 'application/json' },
        });
        readCsrfFromResponse(res);
        if (res.ok) {
          const data = await res
            .clone()
            .json()
            .catch(() => ({}));
          if (data.csrf_token) csrfToken = data.csrf_token;
        }
      } catch {}
    }
    return csrfToken;
  }
  async function ensureCsrfFor(method) {
    const needs = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(
      String(method || 'GET').toUpperCase()
    );
    if (!needs) return null;
    if (!csrfToken) await fetchCsrf();
    return csrfToken;
  }
  async function doRefresh() {
    const hasRefresh = () => /(?:^|; )(__Host-)?refresh_token=/.test(document.cookie);
    if (!hasRefresh()) return Promise.reject(new Error('No refresh cookie'));
    if (!refreshing) {
      refreshing = (async () => {
        try {
          const headers = { Accept: 'application/json' };
          if (csrfToken) headers[CSRF_HEADER] = csrfToken;
          const res = await fetch(`${BASE}${REFRESH_ENDPOINT}`, {
            method: 'POST',
            credentials: 'include',
            headers,
          });
          readCsrfFromResponse(res);
          if (res.ok) {
            const data = await res
              .clone()
              .json()
              .catch(() => ({}));
            if (data.csrf_token) csrfToken = data.csrf_token;
          }
          if (!res.ok) throw new Error('Refresh failed');
          return true;
        } finally {
          const done = refreshing;
          refreshing = null;
          return done;
        }
      })();
    }
    return refreshing;
  }

  async function apiFetch(path, opts) {
    const options = Object.assign({ method: 'GET' }, opts || {});
    options.method = (options.method || 'GET').toUpperCase();
    const existingBearer =
      options.headers && (options.headers.Authorization || options.headers.authorization);
    const storedToken = getBearerToken();
    const preferBearer = existingBearer || FORCE_BEARER_MODE || (CROSS_ORIGIN && storedToken);
    if (preferBearer) {
      options.credentials = 'omit';
    } else if (typeof options.credentials === 'undefined') {
      options.credentials = 'include';
    }
    options.headers = Object.assign({}, options.headers || {});
    if (preferBearer && !existingBearer && storedToken) {
      options.headers.Authorization = `Bearer ${storedToken}`;
      if (isTokenExpiredSoon(storedToken, 10)) options.headers['X-Token-Expiring'] = '1';
    }
    if (!preferBearer) {
      const t = await ensureCsrfFor(options.method);
      if (t) options.headers[CSRF_HEADER] = t;
    }
    // Build final URL: respect absolute URLs and robustly join base + relative path
    function buildUrl(p) {
      try {
        if (typeof p === 'string' && /^https?:\/\//i.test(p)) return p; // already absolute
      } catch {}
      const base = String(BASE || '');
      const trimmedBase = base.replace(/\/+$/, '');
      const rel = String(p || '');
      if (!trimmedBase) return rel; // fallback when BASE missing
      if (rel.startsWith('/')) return trimmedBase + rel;
      return trimmedBase + '/' + rel;
    }
    const url = buildUrl(path);
    let res;
    try {
      res = await fetch(url, options);
    } catch (e) {
      if (!preferBearer && CROSS_ORIGIN) {
        const bt = getBearerToken();
        if (bt) {
          FORCE_BEARER_MODE = true;
          options.credentials = 'omit';
          options.headers.Authorization = `Bearer ${bt}`;
          res = await fetch(url, options);
        } else {
          throw e;
        }
      } else {
        throw e;
      }
    }
    readCsrfFromResponse(res);
    const usedBearer = !!options.headers.Authorization || !!options.headers.authorization;
    if ((res.status === 401 || res.status === 419) && !usedBearer && !preferBearer) {
      try {
        await doRefresh();
        const t2 = await ensureCsrfFor(options.method);
        if (t2) options.headers[CSRF_HEADER] = t2;
        res = await fetch(url, options);
        readCsrfFromResponse(res);
      } catch {}
    }
    if ((res.status === 401 || res.status === 419) && (preferBearer || usedBearer)) {
      try {
        if (typeof window.handleUnauthorized === 'function') {
          window.handleUnauthorized({ autoRedirect: true, delayMs: 500 });
        } else {
          setTimeout(() => {
            window.location.href = 'login.html';
          }, 500);
        }
      } catch {}
    }
    return res;
  }

  async function parseJson(res) {
    const ct = res.headers.get('Content-Type') || '';
    if (/json/i.test(ct)) {
      try {
        return await res.clone().json();
      } catch {
        return null;
      }
    }
    return null;
  }
  function buildJsonOptions(method, data, opts) {
    const base = Object.assign({}, opts || {});
    base.method = method;
    base.headers = Object.assign({}, base.headers || {}, { Accept: 'application/json' });
    if (data !== undefined) {
      base.headers['Content-Type'] = base.headers['Content-Type'] || 'application/json';
      base.body = typeof data === 'string' ? data : JSON.stringify(data);
    }
    return base;
  }
  async function apiGet(p, o) {
    const r = await apiFetch(p, buildJsonOptions('GET', undefined, o));
    return { res: r, data: await parseJson(r) };
  }
  async function apiPost(p, d, o) {
    const r = await apiFetch(p, buildJsonOptions('POST', d, o));
    return { res: r, data: await parseJson(r) };
  }
  async function apiPut(p, d, o) {
    const r = await apiFetch(p, buildJsonOptions('PUT', d, o));
    return { res: r, data: await parseJson(r) };
  }
  async function apiPatch(p, d, o) {
    const r = await apiFetch(p, buildJsonOptions('PATCH', d, o));
    return { res: r, data: await parseJson(r) };
  }
  async function apiDelete(p, o) {
    const r = await apiFetch(p, buildJsonOptions('DELETE', undefined, o));
    return { res: r, data: await parseJson(r) };
  }

  // JSDoc public API
  /** @typedef {Response & {data?:any}} DHResponse */
  /** Fetch wrapper with cookie/bearer + CSRF handling. @param {string} path @param {RequestInit & {retry?:boolean}} [opts] */
  window.dh.apiFetch = apiFetch;
  /** Ensure CSRF token loaded (for early manual calls) */
  window.dh.initCsrf = fetchCsrf;
  window.dh.apiGet = apiGet;
  window.dh.apiPost = apiPost;
  window.dh.apiPut = apiPut;
  window.dh.apiPatch = apiPatch;
  window.dh.apiDelete = apiDelete;

  // Legacy global fallbacks (some older pages still call window.apiFetch / window.initCsrf)
  if (typeof window.apiFetch !== 'function') {
    window.apiFetch = window.dh.apiFetch;
  }
  if (typeof window.initCsrf !== 'function') {
    window.initCsrf = window.dh.initCsrf;
  }
})();
