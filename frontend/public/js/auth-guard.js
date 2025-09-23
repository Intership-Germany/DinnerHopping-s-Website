// Early auth guard utility for protected pages.
// Include this as early as possible in <head> before rendering content.
(function(){
  try {
    var authed = /(?:^|; )dh_token=/.test(document.cookie);
    var isLogin = (location.pathname.split('/').pop() || '') === 'login.html';
    if (!authed && !isLogin) {
      location.replace('login.html');
    }
  } catch (e) {
    console.error('auth-guard error', e);
  }
})();
