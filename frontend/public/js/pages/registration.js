/**
 * Registration listing page (formerly registration.js)
 * Shows active events via /registrations/events/active and provides
 * quick solo/team registration shortcuts.
 */
(function () {
  const BASE = window.BACKEND_BASE_URL;
  function el(tag, attrs, ...children) {
    const n = document.createElement(tag);
    if (attrs) {
      Object.entries(attrs).forEach(([k, v]) => {
        if (k === 'class') n.className = v;
        else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
        else n.setAttribute(k, v);
      });
    }
    children.flat().forEach((ch) => {
      if (ch == null) return;
      n.appendChild(typeof ch === 'string' ? document.createTextNode(ch) : ch);
    });
    return n;
  }
  async function fetchActiveEvents() {
    const api =
      window.dh && window.dh.apiFetch
        ? window.dh.apiFetch
        : (p, opts) => fetch(BASE + p, { ...(opts || {}), credentials: 'include' });
    const res = await api('/registrations/events/active', { method: 'GET' });
    if (!res.ok) throw new Error('Failed to load events');
    return await res.json();
  }
  async function startSolo(eventId) {
    try {
      const api =
        window.dh && window.dh.apiFetch
          ? window.dh.apiFetch
          : (p, opts) => fetch(BASE + p, { ...(opts || {}), credentials: 'include' });
      const res = await api('/registrations/solo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event_id: eventId }),
      });
      const data = await res.json();
      if (!res.ok) {
        // Handle 409 conflict for existing active registration
        if (res.status === 409 && data.existing_registration) {
          const existing = data.existing_registration;
          alert(
            `${data.message || 'You already have an active registration.'}\n\n` +
            `Event: ${existing.event_title || 'Unknown'}\n` +
            `Status: ${existing.status || 'Unknown'}\n\n` +
            `Please cancel that registration first, or wait until it completes.`
          );
          return;
        }
        alert(data.detail || data.message || 'Failed to register');
        return;
      }
      // Determine provider(s)
      let providers = ['paypal','stripe','wero']; let defaultProvider='paypal';
      try {
        const pr = await api('/payments/providers', { method: 'GET', headers:{'Accept':'application/json'} });
        if (pr.ok){ const provs = await pr.json(); if (provs?.providers) providers = provs.providers; else if (Array.isArray(provs)) providers = provs; if (typeof provs?.default==='string') defaultProvider = provs.default; }
      } catch {}
      const chosen = providers.length===1 ? providers[0] : defaultProvider;
  const payRes = await api('/payments', { method:'POST', headers:{ 'Content-Type':'application/json' }, body: JSON.stringify({ registration_id: data.registration_id, provider: chosen }) });
  const pay = await payRes.json();
  if (pay.status === 'no_payment_required') { alert('No payment required.'); return; }
  let link = null;
  if (pay.next_action){ if (pay.next_action.type==='redirect') link=pay.next_action.url; else if (pay.next_action.type==='paypal_order') link=pay.next_action.approval_link; }
  if (!link) link = pay.payment_link;
  if (!link && pay.instructions) link = pay.instructions.approval_link || pay.instructions.link || null;
  if (link) window.location.href = link.startsWith('http')? link: window.BACKEND_BASE_URL + link; else if (pay.instructions) alert('Instructions generated. Follow the bank transfer steps.'); else alert('Payment created. Please follow provider instructions.');
    } catch (e) {
      alert('Registration failed.');
    }
  }
  async function startTeam(eventId) {
    try {
      const api =
        window.dh && window.dh.apiFetch
          ? window.dh.apiFetch
          : (p, opts) => fetch(BASE + p, { ...(opts || {}), credentials: 'include' });
      const res = await api('/registrations/team', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event_id: eventId, cooking_location: 'creator' }),
      });
      const data = await res.json();
      if (!res.ok) {
        // Handle 409 conflict for existing active registration
        if (res.status === 409 && data.existing_registration) {
          const existing = data.existing_registration;
          alert(
            `${data.message || 'You already have an active registration.'}\n\n` +
            `Event: ${existing.event_title || 'Unknown'}\n` +
            `Status: ${existing.status || 'Unknown'}\n\n` +
            `Please cancel that registration first, or wait until it completes.`
          );
          return;
        }
        alert(data.detail || data.message || 'Failed to register team');
        return;
      }
      let providers = ['paypal','stripe','wero']; let defaultProvider='paypal';
      try {
        const pr = await api('/payments/providers', { method: 'GET', headers:{'Accept':'application/json'} });
        if (pr.ok){ const provs = await pr.json(); if (provs?.providers) providers = provs.providers; else if (Array.isArray(provs)) providers = provs; if (typeof provs?.default==='string') defaultProvider = provs.default; }
      } catch {}
      const chosen = providers.length===1 ? providers[0] : defaultProvider;
  const payRes = await api('/payments', { method:'POST', headers:{ 'Content-Type':'application/json' }, body: JSON.stringify({ registration_id: data.registration_id, provider: chosen }) });
  const pay = await payRes.json();
  if (pay.status === 'no_payment_required') { alert('Team created. No payment required.'); return; }
  let link = null;
  if (pay.next_action){ if (pay.next_action.type==='redirect') link=pay.next_action.url; else if (pay.next_action.type==='paypal_order') link=pay.next_action.approval_link; }
  if (!link) link = pay.payment_link;
  if (!link && pay.instructions) link = pay.instructions.approval_link || pay.instructions.link || null;
  if (link) window.location.href = link.startsWith('http')? link: window.BACKEND_BASE_URL + link; else if (pay.instructions) alert('Team created. Instructions generated for bank transfer.'); else alert('Team created. Payment pending.');
    } catch (e) {
      alert('Team registration failed.');
    }
  }
  async function init() {
    const list = document.getElementById('events-list');
    if (!list) return;
    try {
      const events = await fetchActiveEvents();
      if (!events.length) {
        list.appendChild(el('p', { class: 'text-gray-600' }, 'No active events right now.'));
        return;
      }
      events.forEach((ev) => {
        const row = el(
          'div',
          { class: 'p-3 border rounded mb-2 flex items-center justify-between' },
          el(
            'div',
            null,
            el('div', { class: 'font-semibold' }, ev.title || 'Event'),
            el('div', { class: 'text-xs text-gray-500' }, ev.date || ev.start_at || '')
          ),
          el(
            'div',
            { class: 'space-x-2' },
            el(
              'button',
              {
                class: 'px-3 py-1 bg-emerald-600 text-white rounded',
                onclick: () => startSolo(ev.id),
              },
              'Register Solo'
            ),
            el(
              'button',
              {
                class: 'px-3 py-1 bg-indigo-600 text-white rounded',
                onclick: () => startTeam(ev.id),
              },
              'Register Team'
            )
          )
        );
        list.appendChild(row);
      });
    } catch {
      list.appendChild(el('p', { class: 'text-red-600' }, 'Failed to load events.'));
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
  window.dh = window.dh || {};
  window.dh.pages = window.dh.pages || {};
  window.dh.pages.registration = { startSolo, startTeam };
})();
