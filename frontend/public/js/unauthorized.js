(function(){
  if (typeof window === 'undefined') return;
  function ensureBanner(){
    var existing = document.getElementById('dh-unauth-banner');
    if (existing) return existing;
    var wrap = document.createElement('div');
    wrap.id = 'dh-unauth-banner';
    // Tailwind-style classes; inline styles for safety if Tailwind not yet parsed
    wrap.className = 'fixed top-0 left-0 right-0 z-50';
    wrap.style.position = 'fixed';
    wrap.style.top = '0';
    wrap.style.left = '0';
    wrap.style.right = '0';
    wrap.style.zIndex = '9999';

    var inner = document.createElement('div');
    inner.className = 'mx-auto max-w-5xl px-4 py-3';

    var box = document.createElement('div');
    box.className = 'rounded-xl border border-red-300 bg-red-50 text-red-800 px-4 py-3 shadow';

    var content = document.createElement('div');
    content.className = 'flex flex-col md:flex-row md:items-center md:justify-between gap-2';

    var msg = document.createElement('div');
    msg.className = 'text-sm font-medium';
    msg.textContent = 'Your session has expired. Please log in again.';

    var actions = document.createElement('div');

    var btn = document.createElement('a');
    btn.className = 'inline-block rounded-lg bg-[#f46f47] text-white text-sm font-semibold px-4 py-2 hover:opacity-95 transition';
    btn.href = 'login.html';
    btn.textContent = 'Log in again';

    actions.appendChild(btn);
    content.appendChild(msg);
    content.appendChild(actions);
    box.appendChild(content);
    inner.appendChild(box);
    wrap.appendChild(inner);

    document.addEventListener('DOMContentLoaded', function(){
      document.body.appendChild(wrap);
    });
    if (document.readyState !== 'loading') {
      try { document.body.appendChild(wrap); } catch(e) { /* ignore */ }
    }
    return wrap;
  }

  function buildNext(){
    try {
      var p = location.pathname + (location.search || '');
      return encodeURIComponent(p || '/');
    } catch { return encodeURIComponent('/'); }
  }

  function deleteTokenCookie(){
    try {
      if (window.auth && window.auth.deleteTokenCookie) {
        window.auth.deleteTokenCookie();
        return;
      }
      if (window.auth && window.auth.deleteCookie) {
        window.auth.deleteCookie('dh_token');
        return;
      }
      var secure = (location && location.protocol === 'https:') ? '; Secure' : '';
      document.cookie = 'dh_token=; Path=/; SameSite=Strict' + secure + '; Expires=Thu, 01 Jan 1970 00:00:00 GMT';
    } catch {}
  }

  window.handleUnauthorized = function(opts){
    opts = opts || {};
    var banner = ensureBanner();
    // Update CTA href with next param
    try {
      var a = banner.querySelector('a');
      if (a) {
        var url = 'login.html?next=' + buildNext();
        a.setAttribute('href', url);
      }
    } catch {}

    deleteTokenCookie();

    // Optional auto-redirect after a brief delay
    var shouldRedirect = (typeof opts.autoRedirect === 'boolean') ? opts.autoRedirect : true;
    if (shouldRedirect) {
      setTimeout(function(){
        try { window.location.href = 'login.html?next=' + buildNext(); } catch {}
      }, typeof opts.delayMs === 'number' ? Math.max(100, opts.delayMs) : 1600);
    }
  };
})();
