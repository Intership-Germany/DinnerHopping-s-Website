(function () {
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  const C = (window.dh.components = window.dh.components || {});
  const U = (window.dh.utils = window.dh.utils || {});

  const tpl =
    U.tpl ||
    function (id) {
      const t = document.getElementById(id);
      return t && 'content' in t ? t.content.firstElementChild : null;
    };
  const openProviderModal = () =>
    U.openModalFromTemplate
      ? U.openModalFromTemplate('tpl-provider-modal')
      : (function () {
          const t = tpl('tpl-provider-modal');
          if (!t) return null;
          const n = t.cloneNode(true);
          document.body.appendChild(n);
          n.addEventListener('click', (e) => {
            if (e.target === n) n.remove();
          });
          n.querySelector('.provider-close')?.addEventListener('click', () => n.remove());
          return n;
        })();
  const buildRegistrationPayload = U.buildRegistrationPayload;
  const aggregateDiet =
    U.aggregateDiet ||
    function (a, b) {
      const o = ['omnivore', 'vegetarian', 'vegan'];
      const ca = o.indexOf((a || '').toLowerCase());
      const cb = o.indexOf((b || '').toLowerCase());
      if (ca === -1) return b || a || '';
      if (cb === -1) return a || b || '';
      return o[Math.max(ca, cb)];
    };

  /**
   * Open registration modal for an event.
   * @param {Object} ctx { event,eventId,spotsEl,ctaEl,placeLeft,userProfile }
   */
  function openRegisterModal(ctx) {
    const { event: eventObj, eventId, spotsEl, ctaEl, placeLeft, userProfile } = ctx || {};
    const t = document.getElementById('tpl-register-modal');
    if (!t) return;
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
        const profDiet =
          userProfile.default_dietary_preference || userProfile.preferences?.default_dietary;
        if (dietarySel && profDiet && dietarySel.querySelector(`option[value="${profDiet}"]`))
          dietarySel.value = profDiet;
        if (kitchenSel && typeof userProfile.kitchen_available === 'boolean')
          kitchenSel.value = userProfile.kitchen_available ? 'yes' : 'no';
        if (mainSel && typeof userProfile.main_course_possible === 'boolean')
          mainSel.value = userProfile.main_course_possible ? 'yes' : 'no';
        const courseSel = soloRoot?.querySelector('select[name="course"]');
        const mainPossible = userProfile.main_course_possible === true || mainSel?.value === 'yes';
        if (courseSel) {
          const optMain = courseSel.querySelector('option[value="main"]');
          if (optMain) optMain.disabled = !mainPossible;
        }
        mainSel?.addEventListener('change', () => {
          const courseSel2 = soloRoot?.querySelector('select[name="course"]');
          if (!courseSel2) return;
          const optMain2 = courseSel2.querySelector('option[value="main"]');
          if (optMain2) optMain2.disabled = mainSel.value !== 'yes';
          if (optMain2?.disabled && courseSel2.value === 'main') courseSel2.value = '';
        });
      }
    } catch {}
    const close = () => overlay.remove();
    modalFrag.querySelector('.reg-close')?.addEventListener('click', close);
    modalFrag.querySelector('.reg-cancel')?.addEventListener('click', close);
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) close();
    });
    const tabSolo = modalFrag.querySelector('.tab-btn.solo');
    const tabTeam = modalFrag.querySelector('.tab-btn.team');
    const formSolo = modalFrag.querySelector('.form-solo');
    const formTeam = modalFrag.querySelector('.form-team');
    function setMode(mode) {
      form.dataset.mode = mode;
      if (mode === 'solo') {
        formSolo.classList.remove('hidden');
        formTeam.classList.add('hidden');
        tabSolo.classList.add('bg-white', 'text-[#172a3a]', 'font-semibold');
        tabTeam.classList.remove('bg-white', 'text-[#172a3a]', 'font-semibold');
      } else {
        formTeam.classList.remove('hidden');
        formSolo.classList.add('hidden');
        tabTeam.classList.add('bg-white', 'text-[#172a3a]', 'font-semibold');
        tabSolo.classList.remove('bg-white', 'text-[#172a3a]', 'font-semibold');
      }
    }
    tabSolo.addEventListener('click', () => setMode('solo'));
    tabTeam.addEventListener('click', () => setMode('team'));
    const teamRoot = modalFrag.querySelector('.form-team');
    const partnerExisting = teamRoot.querySelector('.partner-existing');
    const partnerExternal = teamRoot.querySelector('.partner-external');
    // Partner search elements (from home.html modification)
    const partnerEmailInput = partnerExisting.querySelector('.partner-email-input');
    const partnerSearchBtn = partnerExisting.querySelector('.partner-search-btn');
    const partnerStatusEl = partnerExisting.querySelector('.partner-search-status');

    // Helper: show status message
    const setPartnerStatus = (msg, kind) => {
      if (!partnerStatusEl) return;
      partnerStatusEl.textContent = msg || '';
      partnerStatusEl.classList.remove('text-red-600', 'text-green-600', 'text-gray-600');
      if (kind === 'error') partnerStatusEl.classList.add('text-red-600');
      else if (kind === 'ok') partnerStatusEl.classList.add('text-green-600');
      else partnerStatusEl.classList.add('text-gray-600');
    };
    teamRoot.addEventListener('change', (e) => {
      if (e.target.name === 'partner_mode') {
        const isExternal = e.target.value === 'external';
        partnerExternal.classList.toggle('hidden', !isExternal);
        partnerExisting.classList.toggle('hidden', isExternal);
      }
    });

    // Partner search click handler: call backend search-user endpoint
    if (partnerSearchBtn && partnerEmailInput) {
      partnerSearchBtn.addEventListener('click', async (ev) => {
        ev.preventDefault();
        const email = (partnerEmailInput.value || '').trim();
        if (!email) {
          setPartnerStatus('Please enter an email to search.', 'error');
          return;
        }
        setPartnerStatus('Searching…', 'info');
        try {
          const apiGet = window.dh?.apiGet || (window.apiFetch ? async (p) => { const r = await window.apiFetch(p); return { res: r, data: await (r.ok ? r.json() : null) }; } : null);
          if (!apiGet) {
            // fallback to direct fetch
            const q = new URL((window.BACKEND_BASE_URL || '') + '/registrations/search-user', window.location.origin);
            q.searchParams.set('email', email);
            const r = await fetch(q.toString(), { credentials: 'include' });
            if (!r.ok) {
              if (r.status === 404) throw new Error('User not found');
              const t = await r.text(); throw new Error(t || `HTTP ${r.status}`);
            }
            const data = await r.json();
            // store minimal profile snapshot on the input element for later use
            partnerEmailInput.dataset.found = '1';
            partnerEmailInput.dataset.fullName = data.full_name || '';
            partnerEmailInput.dataset.kitchenAvailable = data.kitchen_available ? '1' : '0';
            partnerEmailInput.dataset.mainCoursePossible = data.main_course_possible ? '1' : '0';
            setPartnerStatus(`Found: ${data.full_name || data.email}`, 'ok');
            return;
          }
          // use apiGet wrapper which returns { res, data }
          const { res, data } = await apiGet(`/registrations/search-user?email=${encodeURIComponent(email)}`);
          if (!res.ok) {
            if (res.status === 404) throw new Error('User not found');
            const detail = data?.detail || data?.message || `HTTP ${res.status}`;
            throw new Error(detail);
          }
          partnerEmailInput.dataset.found = '1';
          partnerEmailInput.dataset.fullName = data.full_name || '';
          partnerEmailInput.dataset.kitchenAvailable = data.kitchen_available ? '1' : '0';
          partnerEmailInput.dataset.mainCoursePossible = data.main_course_possible ? '1' : '0';
          setPartnerStatus(`Found: ${data.full_name || data.email}`, 'ok');
        } catch (err) {
          setPartnerStatus(err.message || 'Search failed', 'error');
          try { delete partnerEmailInput.dataset.found; } catch (e) {}
        }
      });
    }
    const teamDietSummary = modalFrag.querySelector('#team-diet-summary');
    teamRoot.addEventListener('input', () => {
      const partnerDiet = teamRoot.querySelector('[name="partner_dietary"]').value || '';
      const selfDiet = userProfile?.preferences?.default_dietary || '';
      const agg = aggregateDiet(selfDiet, partnerDiet);
      teamDietSummary.textContent = agg ? `Aggregated dietary preference: ${agg}` : '';
    });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const submitBtn = form.querySelector('.reg-submit');
      const prevLabel = submitBtn.textContent;
      submitBtn.textContent = 'Submitting…';
      submitBtn.disabled = true;
      try {
        const payload = buildRegistrationPayload(form, userProfile);
        const headers = { Accept: 'application/json', 'Content-Type': 'application/json' };
        const api = window.dh?.apiFetch || window.apiFetch;
        let res;
        if (payload.solo) {
          res = await api('/registrations/solo', {
            method: 'POST',
            headers,
            body: JSON.stringify(payload.body),
          });
        } else {
          // Map client payload to backend expected TeamRegistrationIn shape
          // payload.body currently contains: { team_size:2, invited_emails, preferences }
          const pref = payload.body.preferences || {};
          const cookAt = form.elements.cook_location?.value || pref.cook_at || 'self';
          // Normalize course names: 'starter' -> 'appetizer'
          let teamCourse = pref.course_preference || form.elements.team_course?.value || '';
          if (teamCourse === 'starter') teamCourse = 'appetizer';

          // Build backend body
          const backendBody = {
            event_id: payload.event_id,
            cooking_location: cookAt,
            course_preference: teamCourse || undefined,
          };

          // Prefer explicit partner_external when provided, otherwise fall back to partner_existing
          if (payload.body && payload.body.preferences && payload.body.preferences.partner_external) {
            const ext = payload.body.preferences.partner_external;
            backendBody.partner_external = {
              name: ext.name,
              email: ext.email,
              gender: ext.gender || undefined,
              dietary_preference: ext.dietary || ext.dietary_preference || undefined,
              field_of_study: ext.field_of_study || ext.field || undefined,
            };
            // forward optional partner kitchen/main flags if provided
            if (typeof ext.kitchen_available !== 'undefined') backendBody.partner_external.kitchen_available = !!ext.kitchen_available;
            if (typeof ext.main_course_possible !== 'undefined') backendBody.partner_external.main_course_possible = !!ext.main_course_possible;
          } else if (Array.isArray(payload.body.invited_emails) && payload.body.invited_emails.length) {
            backendBody.partner_existing = { email: payload.body.invited_emails[0] };
          }

          // forward creator kitchen/main choices when present (top-level fields expected by backend)
          const creatorKitchenVal = form.elements.creator_kitchen?.value;
          const creatorMainVal = form.elements.creator_main_course?.value;
          if (typeof creatorKitchenVal !== 'undefined' && creatorKitchenVal !== null && creatorKitchenVal !== '') {
            backendBody.kitchen_available = creatorKitchenVal === 'yes';
          }
          if (typeof creatorMainVal !== 'undefined' && creatorMainVal !== null && creatorMainVal !== '') {
            backendBody.main_course_possible = creatorMainVal === 'yes';
          }

          res = await api('/registrations/team', {
            method: 'POST',
            headers,
            body: JSON.stringify(backendBody),
          });
        }
        if (!res.ok) {
          if (res.status === 401 || res.status === 419) {
            if (typeof window.handleUnauthorized === 'function') window.handleUnauthorized();
            return;
          }
          // Try to read server error detail for better feedback
          let errMsg = `HTTP ${res.status}`;
          try {
            const errBody = await res.clone().json();
            const detail = errBody?.detail || errBody?.message || errBody?.error;
            if (detail) errMsg = detail;
            console.error('Registration failed:', errBody);
          } catch {
            try {
              const t = await res.clone().text();
              if (t) console.error('Registration failed (raw):', t);
            } catch {}
          }
          throw new Error(errMsg);
        }
        const body = await res.json();
        const paymentLink =
          body.payment_link || (body.payment && (body.payment.link || body.payment.url));
        try {
          // snapshot with preferences (client-side only; backend unchanged)
          const regId =
            body.registration_id ||
            body.registrationId ||
            (Array.isArray(body.registration_ids) && body.registration_ids[0]) ||
            (Array.isArray(body.registrationIds) && body.registrationIds[0]);
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
              prefs.diet =
                payload.body?.dietary_preference || form.elements.dietary.value || undefined;
              prefs.kitchen = (function () {
                const v = form.elements.kitchen.value;
                return v ? v === 'yes' : 'unknown';
              })();
              prefs.main_course_possible = (function () {
                const v = form.elements.main_course.value;
                return v ? v === 'yes' : undefined;
              })();
              const cp = payload.body?.course_preference || form.elements.course.value;
              prefs.course_preference = cp === 'starter' ? 'appetizer' : cp || undefined;
            } else {
              prefs.mode = 'team';
              prefs.course_preference =
                payload.body?.preferences?.course_preference ||
                form.elements.team_course.value ||
                undefined;
              const cookAt =
                payload.body?.preferences?.cook_at ||
                form.elements.cook_location.value ||
                undefined;
              if (cookAt) prefs.cook_at = cookAt;
              if (payload.body?.preferences?.partner_external) {
                prefs.partner_external = payload.body.preferences.partner_external;
              }
            }
            const snapshot = { ...snapCommon, preferences: prefs };
            // Removed: do not persist registration snapshot in localStorage (per request)
          }
        } catch {}
        // Refresh registrations banner if component present
        try {
          if (window.dh?.utils?.fetchMyEvents && window.dh?.components?.renderMyRegistrations) {
            const myEventsNow = await window.dh.utils.fetchMyEvents();
            window.dh.components.renderMyRegistrations(
              Array.isArray(myEventsNow) ? myEventsNow : []
            );
          }
        } catch {}
        if (spotsEl && payload.solo) {
          const left = Math.max((placeLeft || 1) - 1, 0);
          if (left <= 0) {
            spotsEl.textContent = 'Event Full';
            ctaEl.classList.add('opacity-60', 'cursor-not-allowed');
            ctaEl.setAttribute('aria-disabled', 'true');
            ctaEl.tabIndex = -1;
          } else spotsEl.textContent = `${left} spots left`;
        }
        if (paymentLink) {
          window.location.href = paymentLink.startsWith('http')
            ? paymentLink
            : window.BACKEND_BASE_URL + paymentLink;
          return;
        }
        const regId2 =
          body.registration_id ||
          body.registrationId ||
          (Array.isArray(body.registration_ids) && body.registration_ids[0]) ||
          (Array.isArray(body.registrationIds) && body.registrationIds[0]);
        const amountCents = typeof eventObj?.fee_cents === 'number' ? eventObj.fee_cents : 0;
        if (amountCents > 0 && regId2) {
          const payCreatePath = body.payment_create_endpoint || '/payments/create';
          // Requirement: user must be able to choose among all available providers.
          // Fallback: if we cannot retrieve providers, auto-start defaultProvider.
          let providers = [];
          let defaultProvider = 'paypal';
          let providersLoaded = false;
          try {
            if (window.dh?.apiGet) {
              const { res: pr, data: provs } = await window.dh.apiGet('/payments/providers');
              if (pr.ok) {
                providersLoaded = true;
                if (provs?.providers && Array.isArray(provs.providers)) providers = provs.providers;
                else if (Array.isArray(provs)) providers = provs;
                if (typeof provs?.default === 'string') defaultProvider = provs.default;
              }
            }
          } catch (e) {
            console.warn('Provider list failed', e);
          }
          if (!Array.isArray(providers)) providers = [];
          providers = providers
            .map((p) => (typeof p === 'string' ? p.toLowerCase() : ''))
            .filter((p) => p && ['paypal', 'stripe', 'others'].includes(p));
          if (!providers.includes(defaultProvider) && providers.length) defaultProvider = providers[0];

          const startPayment = async (provider, opts) => {
            try {
              const payPayload = { registration_id: regId2, provider };
              // If opting for manual 'others' provider allow passing a message
              if (provider === 'others' && opts && typeof opts.message === 'string') {
                payPayload.message = opts.message;
              }
              const { res: createRes, data: created } = await (window.dh?.apiPost
                ? window.dh.apiPost(payCreatePath, payPayload)
                : { res: { ok: false }, data: {} });
              if (!createRes.ok) throw new Error(`HTTP ${createRes.status}`);
              if (created.status === 'no_payment_required') {
                alert('No payment required.');
                return true;
              }
              // Resolve link from next_action or fallback fields
              let link = null;
              if (created.next_action) {
                if (created.next_action.type === 'redirect') link = created.next_action.url;
                else if (created.next_action.type === 'paypal_order') link = created.next_action.approval_link;
                else if (created.next_action.type === 'instructions') {
                  const instr = created.next_action.instructions || created.instructions;
                  if (instr) {
                    const summary = [
                      instr.reference && `Reference: ${instr.reference}`,
                      instr.iban && `IBAN: ${instr.iban}`,
                      instr.amount && `Amount: ${instr.amount}`,
                      instr.currency && `Currency: ${instr.currency}`,
                    ].filter(Boolean).join('\n');
                    alert('Bank transfer instructions generated.\n\n' + summary);
                  }
                }
              }
              if (!link) link = created.payment_link;
              if (link) {
                window.location.href = link.startsWith('http') ? link : window.BACKEND_BASE_URL + link;
                return true;
              }
              alert('Payment initiated. Follow provider instructions.');
              return true;
            } catch (err) {
              console.error('Create payment failed', err);
              alert('Could not start payment.');
              return false;
            }
          };

          if (!providers.length) {
            alert('Online payments are currently unavailable. Please contact support to finalize your registration.');
            return;
          }

          // If we have exactly one provider, auto-start it.
          if (providers.length === 1) {
            if (providers[0] === 'others') {
              const msg = prompt('Enter a short message for the admin to process your manual payment (optional):');
              await startPayment('others', { message: msg });
            } else {
              await startPayment(providers[0]);
            }
          } else if (providers.length > 1) {
            // Show selection modal.
            const provModal = openProviderModal();
            if (provModal) {
              let providerChosen = false;
              // Mark unavailable buttons and highlight default
              provModal.querySelectorAll('[data-provider]')?.forEach((btn) => {
                const p = (btn.getAttribute('data-provider') || '').toLowerCase();
                if (!providers.includes(p)) {
                  btn.classList.add('opacity-40', 'pointer-events-none');
                  btn.setAttribute('aria-disabled', 'true');
                } else if (p === defaultProvider) {
                  btn.classList.add('ring-2', 'ring-[#008080]', 'ring-offset-1');
                }
                // Normalize label text to provider key when desired
                if (providers.includes(p)) {
                  const labelSpan = btn.querySelector('span.font-medium');
                  if (labelSpan) {
                    if (p === 'paypal') {
                      labelSpan.textContent = 'Pay with PayPal';
                    } else if (p === 'stripe') {
                      labelSpan.textContent = 'Pay with Stripe';
                    } else {
                      labelSpan.textContent = `Pay with ${p.charAt(0).toUpperCase()}${p.slice(1)}`;
                    }
                  }
                }
              });
              const onChoose = async (provider) => {
                providerChosen = true;
                if (provider === 'others') {
                  // collect optional message then create manual payment
                  const msg = provModal.querySelector('textarea[data-provider-message]')?.value || prompt('Enter a short message for the admin (optional):');
                  const ok = await startPayment(provider, { message: msg });
                  if (ok) provModal.remove();
                  return;
                }
                const ok = await startPayment(provider);
                if (ok) provModal.remove();
              };
              provModal.querySelectorAll('[data-provider]')?.forEach((btn) => {
                btn.addEventListener('click', (e) => {
                  e.preventDefault();
                  const p = (btn.getAttribute('data-provider') || '').toLowerCase();
                  if (providers.includes(p)) onChoose(p);
                });
              });
              // Intercept closing (close button or background) to fallback to default provider.
              const tryFallback = () => {
                if (!providerChosen) startPayment(defaultProvider);
              };
              // Close button
              const closeBtn = provModal.querySelector('.provider-close');
              if (closeBtn) {
                closeBtn.addEventListener('click', () => {
                  tryFallback();
                }, { capture: true });
              }
              // Background click (modal overlay root itself)
              provModal.addEventListener('click', (evt) => {
                if (evt.target === provModal) tryFallback();
              }, { capture: true });
            } else {
              // Modal template missing -> fallback
              await startPayment(defaultProvider);
            }
          } else {
            // No providers list (fetch failed or empty) -> fallback to default directly
            await startPayment(defaultProvider || providers[0]);
          }
        }
        close();
      } catch (err) {
        alert(err.message || String(err));
      } finally {
        const submitBtn2 = form.querySelector('.reg-submit');
        submitBtn2.textContent = prevLabel;
        submitBtn2.disabled = false;
      }
    });
    document.body.appendChild(overlay);
  }

  C.openRegisterModal = openRegisterModal;
})();
