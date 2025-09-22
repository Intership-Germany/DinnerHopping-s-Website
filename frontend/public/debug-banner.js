(function(){
  try {
    if (typeof window !== 'undefined' && window.DEBUG_BANNER === false) {
      return; // opt-out via env flag
    }
    var origin = window.location.origin;
    var backend = (typeof window !== 'undefined' && window.BACKEND_BASE_URL) || 'n/a';
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
    el.textContent = 'Origin: ' + origin + '  |  Backend: ' + backend;
    document.addEventListener('DOMContentLoaded', function(){
      document.body.appendChild(el);
    });
  } catch (e) {
    // no-op
  }
})();
