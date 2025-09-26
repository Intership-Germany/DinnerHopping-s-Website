// Simple auth utilities for cookie-based auth
// - Uses SameSite=Strict by default to avoid sending to other websites
// - Adds Secure attribute automatically on HTTPS
// - Provides enforceLogin() to guard pages
(function(){
  // Core low-level helpers (kept stable; other scripts rely on them)
  function isHttps(){
    return typeof location !== 'undefined' && location.protocol === 'https:';
  }
  function setCookie(name, value, days){
    // Max-Age expects SECONDS
    let maxAge = '';
    if (typeof days === 'number' && isFinite(days) && days > 0) {
      const secs = Math.max(1, Math.floor(days * 86400));
      maxAge = `; Max-Age=${secs}`;
    }
    const attrs = `Path=/; SameSite=Strict${isHttps()?'; Secure':''}${maxAge}`;
    document.cookie = `${name}=${encodeURIComponent(value)}; ${attrs}`;
  }
  function getCookie(name){
    // Escape regex special chars in name (minimal safe subset)
    const safe = name.replace(/[-\\^$*+?.()|[\]{}]/g, '\\$&');
    const m = document.cookie.match(new RegExp('(?:^|; )' + safe + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : null;
  }
  function deleteCookie(name){
    document.cookie = `${name}=; Path=/; SameSite=Strict${isHttps()?'; Secure':''}; Expires=Thu, 01 Jan 1970 00:00:00 GMT`;
  }
  function hasAuth(){
    return !!getCookie('dh_token');
  }
  function deleteTokenCookie(){
    deleteCookie('dh_token');
  }
  function enforceLogin(loginUrl){
    if (!hasAuth()) {
      window.location.href = loginUrl || 'login.html';
    }
  }
  if (typeof window !== 'undefined'){
    // Merge with any existing (defensive in case of multiple loads)
    window.auth = Object.assign({}, window.auth || {}, { setCookie, getCookie, deleteCookie, deleteTokenCookie, hasAuth, enforceLogin, isHttps });
  }
})();
