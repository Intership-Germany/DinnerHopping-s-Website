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
(function(){
  const BASE = (typeof window !== 'undefined' && window.BACKEND_BASE_URL) || 'http://localhost:8000';
  const CSRF_HEADER = (typeof window !== 'undefined' && window.CSRF_HEADER) || 'X-CSRF-Token';
  const CSRF_ENDPOINT = (typeof window !== 'undefined' && window.CSRF_ENDPOINT) || '/csrf';
  const REFRESH_ENDPOINT = (typeof window !== 'undefined' && window.REFRESH_ENDPOINT) || '/refresh';

  let csrfToken = null;
  let refreshing = null; // Promise gate to avoid parallel refreshes

  function readCsrfFromResponse(resp){
    // Try header first
    const h = resp.headers.get(CSRF_HEADER) || resp.headers.get(CSRF_HEADER.toLowerCase());
    if (h) {
      csrfToken = h;
      return h;
    }
    return null;
  }

  async function fetchCsrf(){
    try {
      const res = await fetch(`${BASE}${CSRF_ENDPOINT}`, {
        method: 'GET',
        credentials: 'include',
        headers: { 'Accept': 'application/json' }
      });
      // Accept header-provided token or JSON body { csrf_token }
      const fromHeader = readCsrfFromResponse(res);
      if (fromHeader) return fromHeader;
      if (res.ok) {
        const data = await res.clone().json().catch(()=>({}));
        if (data && data.csrf_token) {
          csrfToken = data.csrf_token;
          return csrfToken;
        }
      }
      // Fallback: no token available, but continue; server may not require CSRF for GETs
      return null;
    } catch {
      // Network error; continue without token (may fail for mutating requests)
      return null;
    }
  }

  async function ensureCsrfFor(method){
    const needs = ['POST','PUT','PATCH','DELETE'].includes(String(method||'GET').toUpperCase());
    if (!needs) return null;
    if (!csrfToken) await fetchCsrf();
    return csrfToken;
  }

  async function doRefresh(){
    if (!refreshing){
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
          if (res.ok){
            const data = await res.clone().json().catch(()=>({}));
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
  async function apiFetch(path, opts){
    const options = Object.assign({ method: 'GET' }, opts || {});
    options.method = (options.method || 'GET').toUpperCase();
    options.credentials = 'include';
    options.headers = Object.assign({}, options.headers || {});

    // Attach CSRF for mutating verbs
    const token = await ensureCsrfFor(options.method);
    if (token) options.headers[CSRF_HEADER] = token;

    let res = await fetch(`${BASE}${path}`, options);
    readCsrfFromResponse(res);

    if (res.status === 401 || res.status === 419){
      // Attempt one refresh then retry once
      try {
        await doRefresh();
      } catch {
        return res; // propagate original 401/419
      }

      // Ensure CSRF again in case it rotated
      if (['POST','PUT','PATCH','DELETE'].includes(options.method)){
        if (!csrfToken) await fetchCsrf();
        if (csrfToken) options.headers[CSRF_HEADER] = csrfToken;
      }

      res = await fetch(`${BASE}${path}`, options);
      readCsrfFromResponse(res);
    }
    return res;
  }

  // Expose helpers globally
  if (typeof window !== 'undefined'){
    window.apiFetch = apiFetch;
    window.initCsrf = fetchCsrf;
  }
})();
