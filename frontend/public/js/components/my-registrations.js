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

  // Cached provider list promise
  let __providersCachePromise = null;
  async function fetchProviders(){
    if (!window.dh?.apiGet) return { providers:['paypal','stripe','wero'], default:'paypal' };
    try {
      const { res, data } = await window.dh.apiGet('/payments/providers');
      if (res.ok){
        if (data?.providers){ return { providers: data.providers, default: data.default || data.providers[0] || 'paypal' }; }
        if (Array.isArray(data)) return { providers:data, default:data[0] || 'paypal' };
      }
    } catch {}
    return { providers:['paypal','stripe','wero'], default:'paypal' };
  }
  function getProviders(){ if(!__providersCachePromise) __providersCachePromise = fetchProviders(); return __providersCachePromise; }
  async function resolveDefaultProvider(){ const { providers, default: def } = await getProviders(); return providers.length===1 ? providers[0] : def || providers[0]; }

  /** Build a concise preferences summary string */
  function summarizePreferences(p) {
    if (!p) return 'Preferences: n/a';
    const parts = [];
    try {
      if (p.mode === 'solo') {
        if (p.diet) parts.push(p.diet);
        if (typeof p.kitchen === 'boolean') parts.push(p.kitchen ? 'kitchen: yes' : 'kitchen: no');
        if (p.main_course_possible === true) parts.push('can cook main');
        if (p.course_preference) parts.push(`pref: ${p.course_preference}`);
      } else if (p.mode === 'team') {
        if (p.course_preference) parts.push(`team pref: ${p.course_preference}`);
        if (p.cook_at)
          parts.push(
            p.cook_at === 'self'
              ? 'cook at you'
              : p.cook_at === 'partner'
                ? 'cook at partner'
                : `cook: ${p.cook_at}`
          );
        if (p.partner_external?.dietary) parts.push(`partner diet: ${p.partner_external.dietary}`);
      }
    } catch {}
    return parts.length ? parts.join(' • ') : 'Preferences: —';
  }

  /** Determine human-readable status + styling + refund info */
  function computeStatus(regInfo) {
    if (!regInfo) return { label: 'registered', cancelled: false, refunded: false, refundPending: false };
    const s = (regInfo.status || '').toLowerCase();
    const pay = (regInfo.payment_status || '').toLowerCase();
    const refunded = s === 'refunded' || pay === 'refunded';
    const cancelled = ['cancelled_by_user', 'cancelled_admin', 'cancelled', 'expired', 'refunded'].includes(s);
    let label;
    if (refunded) label = 'Refunded';
    else if (s === 'cancelled_by_user') label = 'Cancelled (you)';
    else if (s === 'cancelled_admin') label = 'Cancelled (organizer)';
    else if (s === 'expired') label = 'Cancelled (expired)';
    else if (cancelled) label = 'Cancelled';
  else if (['succeeded', 'paid'].includes(pay) || ['succeeded','paid'].includes(s)) label = 'Paid';
    else if (s) label = s;
    else label = 'registered';
    const refundPending = !!regInfo.refund_flag && !refunded;
    return { label, cancelled, refunded, refundPending };
  }

  /** Update badge element with status & styling */
  function applyBadge(badgeEl, statusMeta) {
    if (!badgeEl || !statusMeta) return;
    badgeEl.textContent = statusMeta.label;
    // Add red styling for cancelled/refunded; rely on utility classes if Tailwind present
    if (statusMeta.cancelled || statusMeta.refunded) {
      badgeEl.classList.add('bg-red-600', 'text-white');
      badgeEl.classList.remove('bg-green-600');
    } else if (statusMeta.label.toLowerCase() === 'paid') {
      badgeEl.classList.add('bg-green-600', 'text-white');
    }
  }

  /** Attempt to refresh a pending payment (best-effort) */
  async function maybeRefreshPayment(regInfo, amountCents, key) {
    if (!regInfo || !regInfo.registration_id) return regInfo;
    if (regInfo.payment_status && !['pending', ''].includes(regInfo.payment_status)) return regInfo;
    const provider = await resolveDefaultProvider();
    try {
      const { res: r, data } = await (window.dh?.apiPost
        ? window.dh.apiPost('/payments/create', {
            registration_id: regInfo.registration_id,
            amount_cents: amountCents,
            provider,
          })
        : { res: { ok: false }, data: {} });
      if (r.ok && data?.status) {
        regInfo.payment_status = data.status;
        try { localStorage.setItem(key, JSON.stringify(regInfo)); } catch {}
      }
    } catch {}
    return regInfo;
  }

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

    // Split future vs past
    const now = Date.now();
    const future = [];
    const past = [];
    combined.forEach(({ event: ev, regInfo }) => {
      const start = ev.start_at ? new Date(ev.start_at).getTime() : (ev.date ? new Date(ev.date + 'T23:59:00').getTime() : null);
      if (start && start < now) past.push({ ev, regInfo }); else future.push({ ev, regInfo });
    });

    function renderRecord(ev, regInfo, container) {
      const node = tpl.content.cloneNode(true);
      const titleEl = node.querySelector('.reg-title');
      const dateEl = node.querySelector('.reg-date');
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
      if (titleEl) titleEl.textContent = ev.title || ev.name || 'Event';
      if (dateEl)
        dateEl.textContent = ev.start_at ? new Date(ev.start_at).toLocaleString() : ev.date || '';

      const amountCents = typeof ev.fee_cents === 'number' ? ev.fee_cents : 0;

      // Refresh payment best-effort (async, fire & forget)
      // (Disabled) previously attempted to refresh payment & update local snapshot.
      maybeRefreshPayment(regInfo, amountCents, undefined).then((ri) => {
        // After a best-effort refresh, recompute visual status.
        const meta = computeStatus(ri);
        applyBadge(badge, meta);
        // If payment just transitioned to paid, hide stale unpaid note & actions.
        if (meta.label && meta.label.toLowerCase() === 'paid') {
          note && note.classList.add('hidden');
          btnPay && btnPay.classList.add('hidden');
          btnShowId && btnShowId.classList.add('hidden');
        }
      });

      // Preferences summary
      if (prefsEl) {
        try { prefsEl.textContent = summarizePreferences(regInfo?.preferences); } catch { prefsEl.textContent = 'Preferences: n/a'; }
      }

      const statusMeta = computeStatus(regInfo);
      applyBadge(badge, statusMeta);

      // Cancellation / refund messaging overrides payment prompts
      if (statusMeta.cancelled || statusMeta.refunded) {
        if (note) {
          note.classList.remove('hidden');
          let msg = statusMeta.refunded ? 'Registration refunded.' : statusMeta.label;
          if (statusMeta.refundPending) msg += ' – refund pending';
          note.textContent = msg;
          note.classList.add('text-red-600');
        }
        btnPay && btnPay.classList.add('hidden');
        btnShowId && btnShowId.classList.add('hidden');
        spanPaymentId && spanPaymentId.classList.add('hidden');
      } else if (amountCents > 0) {
        // Determine if the registration should be treated as paid. Some backends may
        // reflect final state in either payment_status OR status (e.g., status = 'paid').
        const paid = (() => {
          const pay = (regInfo?.payment_status || '').toLowerCase();
            const st = (regInfo?.status || '').toLowerCase();
            return ['succeeded','paid'].includes(pay) || ['paid','succeeded'].includes(st);
        })();
        if (!paid) {
          if (note) {
            note.classList.remove('hidden');
            // Base unpaid message + provider reselect option
            note.innerHTML = `You haven't paid the fee yet. <button type="button" class="underline text-[#008080] text-xs font-medium ml-1 choose-provider">Choose provider</button>`;
          }
          if (regInfo?.registration_id) {
            if (btnPay) {
              btnPay.classList.remove('hidden');
              btnPay.addEventListener('click', async () => {
                alert('Payment initiation requires registration details. Reload after registration or open the event page.');
              });
            }
            // Provider chooser handler
            try {
              const chooseBtn = note?.querySelector('button.choose-provider');
              if (chooseBtn) {
                chooseBtn.addEventListener('click', async () => {
                  chooseBtn.disabled = true;
                  try {
                    // Fetch providers
                    let providers=['wero']; let def='wero';
                    try { if (window.dh?.apiGet){ const { res, data } = await window.dh.apiGet('/payments/providers'); if (res.ok){ if (Array.isArray(data.providers)) providers = data.providers.slice(); else if (Array.isArray(data)) providers=data.slice(); if (typeof data.default==='string') def=data.default; } } } catch{}
                    if (!providers.length) providers=['wero']; if(!providers.includes(def)) def=providers[0];
                    let provider = def;
                    if (providers.length>1){
                      provider = await new Promise(resolve=>{
                        const overlay=document.createElement('div'); overlay.className='fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4';
                        const card=document.createElement('div'); card.className='bg-white rounded-lg shadow-xl w-full max-w-sm p-5 space-y-3';
                        card.innerHTML='<h3 class="text-sm font-semibold text-gray-700">Select Payment Provider</h3>';
                        const list=document.createElement('div'); list.className='space-y-2';
                        providers.forEach(p=>{ const b=document.createElement('button'); b.type='button'; b.dataset.provider=p; b.className='w-full px-3 py-2 rounded border text-sm flex items-center justify-between hover:bg-gray-50'; b.innerHTML=`<span class="capitalize">${p}</span>${p===def?'<span class="text-xs text-teal-600">(default)</span>':''}`; list.appendChild(b); });
                        const cancel=document.createElement('button'); cancel.type='button'; cancel.className='w-full mt-2 px-3 py-2 rounded bg-gray-200 hover:bg-gray-300 text-gray-800 text-sm'; cancel.textContent='Cancel';
                        card.appendChild(list); card.appendChild(cancel); overlay.appendChild(card); document.body.appendChild(overlay);
                        list.addEventListener('click', ev=>{ const b=ev.target.closest('button[data-provider]'); if(!b) return; const p=b.dataset.provider; overlay.remove(); resolve(p); });
                        cancel.addEventListener('click', ()=>{ overlay.remove(); resolve(null); });
                      });
                      if (!provider){ chooseBtn.disabled=false; return; }
                    }
                    const payload = { registration_id: regInfo.registration_id, amount_cents: amountCents, provider };
                    const { res: createRes, data } = await (window.dh?.apiPost ? window.dh.apiPost('/payments/create', payload) : { res:{ ok:false }, data:{} });
                    if (!createRes.ok) throw new Error(`HTTP ${createRes.status}`);
                    const link = data.payment_link || (data.instructions && (data.instructions.approval_link || data.instructions.link));
                    if (link) window.location.assign(link.startsWith('http')? link: window.BACKEND_BASE_URL + link); else alert('Payment initiated. Follow provider instructions.');
                  } catch(err){ alert(err.message || 'Payment creation failed'); }
                  finally { chooseBtn.disabled=false; }
                });
              }
            } catch {}
            if (btnShowId) {
              btnShowId.classList.remove('hidden');
              btnShowId.addEventListener('click', async (e) => {
                e.preventDefault();
                alert('Payment ID unavailable (registration data not cached). Open the event page for payment details.');
              });
            }
          } else {
            // No registration snapshot => cannot initiate payment (need registration_id). Inform user.
            if (note) {
              const linkUrl = `/event.html?id=${encodeURIComponent(eventId)}`;
              note.innerHTML = `Registration info not loaded. <a class="underline" href="${linkUrl}">Open event page</a> or <button type="button" class="retrieve-link underline text-[#172a3a]">Retrieve payment link</button>.`;
              // Add retrieve payment link handler (solo re-register upsert approach)
              const btnRetrieve = note.querySelector('button.retrieve-link');
              if (btnRetrieve) {
                btnRetrieve.addEventListener('click', async () => {
                  btnRetrieve.disabled = true;
                  const prev = btnRetrieve.textContent;
                  btnRetrieve.textContent = 'Working…';
                  try {
                    const headers = { 'Accept': 'application/json', 'Content-Type': 'application/json' };
                    const api = window.dh?.apiFetch || window.apiFetch;
                    // Attempt solo upsert registration to obtain a registration_id (works even if originally registered earlier as solo; may fail for team registrations)
                    const regRes = await api('/registrations/solo', { method: 'POST', headers, body: JSON.stringify({ event_id: eventId }) });
                    if (!regRes.ok) throw new Error(`Registration refresh failed (${regRes.status})`);
                    const regBody = await regRes.json();
                    const regId = regBody.registration_id || regBody.registrationId || (Array.isArray(regBody.registration_ids)&&regBody.registration_ids[0]) || (Array.isArray(regBody.registrationIds)&&regBody.registrationIds[0]);
                    if (!regId) throw new Error('No registration id returned');
                    // Fetch providers to respect backend default (no hardcoded assumption)
                    let providers = ['wero']; let defaultProvider = 'wero';
                    try {
                      if (window.dh?.apiGet) {
                        const { res: pr, data: provs } = await window.dh.apiGet('/payments/providers');
                        if (pr.ok && provs) {
                          if (Array.isArray(provs.providers)) providers = provs.providers.slice();
                          else if (Array.isArray(provs)) providers = provs.slice();
                          if (typeof provs.default === 'string') defaultProvider = provs.default;
                          else if (providers.length) defaultProvider = providers[0];
                        }
                      }
                    } catch(e){ console.warn('Provider list fetch failed', e); }
                    const providerToUse = providers.length === 1 ? providers[0] : defaultProvider;
                    const payPayload = { registration_id: regId, amount_cents: amountCents, provider: providerToUse };
                    let create; try { create = window.dh?.apiPost ? await window.dh.apiPost('/payments/create', payPayload) : null; } catch(e){ create = null; }
                    if (!create || !create.res || !create.res.ok) throw new Error(`Payment create failed${create?.res? ' ('+create.res.status+')':''}`);
                    const data = create.data || {};
                    const link = data.payment_link || (data.instructions && (data.instructions.approval_link || data.instructions.link));
                    if (link) { window.location.assign(link.startsWith('http') ? link : window.BACKEND_BASE_URL + link); return; }
                    btnRetrieve.textContent = 'Payment created (follow provider instructions)';
                  } catch (err) {
                    console.error('Retrieve payment link failed', err);
                    alert(err.message || 'Could not retrieve payment link.');
                    btnRetrieve.disabled = false;
                    btnRetrieve.textContent = prev;
                    return;
                  }
                });
              }
            }
          }
        } else {
          note && note.classList.add('hidden');
          btnPay && btnPay.classList.add('hidden');
          btnShowId && btnShowId.classList.add('hidden');
        }
      }

      if (match) match.textContent = ev.matching_status ? `Matching: ${ev.matching_status}` : '';
      container.appendChild(node);
    }

    // Render future first
    future.sort((a,b)=>{
      const ta = a.ev.start_at ? new Date(a.ev.start_at).getTime() : 0;
      const tb = b.ev.start_at ? new Date(b.ev.start_at).getTime() : 0;
      return ta - tb;
    }).forEach(r => renderRecord(r.ev, r.regInfo, list));

    // Create collapsible section for past events
    if (past.length) {
      const toggleId = 'myregs-toggle-past';
      let pastWrap = document.createElement('div');
      pastWrap.className = 'mt-4';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.id = toggleId;
      btn.className = 'text-xs text-gray-600 hover:text-gray-800 underline';
      btn.textContent = `Show past events (${past.length})`;
      const pastList = document.createElement('div');
      pastList.className = 'mt-2 hidden';
      // sort most recent first
      past.sort((a,b)=>{
        const ta = a.ev.start_at ? new Date(a.ev.start_at).getTime() : 0;
        const tb = b.ev.start_at ? new Date(b.ev.start_at).getTime() : 0;
        return tb - ta;
      }).forEach(r => renderRecord(r.ev, r.regInfo, pastList));
      btn.addEventListener('click', () => {
        const hidden = pastList.classList.contains('hidden');
        pastList.classList.toggle('hidden', !hidden);
        btn.textContent = hidden ? `Hide past events (${past.length})` : `Show past events (${past.length})`;
      });
      pastWrap.appendChild(btn);
      pastWrap.appendChild(pastList);
      list.appendChild(pastWrap);
    }

    wrap.classList.remove('hidden');
  }

  C.renderMyRegistrations = renderMyRegistrations;
})();
