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
        authed = /(?:^|; )dh_token=/.test(document.cookie);
      }
    } else {
      authed = /(?:^|; )dh_token=/.test(document.cookie);
    }
    dlog('[header] authed=', authed, 'mode=', (function(){
      try{
        var ls = localStorage.getItem('dh_access_token');
        return ls ? 'bearer-ls' : '__cookies_unknown__';
      }catch{return '__unknown__'}
    })());

    if (!authed) {
      document
        .querySelectorAll('a[data-protected]')
        .forEach((a) => a.setAttribute('href', 'login.html'));
    }

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

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function(){ revealAdminIfAllowed(0); }, { once: true });
    } else {
      revealAdminIfAllowed(0);
    }
  } catch (e) {
    if (window && window.DEBUG_BANNER) try{ console.error('Header init failed', e);}catch{}
  }
})();
