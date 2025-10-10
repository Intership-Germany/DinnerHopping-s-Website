// Debug helpers for pages: isOn/dlog/derror plus handy dump() utilities and optional network logging
// Usage:
//   <script src="js/utils/debug.js"></script>
//   if (dh.debug.isOn()) dh.debug.dumpAll();
//   const restore = dh.debug.enableNetworkLogging({ bodySnippet: 256 }); // optional; call restore() to undo
(function () {
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  const ns = (window.dh.debug = window.dh.debug || {});

  // Config helpers
  function isOn() {
    try { return !!window.DEBUG_BANNER; } catch { return false; }
  }
  function log() { if (!isOn()) return; try { console.log.apply(console, arguments); } catch {}
  }
  function warn() { if (!isOn()) return; try { console.warn.apply(console, arguments); } catch {}
  }
  function error() { if (!isOn()) return; try { console.error.apply(console, arguments); } catch {}
  }
  function group(label, fn) {
    if (!isOn()) return fn && fn();
    try { console.groupCollapsed(String(label)); } catch {}
    try { fn && fn(); } finally { try { console.groupEnd(); } catch {} }
  }
  function time(label) {
    const key = String(label || 'dbg');
    const start = performance.now();
    return function end() {
      const dur = performance.now() - start;
      log(`[time] ${key}: ${dur.toFixed(1)}ms`);
      return dur;
    };
  }

  // Small helpers
  function safeParseJSON(s) { try { return JSON.parse(s); } catch { return undefined; } }
  function getCookie(name) {
    try {
      const m = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '=([^;]*)'));
      return m ? decodeURIComponent(m[1]) : undefined;
    } catch { return undefined; }
  }
  function mask(v) {
    if (!v) return v;
    const s = String(v);
    return s.length <= 6 ? '•••' : s.slice(0, 3) + '•••' + s.slice(-3);
  }

  // Data collectors
  function getEnv() {
    const loc = window.location;
    const ua = navigator.userAgent;
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return {
      frontend_origin: loc.origin,
      path: loc.pathname + loc.search,
      backend_base_url: window.BACKEND_BASE_URL,
      csrf_header: window.CSRF_HEADER || 'X-CSRF-Token',
      debug_banner: !!window.DEBUG_BANNER,
      user_agent: ua,
      timezone: tz,
      lang: navigator.language,
      languages: navigator.languages,
      online: navigator.onLine,
      screen: { w: window.screen && window.screen.width, h: window.screen && window.screen.height },
    };
  }

  function getStorage() {
    const ls = {}; const ss = {};
    try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); ls[k] = localStorage.getItem(k); } } catch {}
    try { for (let i = 0; i < sessionStorage.length; i++) { const k = sessionStorage.key(i); ss[k] = sessionStorage.getItem(k); } } catch {}
    return { localStorage: ls, sessionStorage: ss };
  }

  function getCookies(maskSensitive = true) {
    const out = {};
    try {
      document.cookie.split(';').forEach((pair) => {
        const p = pair.trim(); if (!p) return; const idx = p.indexOf('=');
        const k = idx >= 0 ? p.slice(0, idx) : p; const v = idx >= 0 ? decodeURIComponent(p.slice(idx + 1)) : '';
        out[k] = maskSensitive ? mask(v) : v;
      });
    } catch {}
    return out;
  }

  function getAuthSnapshot() {
  // Access tokens are not stored in localStorage for security.
  const lsToken = null;
    return {
      mode: lsToken ? 'bearer-ls' : (tokenCookie ? 'legacy-cookie' : 'unknown'),
      has_csrf_cookie: !!getCookie('csrf_token'),
      has_refresh_cookie: !!getCookie('refresh_token') || !!getCookie('__Host-refresh_token'),
      has_access_cookie: !!getCookie('access_token') || !!getCookie('__Host-access_token'),
      ls_access_present: !!lsToken,
      ls_access_preview: lsToken ? mask(lsToken) : undefined,
    };
    // Removed has_dh_token_cookie from the return object
  }

  async function checkAuthServer() {
    try {
      const apiGet = window.dh && typeof window.dh.apiGet === 'function' && window.dh.apiGet;
      if (!apiGet) return { ok: false, reason: 'no-client' };
      // Best effort: ensure CSRF if client provides it
      if (window.dh && typeof window.dh.initCsrf === 'function') {
        try { await window.dh.initCsrf(); } catch {}
      } else if (typeof window.initCsrf === 'function') {
        try { await window.initCsrf(); } catch {}
      }
      const { res, data } = await apiGet('/profile');
      return { ok: !!(res && res.ok), status: res && res.status, roles: data && data.roles, email: data && data.email };
    } catch (e) {
      return { ok: false, error: String(e && e.message || e) };
    }
  }

  function getPerformanceSnapshot(limit = 50) {
    const nav = performance.getEntriesByType && performance.getEntriesByType('navigation');
    const res = performance.getEntriesByType && performance.getEntriesByType('resource');
    return {
      navigation: nav && nav[0] || undefined,
      recent_resources: (res || []).slice(-limit).map((r) => ({ name: r.name, type: r.initiatorType, dur: r.duration })),
    };
  }

  // Optional: network logging wrapper around dh.apiFetch/window.apiFetch (idempotent)
  function enableNetworkLogging(opts) {
    if (!isOn()) return function noop(){};
    const options = Object.assign({ bodySnippet: 0, logHeaders: false }, opts || {});
    const hasDh = !!(window.dh && window.dh.apiFetch);
    const key = hasDh ? 'dh.apiFetch' : 'apiFetch';
    const orig = hasDh ? window.dh.apiFetch : window.apiFetch;
    if (typeof orig !== 'function') { warn('[debug] no apiFetch found to wrap'); return function noop(){}; }
    if (orig && orig._dhNetworkLogger) { warn('[debug] network logging already enabled'); return orig._dhNetworkLogger.restore || function(){}; }

    const wrapped = async function(path, init) {
      const start = performance.now();
      let reqInfo = { method: (init && init.method) || 'GET', path: String(path) };
      try {
        if (options.logHeaders && init && init.headers) reqInfo.headers = init.headers;
        if (options.bodySnippet && init && init.body) {
          const s = typeof init.body === 'string' ? init.body : (typeof init.body === 'object' ? JSON.stringify(init.body) : String(init.body));
          reqInfo.body = s.length > options.bodySnippet ? s.slice(0, options.bodySnippet) + '…' : s;
        }
      } catch {}
      log('[net:req]', reqInfo);
      try {
        const res = await orig.apply(this, arguments);
        const dur = performance.now() - start;
        let preview;
        try { preview = res && res.clone && (await res.clone().text()).slice(0, 200); } catch {}
        log('[net:res]', { status: res && res.status, ok: res && res.ok, dur: Math.round(dur)+'ms', url: reqInfo.path, preview });
        return res;
      } catch (e) {
        const dur = performance.now() - start;
        error('[net:err]', { url: reqInfo.path, dur: Math.round(dur)+'ms', error: String(e && e.message || e) });
        throw e;
      }
    };
    wrapped._dhNetworkLogger = { wrapped: true };
    if (hasDh) {
      window.dh.apiFetch = wrapped;
    } else {
      window.apiFetch = wrapped;
    }
    const restore = function() {
      if (hasDh) window.dh.apiFetch = orig; else window.apiFetch = orig;
    };
    wrapped._dhNetworkLogger.restore = restore;
    log('[debug] enabled network logging on', key);
    return restore;
  }

  // Pretty printers
  function dumpEnv() { group('ENV', () => log(getEnv())); }
  function dumpAuth() { group('AUTH (client-side snapshot)', () => log(getAuthSnapshot())); }
  async function dumpAuthServer() { const a = await checkAuthServer(); group('AUTH (server check /profile)', () => log(a)); return a; }
  function dumpCookies() { group('COOKIES (masked)', () => log(getCookies(true))); }
  function dumpStorage() { group('STORAGE', () => log(getStorage())); }
  function dumpPerf() { group('PERF', () => log(getPerformanceSnapshot())); }

  async function dumpAll() {
    if (!isOn()) return;
    dumpEnv();
    dumpAuth();
    await dumpAuthServer();
    dumpCookies();
    dumpStorage();
    dumpPerf();
  }

  // Exports
  ns.isOn = isOn;
  ns.log = log; ns.warn = warn; ns.error = error; ns.group = group; ns.time = time;
  ns.getEnv = getEnv; ns.getStorage = getStorage; ns.getCookies = getCookies; ns.getAuthSnapshot = getAuthSnapshot;
  ns.checkAuthServer = checkAuthServer;
  ns.getPerformanceSnapshot = getPerformanceSnapshot;
  ns.dumpEnv = dumpEnv; ns.dumpAuth = dumpAuth; ns.dumpAuthServer = dumpAuthServer; ns.dumpCookies = dumpCookies; ns.dumpStorage = dumpStorage; ns.dumpPerf = dumpPerf; ns.dumpAll = dumpAll;
  ns.enableNetworkLogging = enableNetworkLogging;
  ns.attachNetworkErrorLogging = function attachNetworkErrorLogging() {
    try {
      document.addEventListener('dh:network-error', function (ev) {
        error('[net:center:error]', ev && ev.detail);
      });
      log('[debug] attached network error listener (dh:network-error)');
    } catch {}
  };
})();
