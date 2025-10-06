(function () {
  // Admin-only route guard: ensures the current user has the 'admin' role
  // Redirects non-admins (or unauthenticated users) away from admin pages.
  try {
    function redirectAway() {
      try {
        // Prefer home; if not available, fallback to login
        var target = 'index.html';
        if (!/index\.html$/i.test(target)) target = 'index.html';
        window.location.replace(target);
      } catch {
        window.location.href = 'index.html';
      }
    }

    // Wait until the HTTP client and backend base URL are available
    function whenReady(cb, attempts) {
      attempts = attempts || 0;
      var ready =
        typeof window !== 'undefined' &&
        window.dh &&
        typeof window.dh.apiGet === 'function' &&
        window.BACKEND_BASE_URL !== undefined;
      if (ready) return cb();
      if (attempts > 80) return cb(); // give up waiting after ~4s
      setTimeout(function () {
        whenReady(cb, attempts + 1);
      }, 50);
    }

    whenReady(function () {
      try {
        // Use cookie or bearer automatically via client
        window.dh
          .apiGet('/profile')
          .then(function (out) {
            var res = out && out.res;
            var data = out && out.data;
            if (!res || !res.ok) return redirectAway();
            var roles = Array.isArray(data && data.roles) ? data.roles : [];
            if (roles.indexOf('admin') === -1) return redirectAway();
          })
          .catch(function () {
            redirectAway();
          });
      } catch (e) {
        redirectAway();
      }
    });
  } catch (e) {
    // On any unexpected error, fail closed (redirect)
    try {
      window.location.replace('index.html');
    } catch {}
  }
})();
