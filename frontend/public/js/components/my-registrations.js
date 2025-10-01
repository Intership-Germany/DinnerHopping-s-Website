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

  /** Render the "My registrations" banner/cards. */
  function renderMyRegistrations(events) {
    const wrap = document.getElementById('my-registrations');
    const list = document.getElementById('my-registrations-list');
    if (!wrap || !list) return;
    list.innerHTML = '';
    if (!Array.isArray(events) || events.length === 0) {
      wrap.classList.add('hidden');
      return;
    }
    const tpl = document.getElementById('tpl-myreg-card');
    if (!tpl) {
      wrap.classList.add('hidden');
      return;
    }
    events.forEach((ev) => {
      const node = tpl.content.cloneNode(true);
      node.querySelector('.reg-title').textContent = ev.title || ev.name || 'Event';
      node.querySelector('.reg-date').textContent = ev.start_at
        ? new Date(ev.start_at).toLocaleString()
        : ev.date || '';
      const badge = node.querySelector('.reg-badge');
      const match = node.querySelector('.reg-match');
      const note = node.querySelector('.reg-note');
      const btnPay = node.querySelector('.reg-pay');
      const btnShowId = node.querySelector('.reg-show-id');
      const spanPaymentId = node.querySelector('.reg-payment-id');
  const prefsEl = node.querySelector('.reg-prefs');
      const aGo = node.querySelector('.reg-go');
      const eventId = ev.id || ev._id || ev.eventId;
      if (aGo) aGo.href = `/event.html?id=${encodeURIComponent(eventId)}`;
      const key = `dh:lastReg:${eventId}`;
      const amountCents = typeof ev.fee_cents === 'number' ? ev.fee_cents : 0;
      let regInfo = null;
      try {
        const raw = localStorage.getItem(key);
        regInfo = raw ? JSON.parse(raw) : null;
      } catch {}
      (async () => {
        try {
          if (
            regInfo &&
            regInfo.registration_id &&
            (!regInfo.payment_status || regInfo.payment_status === 'pending')
          ) {
            const { res: r, data } = await (window.dh?.apiPost
              ? window.dh.apiPost('/payments', {
                  registration_id: regInfo.registration_id,
                  amount_cents: amountCents,
                })
              : { res: { ok: false }, data: {} });
            if (r.ok && data) {
              const status = data.status;
              if (status) {
                regInfo.payment_status = status;
                try {
                  localStorage.setItem(key, JSON.stringify(regInfo));
                } catch {}
                if (['succeeded', 'paid'].includes(status)) {
                  badge.textContent = 'paid';
                  note.classList.add('hidden');
                  btnPay.classList.add('hidden');
                  btnShowId.classList.add('hidden');
                  spanPaymentId.classList.add('hidden');
                }
              }
            }
          }
        } catch {}
      })();
      badge.textContent =
        regInfo && ['succeeded', 'paid'].includes(regInfo.payment_status)
          ? 'paid'
          : regInfo && regInfo.status
            ? regInfo.status
            : 'registered';
      // Preferences summary (from localStorage snapshot only; backend unchanged)
      if (prefsEl) {
        try {
          const p = regInfo && regInfo.preferences ? regInfo.preferences : null;
          if (p) {
            const parts = [];
            if (p.mode === 'solo') {
              if (p.diet) parts.push(p.diet);
              if (typeof p.kitchen === 'boolean') parts.push(p.kitchen ? 'kitchen: yes' : 'kitchen: no');
              if (p.main_course_possible === true) parts.push('can cook main');
              if (p.course_preference) parts.push(`pref: ${p.course_preference}`);
            } else if (p.mode === 'team') {
              if (p.course_preference) parts.push(`team pref: ${p.course_preference}`);
              if (p.cook_at) parts.push(p.cook_at === 'self' ? 'cook at you' : p.cook_at === 'partner' ? 'cook at partner' : `cook: ${p.cook_at}`);
              if (p.partner_external && p.partner_external.dietary) parts.push(`partner diet: ${p.partner_external.dietary}`);
            }
            prefsEl.textContent = parts.length ? parts.join(' • ') : 'Preferences: —';
          } else {
            prefsEl.textContent = 'Preferences: n/a';
          }
        } catch { prefsEl.textContent = 'Preferences: n/a'; }
      }
      if (amountCents > 0) {
        const notPaid =
          !regInfo ||
          !regInfo.payment_status ||
          !['succeeded', 'paid'].includes(regInfo.payment_status);
        if (notPaid) {
          note.classList.remove('hidden');
          note.textContent = "You haven't paid the fee yet.";
          if (regInfo && regInfo.registration_id) {
            btnPay.classList.remove('hidden');
            btnPay.addEventListener('click', async () => {
              try {
                const { res: r, data } = await (window.dh?.apiPost
                  ? window.dh.apiPost('/payments', {
                      registration_id: regInfo.registration_id,
                      amount_cents: amountCents,
                    })
                  : { res: { ok: false }, data: {} });
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                try {
                  const stored = regInfo || {};
                  if (data.payment_id) stored.payment_id = String(data.payment_id);
                  if (data.status) stored.payment_status = data.status;
                  localStorage.setItem(key, JSON.stringify(stored));
                } catch {}
                const link = (() => {
                  if (!data) return null;
                  const next = data.next_action || {};
                  if (next.type === 'redirect' && next.url) return next.url;
                  if (next.type === 'instructions' && next.instructions) {
                    const inst = next.instructions;
                    return inst.approval_link || inst.link || null;
                  }
                  if (data.payment_link) return data.payment_link;
                  const inst = data.instructions;
                  if (inst) return inst.approval_link || inst.link || null;
                  return null;
                })();
                if (link)
                  window.location.assign(
                    link.startsWith('http') ? link : window.BACKEND_BASE_URL + link
                  );
              } catch {
                alert('Could not create payment.');
              }
            });
            btnShowId.classList.remove('hidden');
            const reveal = async () => {
              try {
                if (regInfo && regInfo.payment_id) {
                  spanPaymentId.textContent = `Payment ID: ${regInfo.payment_id}`;
                  spanPaymentId.classList.remove('hidden');
                  btnShowId.classList.add('hidden');
                  return;
                }
                const { res: r, data } = await (window.dh?.apiPost
                  ? window.dh.apiPost('/payments', {
                      registration_id: regInfo.registration_id,
                      amount_cents: amountCents,
                    })
                  : { res: { ok: false }, data: {} });
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                const pid = data.payment_id;
                if (pid) {
                  spanPaymentId.textContent = `Payment ID: ${pid}`;
                  spanPaymentId.classList.remove('hidden');
                  btnShowId.classList.add('hidden');
                  try {
                    const stored = regInfo || {};
                    stored.payment_id = String(pid);
                    if (data.status) stored.payment_status = data.status;
                    localStorage.setItem(key, JSON.stringify(stored));
                  } catch {}
                }
              } catch {
                alert('Unable to retrieve payment id right now.');
              }
            };
            btnShowId.addEventListener('click', (e) => {
              e.preventDefault();
              reveal();
            });
            if (regInfo && regInfo.payment_id) {
              spanPaymentId.textContent = `Payment ID: ${regInfo.payment_id}`;
              spanPaymentId.classList.remove('hidden');
              btnShowId.classList.add('hidden');
            }
          }
        } else {
          note.classList.add('hidden');
          btnPay.classList.add('hidden');
          btnShowId.classList.add('hidden');
        }
      }
      match.textContent = ev.matching_status ? `Matching: ${ev.matching_status}` : '';
      list.appendChild(node);
    });
    wrap.classList.remove('hidden');
    try {
      setTimeout(() => {
        const paidEvents = events
          .map((ev) => ev && (ev.id || ev._id || ev.eventId))
          .filter(Boolean)
          .filter((eid) => {
            try {
              const raw = localStorage.getItem(`dh:lastReg:${eid}`);
              if (!raw) return false;
              const obj = JSON.parse(raw);
              return ['succeeded', 'paid'].includes(obj.payment_status);
            } catch {
              return false;
            }
          });
        if (paidEvents.length === 1 && !/event\.html$/i.test(window.location.pathname)) {
          window.location.replace(`event.html?id=${encodeURIComponent(paidEvents[0])}`);
        }
      }, 600);
    } catch {}
  }

  C.renderMyRegistrations = renderMyRegistrations;
})();
