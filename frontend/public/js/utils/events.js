(function(){
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {}; const utils = window.dh.utils = window.dh.utils || {};

  async function fetchPublishedEvents(){
    const api = window.dh.apiFetch || window.apiFetch;
    const res = await api('/events/', { method: 'GET', headers:{ Accept: 'application/json' } });
    if (!res.ok) throw Object.assign(new Error('Events load failed'), { status: res.status });
    return res.json();
  }
  async function fetchMyEvents(){
    const api = window.dh.apiFetch || window.apiFetch;
    const res = await api('/events/?participant=me', { method: 'GET', headers:{ Accept: 'application/json' } });
    if (!res.ok) return [];
    return res.json();
  }

  utils.fetchPublishedEvents = fetchPublishedEvents;
  utils.fetchMyEvents = fetchMyEvents;
})();
