// Header partial behavior: protect links when unauthenticated and show admin shortcut for admins
(function () {
  try {
    function isDbg(){ try{return !!window.DEBUG_BANNER;}catch{return false;} }
    function dlog(){ if(!isDbg()) return; try{ console.log.apply(console, arguments);}catch{} }
    let authed = false;
    if (window.auth && typeof window.auth.hasAuth === 'function') {
      try {
        authed = window.auth.hasAuth();
      } catch (e) {
        dlog('[header] auth.hasAuth error, fallback to cookie', e);
        authed = /(?:^|; )(__Host-)?access_token=/.test(document.cookie);
      }
    } else {
      authed = /(?:^|; )(__Host-)?access_token=/.test(document.cookie);
    }
    dlog('[header] authed=', authed, 'mode=', (function(){
      try{
        // Do not expose bearer tokens from localStorage; prefer cookie check
        var cookieMode = /(?:^|; )(__Host-)?access_token=/.test(document.cookie) ? 'cookies' : '__no_cookie__';
        return cookieMode;
      }catch{return '__unknown__'}
    })());

    // If we don't see auth via document.cookie, double-check with the server
    // before turning protected links into login redirects. This avoids creating
    // broken links when the session is held in HttpOnly cookies invisible to JS.
    async function protectLinksIfUnauthed() {
      const links = Array.from(document.querySelectorAll('a[data-protected]'));
      if (links.length === 0) return;
      if (authed) return; // already authed according to cookie
      try {
        const base = window.BACKEND_BASE_URL || '';
        const trimmedBase = String(base).replace(/\/+$/, '');
        const url = (trimmedBase || '') + '/profile';
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 1500);
        const res = await fetch(url, { credentials: 'include', signal: controller.signal });
        clearTimeout(timer);
        if (res && res.ok) {
          // server says we're authenticated — leave protected links as-is
          return;
        }
      } catch (e) {
        // network/CORS/timeout errors fallthrough to marking as unauthenticated
      }
      // Redirect to login page with ?next=originalpath
      const loc = window.location;
      const currentPath = loc.pathname + loc.search + loc.hash;
      const loginPath = 'login.html?next=' + encodeURIComponent(currentPath);
      links.forEach(function (el) {
        try {
          el.setAttribute('href', loginPath);
          el.classList.add('protected-link');
        } catch (e) {}
      });
    }
    // Trigger the protection check immediately (non-blocking)
    try { protectLinksIfUnauthed(); } catch (e) {}

    // Reveal Admin link only for admins. Use dh client if available; retry until ready (bounded).
    function revealAdminIfAllowed(attempt) {
      attempt = attempt || 0;
      var el = document.getElementById('nav-admin');
      if (!el) {
        dlog('[header] nav-admin element not found');
        return;
      }
      var dhReady = !!(window.dh && typeof window.dh.apiGet === 'function');
      var beDefined = window.BACKEND_BASE_URL !== undefined;
      if (!dhReady || !beDefined) {
        if (attempt < 80) {
          if (attempt === 0) dlog('[header] waiting for client/baseURL...');
          return setTimeout(function(){ revealAdminIfAllowed(attempt + 1); }, 50);
        }
        dlog('[header] client/baseURL still not ready after', attempt, 'attempts');
        return;
      }
      dlog('[header] checking /profile for admin role (attempt', attempt, ')');
      window.dh
        .apiGet('/profile')
        .then(function (out) {
          var res = out && out.res;
          var data = out && out.data;
          dlog('[header] /profile result', res && res.status, data);
          if (!res || !res.ok) return;
          var roles = Array.isArray(data && data.roles) ? data.roles : [];
          dlog('[header] roles from profile:', roles);
          if (roles.indexOf('admin') !== -1) {
            el.style.display = '';
            el.setAttribute('data-protected', '');
            dlog('[header] admin role detected — showing Admin link');
          } else {
            dlog('[header] non-admin user — Admin link stays hidden');
          }
        })
        .catch(function (e) {
          dlog('[header] /profile failed', e);
        });
    }

    // Hook header logout button to centralized auth.logout and show/hide it based on auth state
    function initHeaderLogout() {
      const logoutBtn = document.getElementById('logout-btn');
      if (!logoutBtn) return;
      // Show or hide the logout button depending on auth state
      try {
        const hasAuthFn = window.auth && typeof window.auth.hasAuth === 'function' && window.auth.hasAuth;
        const visible = !!(hasAuthFn && hasAuthFn());
        if (visible) {
          logoutBtn.style.display = '';
        } else {
          // If cookies are HttpOnly we can't see them via document.cookie. Probe the server.
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
                logoutBtn.style.display = '';
                return;
              }
            } catch (e) {}
            logoutBtn.style.display = 'none';
          })();
        }
      } catch {
        // fallback: hide
        logoutBtn.style.display = 'none';
      }
      logoutBtn.addEventListener('click', (e) => {
        e.preventDefault();
        // Hide the button immediately so UI reflects logged-out state
        try { logoutBtn.style.display = 'none'; } catch {}
        try {
          if (window.auth && typeof window.auth.logout === 'function') {
            // Some variants of auth.logout may not redirect; perform redirect here
            Promise.resolve(window.auth.logout()).finally(() => {
              window.location.href = 'login.html';
            });
          } else {
            // As a fallback, perform a simple POST to /logout then redirect
            (async function () {
              try {
                const base = window.BACKEND_BASE_URL || '';
                const trimmedBase = String(base).replace(/\/+$/, '');
                await fetch((trimmedBase || '') + '/logout', { method: 'POST', credentials: 'include' });
              } catch {}
              window.location.href = 'login.html';
            })();
          }
        } catch {
          window.location.href = 'login.html';
        }
      });
    }
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', initHeaderLogout, { once: true });
    } else {
      initHeaderLogout();
    }

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function(){ revealAdminIfAllowed(0); }, { once: true });
    } else {
      revealAdminIfAllowed(0);
    }
  } catch (e) {
    if (window && window.DEBUG_BANNER) try{ console.error('Header init failed', e);}catch{}
  }
})();
