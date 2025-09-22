// Simple auth utilities for cookie-based auth
// - Uses SameSite=Strict by default to avoid sending to other websites
// - Adds Secure attribute automatically on HTTPS
// - Provides enforceLogin() to guard pages
(function(){
  function isHttps(){
    return typeof location !== 'undefined' && location.protocol === 'https:';
  }
  function setCookie(name, value, days){
    const maxAge = days ? `; Max-Age=${Math.floor(days*86400)}` : '';
    const attrs = `Path=/; SameSite=Strict${isHttps()?'; Secure':''}${maxAge}`;
    document.cookie = `${name}=${encodeURIComponent(value)}; ${attrs}`;
  }
  function getCookie(name){
    const m = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/[.$?*|{}()\[\]\\\/\+^]/g, '\\$&') + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : null;
  }
  function deleteCookie(name){
    document.cookie = `${name}=; Path=/; SameSite=Strict${isHttps()?'; Secure':''}; Expires=Thu, 01 Jan 1970 00:00:00 GMT`;
  }
  function hasAuth(){
    return !!getCookie('dh_token');
  }
  function enforceLogin(loginUrl){
    if (!hasAuth()) {
      window.location.href = loginUrl || 'login.html';
    }
  }
  if (typeof window !== 'undefined'){
    window.auth = { setCookie, getCookie, deleteCookie, hasAuth, enforceLogin };
  }
})();
