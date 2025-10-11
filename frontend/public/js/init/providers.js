(function () {
  async function populateProviderTemplate() {
    try {
      const tpl = document.getElementById('tpl-provider-modal');
      if (!tpl || !tpl.content) return;
      const container = tpl.content.querySelector('.providers-list');
      if (!container) return;
      let providers = ['paypal', 'stripe'];
      // Prefer existing frontend helper if available
      try {
        if (window.dh?.apiGet) {
          const { res, data } = await window.dh.apiGet('/payments/providers');
          if (res && res.ok) {
            if (Array.isArray(data?.providers)) providers = data.providers;
            else if (Array.isArray(data)) providers = data;
          }
        } else {
          const base = window.BACKEND_BASE_URL || '';
          const r = await fetch(base + '/payments/providers', { headers: { Accept: 'application/json' } });
          if (r.ok) {
            const json = await r.json();
            if (Array.isArray(json?.providers)) providers = json.providers;
            else if (Array.isArray(json)) providers = json;
          }
        }
      } catch (err) {
        // ignore and fallback to defaults
      }

      // Clear any existing children then populate
      container.innerHTML = '';
      providers.forEach((p) => {
        const key = (p || '').toLowerCase();
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.setAttribute('data-provider', key);
        btn.className = 'w-full inline-flex items-center justify-center gap-2 px-4 py-3 rounded-xl border border-gray-200 hover:bg-gray-50';
        if (key === 'paypal') {
          btn.innerHTML = '<img alt="PayPal" src="https://www.paypalobjects.com/webstatic/icon/pp258.png" class="w-5 h-5" />' +
            '<span class="font-medium">Pay with PayPal</span>';
        } else if (key === 'stripe') {
          btn.innerHTML = '<svg viewBox="0 0 28 28" class="w-5 h-5" aria-hidden="true"><path fill="#635BFF" d="M.5 9.3l7.8-1.4v13.6c0 3.2-1.9 4.6-4.8 4.6-1.3 0-2.2-.3-3-1v-3.8c.6.3 1.3.5 2 .5.8 0 1.2-.3 1.2-1.2V9.3zM27.5 14.9c0-4.1-2.5-5.7-7.4-6.5-3.5-.6-4.2-1-4.2-2 0-.8.8-1.4 2.2-1.4 1.3 0 2.6.3 3.9.8l.6-4c-1.5-.5-3.1-.8-4.7-.8-4 0-6.8 2.1-6.8 5.5 0 3.8 2.5 5.2 6.8 6 3.3.6 4.2 1.1 4.2 2.1 0 1-1 1.6-2.5 1.6-1.6 0-3.3-.4-4.8-1.1l-.7 4.1c1.8.7 3.8 1 5.7 1 4.2 0 7.7-2.1 7.7-5.8z"/></svg>' +
+            '<span class="font-medium">Pay with Stripe</span>';
        } else {
          btn.innerHTML = `<span class="font-medium">Pay with ${key.charAt(0).toUpperCase()}${key.slice(1)}</span>`;
        }
        container.appendChild(btn);
      });
    } catch (e) {
      // no-op on template population failure
      console.error('Could not populate payment providers template', e);
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', populateProviderTemplate);
  else populateProviderTemplate();
})();
