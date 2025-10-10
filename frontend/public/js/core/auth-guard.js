// Early auth guard for protected pages (core)
(function () {
  function hasStoredToken() {
    try {
      // Respect that bearer fallback is disabled; don't treat LS token as auth.
      return false;
    } catch (e) {
      if (window && window.DEBUG_BANNER) {
        try {
          console.warn('[auth-guard] localStorage unavailable', e);
        } catch {}
      }
      return false;
    }
  }

  function evaluateAuth() {
    const page = (location.pathname.split('/').pop() || '').toLowerCase();
    const isLogin = page === 'login.html';
    if (isLogin) return; // never redirect away from login

    let authed = false;
    try {
      if (window.auth && typeof window.auth.hasAuth === 'function') {
        authed = !!window.auth.hasAuth();
      } else {
        authed = hasStoredToken();
      }
    } catch (err) {
      // Fail-safe: rely on stored token check if auth module throws
      if (window && window.DEBUG_BANNER) {
        try {
          console.error('[auth-guard] hasAuth check failed', err);
        } catch {}
      }
      authed = hasStoredToken();
    }

    if (!authed) {
      // Final safety: attempt a lightweight server check in case the session
      // is represented by HttpOnly cookies (not visible to document.cookie).
      // If the server responds OK for /profile we assume the user is
      // authenticated and should not be redirected. Use a short timeout to
      // avoid blocking the page load.
      (async function () {
        try {
          const base = window.BACKEND_BASE_URL || '';
          const trimmedBase = String(base).replace(/\/+$/, '');
          const url = (trimmedBase || '') + '/profile';
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 1500);
          const res = await fetch(url, { credentials: 'include', signal: controller.signal });
          clearTimeout(timer);
          if (res && res.ok) {
            // server confirms authenticated — do not redirect
            return;
          }
        } catch (e) {
          // any error (network, CORS, timeout) falls through to redirect
        }
        try {
          location.replace('login.html');
        } catch {
          location.href = 'login.html';
        }
      })();
    }
  }

  function ensureAuthReady(attempt) {
    attempt = attempt || 0;
    const authAvailable = !!(window.auth && typeof window.auth.hasAuth === 'function');
    if (authAvailable || hasStoredToken() || attempt >= 40) {
      evaluateAuth();
      return;
    }
    setTimeout(function () {
      ensureAuthReady(attempt + 1);
    }, 50);
  }

  try {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function onReady() {
        document.removeEventListener('DOMContentLoaded', onReady);
        ensureAuthReady(0);
      });
    } else {
      ensureAuthReady(0);
    }
  } catch (e) {
    if (window && window.DEBUG_BANNER) {
      try {
        console.error('auth-guard error', e);
      } catch {}
    }
    // Fall back to immediate evaluation if event registration fails.
    try {
      evaluateAuth();
    } catch {}
  }
})();
