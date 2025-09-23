// Header partial behavior: protect links when not authenticated.
(function(){
  try {
    var authed = /(?:^|; )dh_token=/.test(document.cookie);
    if (authed) return;
    document.querySelectorAll('a[data-protected]')
      .forEach(function(a){ a.setAttribute('href', 'login.html'); });
  } catch (e) {
    console.error('Auth guard failed', e);
  }
})();
