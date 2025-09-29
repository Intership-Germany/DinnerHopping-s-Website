// Header partial behavior: protect links when unauthenticated
(function () {
  try {
    let authed = false;
    if (window.auth && typeof window.auth.hasAuth === 'function') {
      authed = window.auth.hasAuth();
    } else {
      authed = /(?:^|; )dh_token=/.test(document.cookie);
    }
    if (authed) return;
    document
      .querySelectorAll('a[data-protected]')
      .forEach((a) => a.setAttribute('href', 'login.html'));
  } catch (e) {
    console.error('Auth guard failed', e);
  }
})();
