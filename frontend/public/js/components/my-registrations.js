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
  if (!window.dh?.apiGet) return { providers: ['paypal', 'stripe'], default: 'paypal' };
    try {
      const { res, data } = await window.dh.apiGet('/payments/providers');
      if (res.ok) {
        if (Array.isArray(data?.providers))
          return { providers: data.providers, default: data.default || data.providers[0] };
        if (Array.isArray(data)) return { providers: data, default: data[0] };
      }
    } catch {}
  return { providers: ['paypal', 'stripe'], default: 'paypal' };
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
    // Normalize invited/pending state to a friendly label
    if (s === 'invited' || s === 'pending') return { label: 'Invited', cancelled: false, refunded: false, refundPending: false };
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
    } else if (meta.label.toLowerCase() === 'invited' || meta.label === 'Invited') {
      el.classList.add('bg-blue-600', 'text-white');
    }
  }

  function isInvitedStatus(regInfo) {
    if (!regInfo) return false;
    const s = (regInfo.status || '').toLowerCase();
    return s === 'invited' || s === 'pending';
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
  async function fetchInvitations() {
    if (!window.dh?.apiGet) return [];
    try {
      const { res, data } = await window.dh.apiGet('/invitations/');
      if (!res.ok) return [];
      return Array.isArray(data) ? data : (Array.isArray(data?.invitations) ? data.invitations : []);
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
      alert('Online payments are currently unavailable. Please contact support to finalize payment.');
      return;
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
          const key = (p || '').toLowerCase();
          const b = document.createElement('button');
          b.type = 'button';
          b.dataset.provider = key;
          b.className = 'w-full px-3 py-2 rounded border text-sm flex items-center justify-between gap-2 hover:bg-gray-50';
          if (key === 'paypal') {
            b.innerHTML = '<div class="flex items-center gap-2"><img alt="PayPal" src="https://www.paypalobjects.com/webstatic/icon/pp258.png" class="w-5 h-5" /><span class="font-medium">Pay with PayPal</span></div>' + (key === def ? '<span class="text-xs text-teal-600">(default)</span>' : '');
          } else if (key === 'stripe') {
            b.innerHTML = '<div class="flex items-center gap-2"><svg viewBox="0 0 28 28" class="w-5 h-5" aria-hidden="true"><path fill="#635BFF" d="M.5 9.3l7.8-1.4v13.6c0 3.2-1.9 4.6-4.8 4.6-1.3 0-2.2-.3-3-1v-3.8c.6.3 1.3.5 2 .5.8 0 1.2-.3 1.2-1.2V9.3zM27.5 14.9c0-4.1-2.5-5.7-7.4-6.5-3.5-.6-4.2-1-4.2-2 0-.8.8-1.4 2.2-1.4 1.3 0 2.6.3 3.9.8l.6-4c-1.5-.5-3.1-.8-4.7-.8-4 0-6.8 2.1-6.8 5.5 0 3.8 2.5 5.2 6.8 6 3.3.6 4.2 1.1 4.2 2.1 0 1-1 1.6-2.5 1.6-1.6 0-3.3-.4-4.8-1.1l-.7 4.1c1.8.7 3.8 1 5.7 1 4.2 0 7.7-2.1 7.7-5.8z"/></svg><span class="font-medium">Pay with Stripe</span></div>' + (key === def ? '<span class="text-xs text-teal-600">(default)</span>' : '');
          } else {
            b.innerHTML = `<div class="flex items-center gap-2"><span class="font-medium">Pay with ${key.charAt(0).toUpperCase()}${key.slice(1)}</span></div>` + (key === def ? '<span class="text-xs text-teal-600">(default)</span>' : '');
          }
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
      const { res, data } = await window.dh.apiPost('/payments/create', {
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
          if (link)
            window.location.assign(link.startsWith('http') ? link : window.BACKEND_BASE_URL + link);
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
    // Load active events (for metadata) and real registrations for current user
    let events = Array.isArray(passedEvents) ? passedEvents : [];
    const [regs] = await Promise.all([
      fetchRegistrations(),
      (async () => {
        if (!events.length) {
          try {
            events = await fetchActiveEvents();
          } catch {}
        }
      })(),
    ]);
    // Map events by id for quick lookup when enriching registrations
    const evMap = new Map();
    events.forEach((e) => {
      const id = e.id || e._id || e.eventId;
      if (id) evMap.set(String(id), e);
    });
    // Build combined list from registrations (only show events the user is registered for)
    const combined = [];
    if (Array.isArray(regs) && regs.length) {
      // regs might already be the array, or {registrations: []} (handled in fetchRegistrations)
      for (const r of regs) {
        const rid = r && (r.event_id || r.eventId || r.eventIdStr);
        let ev = rid ? evMap.get(String(rid)) : null;
        if (!ev && rid) {
          // Try fetch detail for events not in the active list (e.g., past events)
          try {
            const detail = await fetchEventDetail(rid);
            if (detail) {
              ev = detail;
              evMap.set(String(rid), detail);
            }
          } catch {}
        }
        combined.push({ event: ev || { id: rid, title: r.event_title }, regInfo: r });
      }
    }

      // Also include standalone invitations (invitations that may not have a registration yet)
      let invs = [];
      try {
        invs = await fetchInvitations();
      } catch {}
      if (Array.isArray(invs) && invs.length) {
        for (const inv of invs) {
          // skip invitations already represented by a registration in the combined list
          const already = combined.some((c) => {
            const rr = c.regInfo || {};
            const rid = inv.registration_id || inv.registraton_id || inv.registrationId || inv.registration;
            if (!rid) return false;
            return (
              String(rr.registration_id || rr._id || rr.id || '') === String(rid) ||
              String(rr.id || rr._id || '') === String(rid)
            );
          });
          if (already) continue;
          const rid = inv.event_id;
          let ev = rid ? evMap.get(String(rid)) : null;
          if (!ev && rid) {
            try {
              const detail = await fetchEventDetail(rid);
              if (detail) {
                ev = detail;
                evMap.set(String(rid), detail);
              }
            } catch {}
          }
          const regLike = { status: inv.status || 'invited', invitation_id: inv.id || inv._id || inv.id_str, invitation: inv, event_title: (ev && ev.title) || inv.event_title };
          combined.push({ event: ev || { id: rid, title: inv.event_title }, regInfo: regLike });
        }
      }

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

    function renderRecord(ev, regInfo, container) {
      const node = tpl.content.cloneNode(true);
      const titleEl = node.querySelector('.reg-title');
      const dateEl = node.querySelector('.reg-date');
      const badge = node.querySelector('.reg-badge');
      const note = node.querySelector('.reg-note');
      const btnPay = node.querySelector('.reg-pay');
      const btnAccept = node.querySelector('.reg-accept');
      const spanPaymentId = node.querySelector('.reg-payment-id');
      const aGo = node.querySelector('.reg-go');
      const eventId = ev.id || ev._id || ev.eventId;
      if (aGo) aGo.href = `/event?id=${encodeURIComponent(eventId)}`;
      if (titleEl)
        titleEl.textContent = ev.title || ev.name || (regInfo && regInfo.event_title) || 'Event';
      // add a small view-invitation action when invited
      if (isInvitedStatus(regInfo)) {
        const view = document.createElement('button');
        view.type = 'button';
        view.className = 'text-xs text-[#008080] underline ml-2';
        view.textContent = 'View invitation';
        view.addEventListener('click', async (e) => {
          e.preventDefault();
          const inv = regInfo.invitation || (regInfo.invitation_id ? { id: regInfo.invitation_id } : null);
          // If we only have an id, try to fetch details via /invitations?registration_id or id
          let invData = inv;
          try {
            if (!invData || !invData.event_id) {
              // try to fetch the invitation by registration id if present
              if (regInfo.registration_id) {
                const { res, data } = await window.dh.apiGet(`/invitations?registration_id=${encodeURIComponent(regInfo.registration_id)}`);
                if (res.ok && Array.isArray(data) && data.length) invData = data[0];
              }
              // or by listing invitations and matching id
              if ((!invData || !invData.event_id) && regInfo.invitation && regInfo.invitation.id) {
                const { res, data } = await window.dh.apiGet(`/invitations`);
                if (res.ok && Array.isArray(data)) invData = data.find(x => String(x.id || x._id) === String(regInfo.invitation.id)) || invData;
              }
            }
          } catch (e) {}
          openInvitationModal(invData || regInfo.invitation || { id: regInfo.invitation_id });
        });
        if (titleEl && titleEl.parentNode) titleEl.parentNode.appendChild(view);
      }
      if (dateEl) {
        const d = ev.start_at ? new Date(ev.start_at) : ev.date ? new Date(ev.date) : null;
        dateEl.textContent = d && !isNaN(d) ? d.toLocaleString() : '';
      }

      const meta = computeStatus(regInfo);
      applyBadge(badge, meta);
      // Show an explanatory note for invited/pending registrations
      if (isInvitedStatus(regInfo)) {
        if (note) {
          note.classList.remove('hidden');
          note.innerHTML = 'You were invited to this event. You can accept or decline the invitation below.';
          note.classList.remove('text-red-600');
          note.classList.add('text-blue-600');
        }
        // Hide payment button for invited users
        if (btnPay) btnPay.classList.add('hidden');
        if (btnAccept) {
          btnAccept.classList.remove('hidden');
          btnAccept.addEventListener('click', async () => {
            try {
              // Prefer accepting by registration id when available, otherwise accept by invitation id
              const regId = regInfo.registration_id || regInfo._id || regInfo.id;
              let resp;
              if (regId) {
                resp = await window.dh.apiPost(`/invitations/by-registration/${encodeURIComponent(regId)}/accept`, {});
              } else {
                const invId = regInfo.invitation_id || (regInfo.invitation && regInfo.invitation.id) || regInfo.inv_id || regInfo.invitation_id_str;
                if (!invId) return alert('Invitation id missing');
                resp = await window.dh.apiPost(`/invitations/by-id/${encodeURIComponent(invId)}/accept`, {});
              }
              if (!resp.res.ok) {
                const err = resp.data?.detail || 'Failed to accept invitation';
                return alert(err);
              }
              const data = resp.data;
              // If payment is required, redirect to payment creation endpoint
              if (data && data.payment_create_endpoint && data.registration_id) {
                // redirect to payment create page (frontend will typically open provider flow)
                window.location.assign(`${data.payment_create_endpoint}?registration_id=${encodeURIComponent(data.registration_id)}`);
                return;
              }
              // otherwise refresh page or registrations
              if (window.dh?.components?.renderMyRegistrations) {
                try { window.dh.apiGet('/registrations/registration-status').then(({res,data})=> window.dh.components.renderMyRegistrations(data?.registrations || [])); } catch(e){}
                try { window.dh.apiGet('/invitations/').then(()=> window.dh.components.renderMyRegistrations()); } catch(e){}
              } else location.reload();
            } catch (e) { alert(e.message || 'Accept failed'); }
          });
        }
      }
      const amountDue =
        regInfo && typeof regInfo.amount_due_cents === 'number' ? regInfo.amount_due_cents : null;
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
          btnPay.addEventListener(
            'click',
            () => regInfo && chooseProviderAndCreatePayment(regInfo.registration_id)
          );
        }
      }
      if (isPaid) {
        btnPay && btnPay.classList.add('hidden');
        note && note.classList.add('hidden');
      }
      (container || list).appendChild(node);
    }

    future
      .sort((a, b) => {
        const ta = a.event.start_at ? new Date(a.event.start_at).getTime() : 0;
        const tb = b.event.start_at ? new Date(b.event.start_at).getTime() : 0;
        return ta - tb;
      })
      .forEach((c) => renderRecord(c.event, c.regInfo, list));
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
        .forEach((c) => renderRecord(c.event, c.regInfo, pastList));
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

  // -------- Invitation modal helpers --------
  function openInvitationModal(inv) {
    const tpl = document.getElementById('tpl-invitation-modal');
    if (!tpl) return;
    const node = tpl.content.cloneNode(true);
    const root = node.querySelector('[data-invitation-backdrop]')?.parentNode || node.firstElementChild;
    const elTitle = node.querySelector('.inv-event-title');
    const elDate = node.querySelector('.inv-event-date');
    const elBody = node.querySelector('.inv-body');
    const btnClose = node.querySelector('.inv-close');
    const btnAccept = node.querySelector('.inv-accept');
    const btnDecline = node.querySelector('.inv-decline');
    const backdrop = node.querySelector('[data-invitation-backdrop]');
    const invId = inv && (inv.id || inv._id || inv.invitation_id);
    // populate minimal fields
    elTitle && (elTitle.textContent = inv.event_title || (inv.event && inv.event.title) || 'Event');
    elDate && (elDate.textContent = inv.event_date || (inv.event && inv.event.start_at) || '');
    elBody && (elBody.textContent = inv.message || inv.note || `Invited by ${inv.created_by || ''}`);
    function closeModal() { try { document.body.removeChild(root); } catch (e) {} }
    btnClose && btnClose.addEventListener('click', closeModal);
    backdrop && backdrop.addEventListener('click', closeModal);
    btnAccept && btnAccept.addEventListener('click', async () => {
      try {
        if (!invId) return alert('Invitation id missing');
        const resp = await window.dh.apiPost(`/invitations/by-id/${encodeURIComponent(invId)}/accept`, {});
        if (!resp.res.ok) return alert(resp.data?.detail || 'Failed to accept');
        closeModal();
        // refresh registrations & invitations
        try { await window.dh.apiGet('/registrations/registration-status'); } catch {}
        try { await window.dh.apiGet('/invitations/'); } catch {}
        if (window.dh?.components?.renderMyRegistrations) window.dh.components.renderMyRegistrations();
      } catch (e) { alert(e.message || 'Accept failed'); }
    });
    btnDecline && btnDecline.addEventListener('click', async () => {
      try {
        if (!invId) return alert('Invitation id missing');
        const resp = await window.dh.apiPost(`/invitations/${encodeURIComponent(invId)}/revoke`, {});
        if (!resp.res.ok) return alert(resp.data?.detail || 'Failed to decline');
        closeModal();
        try { await window.dh.apiGet('/invitations/'); } catch {}
        if (window.dh?.components?.renderMyRegistrations) window.dh.components.renderMyRegistrations();
      } catch (e) { alert(e.message || 'Decline failed'); }
    });
    document.body.appendChild(root);
  }

  C.renderMyRegistrations = renderMyRegistrations;
})();
