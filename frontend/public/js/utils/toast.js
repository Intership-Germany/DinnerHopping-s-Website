// Simple toast utility for bottom-right notifications
(function(){
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  const containerId = 'dh-toast-container';
  function ensureContainer(){
    let c = document.getElementById(containerId);
    if (!c){
      c = document.createElement('div');
      c.id = containerId;
      c.className = 'fixed bottom-4 right-4 z-50 space-y-2 pointer-events-none';
      document.body.appendChild(c);
    }
    return c;
  }
  function colorBy(type){
    switch(type){
      case 'success': return 'bg-emerald-600';
      case 'warning': return 'bg-amber-600';
      case 'error': return 'bg-red-600';
      default: return 'bg-slate-800';
    }
  }
  function iconBy(type){
    switch(type){
      case 'success': return '✓';
      case 'warning': return '⚠';
      case 'error': return '⛔';
      default: return 'ℹ';
    }
  }
  function showToast(message, { type='info', duration=2500 }={}){
    const c = ensureContainer();
    const box = document.createElement('div');
    box.className = `${colorBy(type)} text-white text-sm shadow-lg rounded-lg px-3 py-2 flex items-center gap-2 pointer-events-auto`;
    box.innerHTML = `<span>${iconBy(type)}</span><span>${message}</span>`;
    c.appendChild(box);
    if (duration > 0){
      setTimeout(()=>{ try{ box.remove(); } catch(e){} }, duration);
    }
    return box;
  }
  // loading toasts that can be updated/closed
  function loading(message){
    const el = showToast(message, { type: 'info', duration: 0 });
    el.dataset.loading = '1';
    return {
      update(msg, type){
        if (!el || !el.parentNode) return;
        const t = el.querySelector('span:nth-child(2)');
        if (t) t.textContent = msg;
        el.className = `${colorBy(type||'info')} text-white text-sm shadow-lg rounded-lg px-3 py-2 flex items-center gap-2 pointer-events-auto`;
        const ic = el.querySelector('span:nth-child(1)');
        if (ic) ic.textContent = iconBy(type||'info');
      },
      close(delay=300){
        setTimeout(()=>{ try{ el.remove(); } catch(e){} }, delay);
      }
    };
  }
  window.dh.toast = showToast;
  window.dh.toastLoading = loading;
})();

