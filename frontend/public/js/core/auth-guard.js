// Early auth guard for protected pages (core)
(function () {
  try {
    const page = location.pathname.split('/').pop() || '';
    const isLogin = page === 'login.html';
    let authed = false;
    if (window.auth && typeof window.auth.hasAuth === 'function') {
      authed = window.auth.hasAuth();
    } else {
      authed = /(?:^|; )dh_token=/.test(document.cookie);
    }
    if (!authed && !isLogin) {
      location.replace('login.html');
    }
  } catch (e) {
    console.error('auth-guard error', e);
  }
})();
