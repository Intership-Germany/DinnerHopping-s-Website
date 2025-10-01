(function(){
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {}; const C = window.dh.components = window.dh.components || {};
  const U = window.dh.utils = window.dh.utils || {};

  const tpl = U.tpl || function(id){ const t=document.getElementById(id); return t && 'content' in t ? t.content.firstElementChild : null; };
  const openProviderModal = () => (U.openModalFromTemplate ? U.openModalFromTemplate('tpl-provider-modal') : (function(){ const t=tpl('tpl-provider-modal'); if(!t) return null; const n=t.cloneNode(true); document.body.appendChild(n); n.addEventListener('click',e=>{ if(e.target===n) n.remove(); }); n.querySelector('.provider-close')?.addEventListener('click',()=>n.remove()); return n; })());
  const buildRegistrationPayload = U.buildRegistrationPayload;
  const aggregateDiet = U.aggregateDiet || function(a,b){ const o=['omnivore','vegetarian','vegan']; const ca=o.indexOf((a||'').toLowerCase()); const cb=o.indexOf((b||'').toLowerCase()); if(ca===-1) return b||a||''; if(cb===-1) return a||b||''; return o[Math.max(ca,cb)]; };

  /**
   * Open registration modal for an event.
   * @param {Object} ctx { event,eventId,spotsEl,ctaEl,placeLeft,userProfile }
   */
  function openRegisterModal(ctx){
    const { event: eventObj, eventId, spotsEl, ctaEl, placeLeft, userProfile } = ctx || {};
    const t = document.getElementById('tpl-register-modal'); if(!t) return;
    const modalFrag = t.content.cloneNode(true);
    const overlay = modalFrag.querySelector('div.fixed');
    const form = modalFrag.querySelector('form.reg-form');
    form.elements.event_id.value = eventId;
    // Prefill solo form from profile
    try {
      if (userProfile) {
        const soloRoot = modalFrag.querySelector('.form-solo');
        const dietarySel = soloRoot?.querySelector('select[name="dietary"]');
        const kitchenSel = soloRoot?.querySelector('select[name="kitchen"]');
        const mainSel = soloRoot?.querySelector('select[name="main_course"]');
        const profDiet = userProfile.default_dietary_preference || userProfile.preferences?.default_dietary;
        if (dietarySel && profDiet && dietarySel.querySelector(`option[value="${profDiet}"]`)) dietarySel.value = profDiet;
        if (kitchenSel && typeof userProfile.kitchen_available === 'boolean') kitchenSel.value = userProfile.kitchen_available ? 'yes':'no';
        if (mainSel && typeof userProfile.main_course_possible === 'boolean') mainSel.value = userProfile.main_course_possible ? 'yes':'no';
        const courseSel = soloRoot?.querySelector('select[name="course"]');
        const mainPossible = userProfile.main_course_possible === true || mainSel?.value === 'yes';
        if (courseSel) { const optMain = courseSel.querySelector('option[value="main"]'); if (optMain) optMain.disabled = !mainPossible; }
        mainSel?.addEventListener('change', () => {
          const courseSel2 = soloRoot?.querySelector('select[name="course"]'); if(!courseSel2) return;
          const optMain2 = courseSel2.querySelector('option[value="main"]'); if (optMain2) optMain2.disabled = mainSel.value !== 'yes'; if (optMain2?.disabled && courseSel2.value === 'main') courseSel2.value='';
        });
      }
    } catch {}
    const close = () => overlay.remove();
    modalFrag.querySelector('.reg-close')?.addEventListener('click', close);
    modalFrag.querySelector('.reg-cancel')?.addEventListener('click', close);
    overlay.addEventListener('click', e=>{ if(e.target===overlay) close(); });
    const tabSolo = modalFrag.querySelector('.tab-btn.solo');
    const tabTeam = modalFrag.querySelector('.tab-btn.team');
    const formSolo = modalFrag.querySelector('.form-solo');
    const formTeam = modalFrag.querySelector('.form-team');
    function setMode(mode){ form.dataset.mode = mode; if(mode==='solo'){ formSolo.classList.remove('hidden'); formTeam.classList.add('hidden'); tabSolo.classList.add('bg-white','text-[#172a3a]','font-semibold'); tabTeam.classList.remove('bg-white','text-[#172a3a]','font-semibold'); } else { formTeam.classList.remove('hidden'); formSolo.classList.add('hidden'); tabTeam.classList.add('bg-white','text-[#172a3a]','font-semibold'); tabSolo.classList.remove('bg-white','text-[#172a3a]','font-semibold'); } }
    tabSolo.addEventListener('click',()=>setMode('solo'));
    tabTeam.addEventListener('click',()=>setMode('team'));
    const teamRoot = modalFrag.querySelector('.form-team');
    const partnerExisting = teamRoot.querySelector('.partner-existing');
    const partnerExternal = teamRoot.querySelector('.partner-external');
    teamRoot.addEventListener('change', e => { if(e.target.name==='partner_mode'){ const isExternal = e.target.value==='external'; partnerExternal.classList.toggle('hidden', !isExternal); partnerExisting.classList.toggle('hidden', isExternal); }});
    const teamDietSummary = modalFrag.querySelector('#team-diet-summary');
    teamRoot.addEventListener('input', () => { const partnerDiet = teamRoot.querySelector('[name="partner_dietary"]').value || ''; const selfDiet = userProfile?.preferences?.default_dietary || ''; const agg = aggregateDiet(selfDiet, partnerDiet); teamDietSummary.textContent = agg ? `Aggregated dietary preference: ${agg}` : ''; });
    form.addEventListener('submit', async (e)=>{
      e.preventDefault();
      const submitBtn = form.querySelector('.reg-submit'); const prevLabel = submitBtn.textContent; submitBtn.textContent='Submitting…'; submitBtn.disabled = true;
      try {
        const payload = buildRegistrationPayload(form, userProfile);
        const headers = { Accept:'application/json','Content-Type':'application/json' };
        const api = window.dh?.apiFetch || window.apiFetch;
        let res;
        if (payload.solo) { res = await api('/registrations/solo',{ method:'POST', headers, body: JSON.stringify(payload.body) }); }
        else { res = await api(`/events/${encodeURIComponent(payload.event_id)}/register`, { method:'POST', headers, body: JSON.stringify(payload.body) }); }
        if(!res.ok){ if(res.status===401||res.status===419){ if(typeof window.handleUnauthorized==='function') window.handleUnauthorized(); return; } throw new Error(`HTTP ${res.status}`); }
        const body = await res.json();
        const paymentLink = body.payment_link || (body.payment && (body.payment.link || body.payment.url));
        try { // snapshot with preferences (client-side only; backend unchanged)
          const regId = body.registration_id || body.registrationId || (Array.isArray(body.registration_ids)&&body.registration_ids[0]) || (Array.isArray(body.registrationIds)&&body.registrationIds[0]);
          if (regId) {
            const snapCommon = {
              registration_id: String(regId),
              status: body.status || 'registered',
              payment_status: paymentLink ? 'pending' : undefined,
              payment_link: paymentLink || undefined,
              saved_at: Date.now(),
            };
            // Derive preference summary data from form payload used to build request
            let prefs = {};
            if (payload.solo) {
              prefs.mode = 'solo';
              prefs.diet = payload.body?.dietary_preference || form.elements.dietary.value || undefined;
              prefs.kitchen = (function(){ const v=form.elements.kitchen.value; return v? (v==='yes'):'unknown'; })();
              prefs.main_course_possible = (function(){ const v=form.elements.main_course.value; return v? (v==='yes'): undefined; })();
              const cp = payload.body?.course_preference || form.elements.course.value; prefs.course_preference = cp === 'starter' ? 'appetizer' : (cp || undefined);
            } else {
              prefs.mode = 'team';
              prefs.course_preference = payload.body?.preferences?.course_preference || form.elements.team_course.value || undefined;
              const cookAt = payload.body?.preferences?.cook_at || form.elements.cook_location.value || undefined; if (cookAt) prefs.cook_at = cookAt;
              if (payload.body?.preferences?.partner_external) {
                prefs.partner_external = payload.body.preferences.partner_external;
              }
            }
            const snapshot = { ...snapCommon, preferences: prefs };
            const evId = payload.event_id; if (evId) localStorage.setItem(`dh:lastReg:${evId}`, JSON.stringify(snapshot));
          }
        } catch{}
        // Refresh registrations banner if component present
        try { if (window.dh?.utils?.fetchMyEvents && window.dh?.components?.renderMyRegistrations) { const myEventsNow = await window.dh.utils.fetchMyEvents(); window.dh.components.renderMyRegistrations(Array.isArray(myEventsNow)? myEventsNow: []); } } catch{}
        if (spotsEl && payload.solo){ const left = Math.max((placeLeft||1)-1,0); if(left<=0){ spotsEl.textContent='Event Full'; ctaEl.classList.add('opacity-60','cursor-not-allowed'); ctaEl.setAttribute('aria-disabled','true'); ctaEl.tabIndex=-1; } else spotsEl.textContent = `${left} spots left`; }
        if (paymentLink){ window.location.href = paymentLink.startsWith('http') ? paymentLink : window.BACKEND_BASE_URL + paymentLink; return; }
        const regId2 = body.registration_id || body.registrationId || (Array.isArray(body.registration_ids)&&body.registration_ids[0]) || (Array.isArray(body.registrationIds)&&body.registrationIds[0]);
        const amountCents = typeof eventObj?.fee_cents === 'number' ? eventObj.fee_cents : 0;
        if (amountCents > 0 && regId2){
          let providers = ['paypal','stripe','wero'];
          try { if(window.dh?.apiGet){ const { res: pr, data: provs } = await window.dh.apiGet('/payments/providers'); if (pr.ok){ providers = provs?.providers ? provs.providers : (Array.isArray(provs)? provs : providers); } } } catch(e){ console.warn('Provider list failed', e); }
          const provModal = openProviderModal();
          if (provModal){
            provModal.querySelectorAll('[data-provider]')?.forEach(btn => { const p=(btn.getAttribute('data-provider')||'').toLowerCase(); if(!providers.includes(p)){ btn.classList.add('opacity-40','pointer-events-none'); btn.setAttribute('aria-disabled','true'); } });
            const onChoose = async (provider)=>{ try { const payPayload = { registration_id: regId2, provider, amount_cents: amountCents }; const { res: createRes, data: created } = await (window.dh?.apiPost ? window.dh.apiPost('/payments', payPayload) : { res:{ ok:false}, data:{} }); if(!createRes.ok) throw new Error(`HTTP ${createRes.status}`); const link = (()=>{ if(!created) return null; const next = created.next_action || {}; if(next.type === 'redirect' && next.url) return next.url; if(next.type === 'instructions' && next.instructions){ const inst = next.instructions; return inst.approval_link || inst.link || null; } if(created.payment_link) return created.payment_link; const inst = created.instructions; if(inst) return inst.approval_link || inst.link || null; return null; })(); if (link){ window.location.href = link.startsWith('http') ? link : window.BACKEND_BASE_URL + link; return; } } catch(err){ console.error('Create payment failed', err); alert('Could not start payment.'); } finally { provModal.remove(); } };
            provModal.querySelectorAll('[data-provider]')?.forEach(btn => { btn.addEventListener('click', e=>{ e.preventDefault(); const p=(btn.getAttribute('data-provider')||'').toLowerCase(); if(providers.includes(p)) onChoose(p); }); });
          }
        }
        close();
      } catch(err){ alert(err.message || String(err)); }
      finally { const submitBtn2 = form.querySelector('.reg-submit'); submitBtn2.textContent = prevLabel; submitBtn2.disabled = false; }
    });
    document.body.appendChild(overlay);
  }

  C.openRegisterModal = openRegisterModal;
})();
