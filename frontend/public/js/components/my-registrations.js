/*
  * Dinner Hopping - frontend
  * Version: 1.0.0 
  * Purpose: "My registrations" component 



*/

(function () {
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  const C = (window.dh.components = window.dh.components || {});
  const U = (window.dh.utils = window.dh.utils || {});

  // ---------- Providers helpers ----------
  let __providersPromise = null;
  async function loadProviders() {
    if (!window.dh?.apiGet) return { providers: ['paypal', 'stripe', 'wero'], default: 'paypal' };
    try {
      const { res, data } = await window.dh.apiGet('/payments/providers');
      if (res.ok) {
        if (Array.isArray(data?.providers))
          return { providers: data.providers, default: data.default || data.providers[0] };
        if (Array.isArray(data)) return { providers: data, default: data[0] };
      }
    } catch {}
    return { providers: ['paypal', 'stripe', 'wero'], default: 'paypal' };
  }
  function getProviders() {
    if (!__providersPromise) __providersPromise = loadProviders();
    return __providersPromise;
  }

  // ---------- Status helpers ----------
  function computeStatus(reg) {
    if (!reg)
      return { label: 'registered', cancelled: false, refunded: false, refundPending: false };
    const s = (reg.status || '').toLowerCase();
    const pay = (reg.payment_status || reg.payment?.status || '').toLowerCase();
    const refunded = s === 'refunded' || pay === 'refunded';
    const cancelled = [
      'cancelled',
      'cancelled_by_user',
      'cancelled_admin',
      'expired',
      'refunded',
    ].includes(s);
    let label;
    if (refunded) label = 'Refunded';
    else if (s === 'cancelled_by_user') label = 'Cancelled (you)';
    else if (s === 'cancelled_admin') label = 'Cancelled (organizer)';
    else if (s === 'expired') label = 'Cancelled (expired)';
    else if (cancelled) label = 'Cancelled';
    else if (['paid', 'succeeded'].includes(pay) || ['paid', 'succeeded'].includes(s))
      label = 'Paid';
    else if (s) label = s;
    else label = 'registered';
    return { label, cancelled, refunded, refundPending: !!reg.refund_flag && !refunded };
  }
  function applyBadge(el, meta) {
    if (!el || !meta) return;
    el.textContent = meta.label;
    if (meta.cancelled || meta.refunded) {
      el.classList.add('bg-red-600', 'text-white');
    } else if (meta.label.toLowerCase() === 'paid') {
      el.classList.add('bg-green-600', 'text-white');
    }
  }

  // ---------- Fetch helpers (new backend endpoints) ----------
  async function fetchRegistrations() {
    if (!window.dh?.apiGet) return [];
    try {
      const { res, data } = await window.dh.apiGet('/registrations/registration-status');
      if (!res.ok) return [];
      const regs = data?.registrations || [];
      return Array.isArray(regs) ? regs : [];
    } catch {
      return [];
    }
  }
  async function fetchActiveEvents() {
    if (!window.dh?.apiGet) return [];
    try {
      const { res, data } = await window.dh.apiGet('/registrations/events/active');
      if (res.ok && Array.isArray(data)) return data;
    } catch {}
    return [];
  }
  async function fetchEventDetail(id) {
    if (!window.dh?.apiGet) return null;
    try {
      const { res, data } = await window.dh.apiGet(`/events/${encodeURIComponent(id)}`);
      if (res.ok) return data;
    } catch {}
    return null;
  }

  // ---------- Payment initiation ----------
  async function chooseProviderAndCreatePayment(regId) {
    if (!regId) return;
    let { providers, default: def } = await getProviders();
    if (!providers.length) {
      providers = ['wero'];
      def = 'wero';
    }
    let provider = def;
    if (providers.length > 1) {
      provider = await new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className =
          'fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4';
        const card = document.createElement('div');
        card.className = 'bg-white rounded-lg shadow-xl w-full max-w-sm p-5 space-y-3';
        card.innerHTML =
          '<h3 class="text-sm font-semibold text-gray-700">Select Payment Provider</h3>';
        const list = document.createElement('div');
        list.className = 'space-y-2';
        providers.forEach((p) => {
          const b = document.createElement('button');
          b.type = 'button';
          b.dataset.provider = p;
          b.className =
            'w-full px-3 py-2 rounded border text-sm flex items-center justify-between hover:bg-gray-50';
          b.innerHTML = `<span class="capitalize">${p}</span>${p === def ? '<span class="text-xs text-teal-600">(default)</span>' : ''}`;
          list.appendChild(b);
        });
        const cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.className =
          'w-full mt-2 px-3 py-2 rounded bg-gray-200 hover:bg-gray-300 text-gray-800 text-sm';
        cancel.textContent = 'Cancel';
        card.appendChild(list);
        card.appendChild(cancel);
        overlay.appendChild(card);
        document.body.appendChild(overlay);
        list.addEventListener('click', (ev) => {
          const b = ev.target.closest('button[data-provider]');
          if (!b) return;
          const p = b.dataset.provider;
          overlay.remove();
          resolve(p);
        });
        cancel.addEventListener('click', () => {
          overlay.remove();
          resolve(null);
        });
      });
      if (!provider) return;
    }
    try {
      const { res, data } = await window.dh.apiPost('/payments', {
        registration_id: regId,
        provider,
      });
      if (!res.ok) throw new Error('Payment creation failed');
      if (data.status === 'no_payment_required') {
        alert('No payment required.');
        return;
      }
      let link = null;
      if (data.next_action) {
        if (data.next_action.type === 'redirect') link = data.next_action.url;
        else if (data.next_action.type === 'paypal_order') link = data.next_action.approval_link;
      }
      if (!link) link = data.payment_link;
      if (!link && data.instructions)
        link = data.instructions.approval_link || data.instructions.link || null;
      if (link)
        window.location.assign(link.startsWith('http') ? link : window.BACKEND_BASE_URL + link);
      else if (data.instructions) alert('Instructions generated. Follow bank transfer steps.');
      else alert('Payment initiated. Follow provider instructions.');
    } catch (e) {
      alert(e.message || 'Payment failed');
    }
  }

  // ---------- Render logic ----------
  async function renderMyRegistrations(passedEvents) {
    const wrap = document.getElementById('my-registrations');
    const list = document.getElementById('my-registrations-list');
    if (!wrap || !list) return;
    list.innerHTML = '';
    const tpl = document.getElementById('tpl-myreg-card');
    if (!tpl) {
      wrap.classList.add('hidden');
      return;
    }
    const tpl = document.getElementById('tpl-myreg-card');
    if (!tpl) { wrap.classList.add('hidden'); return; }
    // Build map of events by id for quick lookup
    const evMap = new Map();
    events.forEach(e => {
      const id = e.id || e._id || e.eventId;
      if (id) evMap.set(String(id), e);
    });
    // LocalStorage snapshots removed per request (no longer persisting registration data client-side)
    // Instead we only rely on events returned by the API (e.g., /events/?participant=me).
    // If in the future an API to list detailed registrations (with payment / preferences) exists,
    // adapt here to merge that data. For now, we treat membership as a simple "registered" state.
    const combined = [];
    evMap.forEach((ev) => combined.push({ event: ev, regInfo: null }));

    // Partition future/past by event start/date
    const nowTs = Date.now();
    const future = [];
    const past = [];
    combined.forEach((c) => {
      const ev = c.event;
      let ts = null;
      if (ev.start_at) ts = new Date(ev.start_at).getTime();
      else if (ev.date) ts = new Date(ev.date + 'T23:59:59').getTime();
      if (ts && ts < nowTs) past.push(c);
      else future.push(c);
    });

    function renderRecord(ev, regInfo) {
      const node = tpl.content.cloneNode(true);
      const titleEl = node.querySelector('.reg-title');
      const dateEl = node.querySelector('.reg-date');
      const badge = node.querySelector('.reg-badge');
      const note = node.querySelector('.reg-note');
      const btnPay = node.querySelector('.reg-pay');
      const spanPaymentId = node.querySelector('.reg-payment-id');
      const prefsEl = node.querySelector('.reg-prefs');
      const aGo = node.querySelector('.reg-go');
      const eventId = ev.id || ev._id || ev.eventId;
      if (aGo) aGo.href = `/event.html?id=${encodeURIComponent(eventId)}`;
      if (titleEl) titleEl.textContent = ev.title || ev.name || regInfo.event_title || 'Event';
      if (dateEl) {
        const d = ev.start_at ? new Date(ev.start_at) : ev.date ? new Date(ev.date) : null;
        dateEl.textContent = d && !isNaN(d) ? d.toLocaleString() : '';
      }
      if (prefsEl) prefsEl.textContent = 'Preferences: n/a';

      const meta = computeStatus(regInfo);
      applyBadge(badge, meta);
      const amountDue =
        typeof regInfo.amount_due_cents === 'number' ? regInfo.amount_due_cents : null;
      const isPaid = meta.label.toLowerCase() === 'paid';
      if (meta.cancelled || meta.refunded) {
        if (note) {
          note.classList.remove('hidden');
          note.textContent = meta.label;
          note.classList.add('text-red-600');
        }
      } else if (amountDue && amountDue > 0 && !isPaid) {
        if (note) {
          note.classList.remove('hidden');
          note.innerHTML = 'Payment pending.';
        }
        if (btnPay) {
          btnPay.classList.remove('hidden');
          btnPay.addEventListener('click', () =>
            chooseProviderAndCreatePayment(regInfo.registration_id)
          );
        }
      }
      if (isPaid) {
        btnPay && btnPay.classList.add('hidden');
        note && note.classList.add('hidden');
      }
      list.appendChild(node);
    }

    future
      .sort((a, b) => {
        const ta = a.event.start_at ? new Date(a.event.start_at).getTime() : 0;
        const tb = b.event.start_at ? new Date(b.event.start_at).getTime() : 0;
        return ta - tb;
      })
      .forEach((c) => renderRecord(c.event, c.reg));
    if (past.length) {
      const wrapPast = document.createElement('div');
      wrapPast.className = 'mt-4';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'text-xs text-gray-600 hover:text-gray-800 underline';
      btn.textContent = `Show past events (${past.length})`;
      const pastList = document.createElement('div');
      pastList.className = 'mt-2 hidden';
      past
        .sort((a, b) => {
          const ta = a.event.start_at ? new Date(a.event.start_at).getTime() : 0;
          const tb = b.event.start_at ? new Date(b.event.start_at).getTime() : 0;
          return tb - ta;
        })
        .forEach((c) => renderRecord(c.event, c.reg));
      // move rendered nodes into pastList
      const nodes = Array.from(list.querySelectorAll(':scope > .past-temp-marker')); // legacy placeholder (noop)
      // toggle logic
      btn.addEventListener('click', () => {
        const hidden = pastList.classList.contains('hidden');
        pastList.classList.toggle('hidden', !hidden);
        btn.textContent = hidden
          ? `Hide past events (${past.length})`
          : `Show past events (${past.length})`;
      });
      wrapPast.appendChild(btn);
      wrapPast.appendChild(pastList);
      list.appendChild(wrapPast);
    }
    wrap.classList.remove('hidden');
  }

  C.renderMyRegistrations = renderMyRegistrations;
})();
