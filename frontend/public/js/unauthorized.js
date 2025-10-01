// Simple unauthorized handler used by legacy pages
(function(){
  if (typeof window === 'undefined') return;
  if (window.handleUnauthorized) return; // don't overwrite if already defined
  window.handleUnauthorized = function(opts){
    try {
      const o = opts || {};
      const delay = typeof o.delayMs === 'number' ? o.delayMs : 800;
      if (o.autoRedirect !== false){
        setTimeout(()=>{ window.location.href = 'login.html'; }, delay);
      }
    } catch(e){
      console.error('handleUnauthorized error', e);
      window.location.href = 'login.html';
    }
  };
})();
