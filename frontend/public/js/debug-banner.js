// Debug banner utility: shows current frontend origin and backend URL in a small
// fixed banner at the bottom-left of the screen. Opt-in via config.js
// window.DEBUG_BANNER = true before loading this script. Opt-out via
// setting window.DEBUG_BANNER = false (overrides true).

(function(){
  try {
    if (typeof window !== 'undefined' && window.DEBUG_BANNER === false) {
      return; // opt-out via env flag
    }
  var origin = window.location.origin;
  var backend = (typeof window !== 'undefined') ? window.BACKEND_BASE_URL : undefined; // fallback removed
  var el = document.createElement('div');
    el.setAttribute('id', 'dh-debug-banner');
    el.style.position = 'fixed';
    el.style.bottom = '10px';
    el.style.left = '10px';
    el.style.zIndex = '9999';
    el.style.fontFamily = 'Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial';
    el.style.fontSize = '12px';
    el.style.padding = '6px 10px';
    el.style.borderRadius = '10px';
    el.style.background = 'rgba(23,42,58,0.9)';
    el.style.color = 'white';
    el.style.boxShadow = '0 4px 12px rgba(0,0,0,0.25)';
    el.style.backdropFilter = 'blur(4px)';
    el.style.opacity = '0.8';
    el.style.pointerEvents = 'none';
    function render(){
      var current = (typeof window !== 'undefined') ? window.BACKEND_BASE_URL : undefined;
      el.textContent = 'Origin: ' + origin + '  |  Backend: ' + (current === undefined ? 'undefined' : current);
    }
    render();
    document.addEventListener('DOMContentLoaded', function(){
      document.body.appendChild(el);
      // Si au moment de l'attachement la valeur n'est pas encore définie, on réessaie quelques fois
      if (window.BACKEND_BASE_URL === undefined) {
        var attempts = 0;
        var maxAttempts = 10;
        var iv = setInterval(function(){
          attempts++;
            if (window.BACKEND_BASE_URL !== undefined) {
              render();
              clearInterval(iv);
            } else if (attempts >= maxAttempts) {
              console.warn('[debug-banner] BACKEND_BASE_URL toujours undefined après', attempts, 'tentatives');
              clearInterval(iv);
            }
        }, 200);
      }
    });
  } catch (e) {
    console.error('debug-banner error', e);
  }
})();
