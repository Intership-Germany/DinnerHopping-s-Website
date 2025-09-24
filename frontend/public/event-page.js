document.addEventListener('DOMContentLoaded', async function () {
  // Populate debug elements from backend
  (async function populateDebug(){
    try { if (window.initCsrf) await window.initCsrf(); } catch {}
    const BASE = (typeof window !== 'undefined' && window.BACKEND_BASE_URL) || 'http://localhost:8000';
    const prefsEl = document.getElementById('debug-global-prefs');
    const methodEl = document.getElementById('debug-payment-method');

    // Helper to format preferences similar to profile.js
    function formatPreferences(pref){
      try {
        if (!pref) return '';
        if (Array.isArray(pref)) return pref.join(', ');
        if (typeof pref === 'string') return pref;
        if (typeof pref === 'object'){
          if (Array.isArray(pref.tags)) return pref.tags.join(', ');
          const parts = [];
          for (const [k,v] of Object.entries(pref)){
            if (k === 'tags') continue;
            if (v === true) parts.push(k);
            else if (Array.isArray(v)) parts.push(`${k}: ${v.join(', ')}`);
            else if (typeof v === 'string' && v.trim()) parts.push(`${k}: ${v}`);
            else if (typeof v === 'number') parts.push(`${k}: ${v}`);
          }
          return parts.join(', ');
        }
        return String(pref);
      } catch { return ''; }
    }

    // Load profile prefs
    try {
      const res = await (window.apiFetch ? window.apiFetch('/profile') : fetch(`${BASE}/profile`, { credentials: 'include' }));
      const data = typeof res.json === 'function' ? await res.json() : await res.then(r=>r.json());
      if (prefsEl) prefsEl.textContent = formatPreferences(data?.preferences) || '(none)';
    } catch (e){ if (prefsEl) prefsEl.textContent = 'error'; }

    // Load payment providers to get default
    try {
      const res2 = await (window.apiFetch ? window.apiFetch('/payments/providers') : fetch(`${BASE}/payments/providers`, { credentials: 'include' }));
      const data2 = typeof res2.json === 'function' ? await res2.json() : await res2.then(r=>r.json());
      if (methodEl) methodEl.textContent = (data2 && (data2.default || (data2.providers||[])[0])) || 'unknown';
    } catch (e){ if (methodEl) methodEl.textContent = 'error'; }
  })();

  if (typeof L === 'undefined') return;
  try {
    var entreeMap = L.map('map-entree', { zoomControl: true, attributionControl: false });
    entreeMap.setView([51.5413, 9.9345], 14);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      minZoom: 13, maxZoom: 18
    }).addTo(entreeMap);
    L.circle([51.5413, 9.9345], { radius: 500, color: '#008080', fillColor: '#008080', fillOpacity: 0.08, weight: 2, dashArray: '6 6' }).addTo(entreeMap);
    L.marker([51.5413, 9.9345], { icon: L.icon({ iconUrl: 'https://cdn.jsdelivr.net/gh/pointhi/leaflet-color-markers@master/img/marker-icon-red.png', iconSize: [25, 41], iconAnchor: [12, 41] }) }).addTo(entreeMap);

    var dessertMap = L.map('map-dessert', { zoomControl: true, attributionControl: false });
    dessertMap.setView([51.5432, 9.9367], 14);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      minZoom: 13, maxZoom: 18
    }).addTo(dessertMap);
    L.circle([51.5432, 9.9367], { radius: 500, color: '#ffc241', fillColor: '#ffc241', fillOpacity: 0.08, weight: 2, dashArray: '6 6' }).addTo(dessertMap);
    L.marker([51.5432, 9.9367], { icon: L.icon({ iconUrl: 'https://cdn.jsdelivr.net/gh/pointhi/leaflet-color-markers@master/img/marker-icon-yellow.png', iconSize: [25, 41], iconAnchor: [12, 41] }) }).addTo(dessertMap);
  } catch (e) {
    console.error('Failed to init maps', e);
  }
});



