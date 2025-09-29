// Central network error display: wraps dh.apiFetch and dispatches 'dh:network-error'
(function () {
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  const origFetchInit = () => window.dh.apiFetch || window.apiFetch;
  function ensureWrapper() {
    const base = origFetchInit();
    if (!base || base.__dhWrapped) return;
    async function wrapped(path, opts) {
      try {
        const res = await base(path, opts);
        if (!res.ok && res.status >= 500) {
          window.dispatchEvent(
            new CustomEvent('dh:network-error', { detail: { path, status: res.status } })
          );
        }
        return res;
      } catch (e) {
        window.dispatchEvent(new CustomEvent('dh:network-error', { detail: { path, error: e } }));
        throw e;
      }
    }
    wrapped.__dhWrapped = true;
    window.dh.apiFetch = wrapped;
  }
  ensureWrapper(); // Simple default listener (page can override)
  if (!window.__dhErrorBanner) {
    window.__dhErrorBanner = true;
    window.addEventListener('dh:network-error', (ev) => {
      const id = 'dh-net-error-banner';
      let b = document.getElementById(id);
      if (!b) {
        b = document.createElement('div');
        b.id = id;
        b.className =
          'fixed bottom-4 right-4 max-w-sm bg-red-600 text-white text-sm shadow-lg rounded-lg p-3 z-50';
        document.body.appendChild(b);
      }
      const d = ev.detail || {};
      b.textContent = d.error
        ? 'Network error: ' + (d.error.message || 'unknown')
        : `Server error ${d.status} on ${d.path}`;
      setTimeout(() => {
        if (b) b.remove();
      }, 6000);
    });
  }
})();
