// Early auth guard utility for protected pages.
// Include this as early as possible in <head> before rendering content.
// Avoid the user seeing protected content flash before redirecting.
(function(){
  try {
    var isLoginPage = (location.pathname.split('/').pop() || '') === 'login.html';
    var authed = false;
    if (window.auth && typeof window.auth.hasAuth === 'function') {
      authed = window.auth.hasAuth();
    } else {
      authed = /(?:^|; )dh_token=/.test(document.cookie); // fallback
    }
    if (!authed && !isLoginPage) {
      location.replace('login.html');
    }
  } catch (e) {
    console.error('auth-guard error', e);
  }
})();
