// Constants for maintainability
const MEAL_TITLES = {
  appetizer: 'Starter',
  main: 'Main Course',
  dessert: 'Dessert',
};
const MEAL_COLORS = {
  appetizer: '#008080',
  main: '#f46f47',
  dessert: '#ffc241',
};
const DEFAULT_COLOR = '#008080';
const ERROR_MESSAGES = {
  noEventId: 'No event_id found in URL parameters.',
  fetchPlanFailed: 'Failed to fetch event plan: ',
  noSections: 'No sections found in event plan.',
  noPlanYet: 'No plan yet (matching not run)',
  mapInitFailed: 'Failed to initialize map: ',
};

// Global variables for registration and event data
let eventData = null;
let registrationData = null;

/**
 * Adds a status message to the UI.
 * @param {string} html - Message content (HTML allowed).
 * @param {string} variant - Message type: 'info', 'warn', 'error', or 'success'.
 */
function pushMessage(html, variant = 'info') {
  const div = document.createElement('div');
  const colors = {
    info: 'bg-blue-50 border-blue-200 text-blue-700',
    warn: 'bg-amber-50 border-amber-200 text-amber-800',
    error: 'bg-red-50 border-red-200 text-red-700',
    success: 'bg-green-50 border-green-200 text-green-700',
  };
  div.className = `text-sm border rounded p-3 ${colors[variant] || colors.info}`;
  div.innerHTML = html;
  document.getElementById('statusMessages').appendChild(div);
  // Keep only last 5 messages
  const messages = document.getElementById('statusMessages');
  while (messages.children.length > 5) {
    messages.removeChild(messages.firstElementChild);
  }
}

/**
 * Sets up the static cancellation box UI for solo registrations.
 * @param {Object} regInfo - Registration info (e.g., { registration_id, status, mode }).
 * @param {string|null} deadlineIso - ISO string for cancellation deadline.
 * @param {Object} refundMeta - Refund metadata (e.g., { feeCents, refundFlag, refundableOnCancellation }).
 */
function setupStaticCancelBox(regInfo, deadlineIso, refundMeta) {
  const box = document.getElementById('solo-cancel-box');
  if (!box) return;

  const statusLower = (regInfo.status || '').toLowerCase();
  if (['cancelled_by_user', 'cancelled_admin', 'refunded', 'expired'].includes(statusLower)) {
    box.classList.add('hidden');
    return;
  }
  if (regInfo.mode !== 'solo') {
    box.classList.add('hidden');
    return;
  }

  const intro = box.querySelector('.scb-intro');
  const refundEl = box.querySelector('.scb-refund');
  const btnStart = document.getElementById('scb-start');
  const confirmWrap = document.getElementById('scb-confirm');
  const btnYes = document.getElementById('scb-yes');
  const btnNo = document.getElementById('scb-no');
  const errEl = document.getElementById('scb-error');
  const okEl = document.getElementById('scb-success');

  let deadlineStr = 'the deadline';
  if (deadlineIso) {
    try {
      const d = new Date(deadlineIso);
      if (!isNaN(d)) deadlineStr = d.toLocaleString();
    } catch {}
  }

  if (intro) {
    intro.innerHTML = `<strong>Need to cancel?</strong> You can cancel your solo registration until <span class="font-semibold">${deadlineStr}</span>. This cannot be undone.`;
  }

  if (refundEl) {
    refundEl.textContent = '';
    if (refundMeta && typeof refundMeta.feeCents === 'number' && refundMeta.feeCents > 0) {
      if (refundMeta.refundFlag) {
        refundEl.innerHTML =
          '<span class="font-semibold">Refund:</span> A refund will be initiated automatically after cancellation (processing may take a few days).';
      } else if (refundMeta.refundableOnCancellation) {
        refundEl.innerHTML =
          '<span class="font-semibold">Refund:</span> Eligible if organizer approves (refund-on-cancellation enabled).';
      } else {
        refundEl.innerHTML = '<span class="font-semibold">Refund:</span> No refund for this event.';
      }
    }
  }

  function showError(msg) {
    if (!errEl) return;
    errEl.textContent = msg || 'Cancellation failed.';
    errEl.classList.remove('hidden');
    errEl.animate(
      [
        { transform: 'translateX(0)' },
        { transform: 'translateX(-4px)' },
        { transform: 'translateX(4px)' },
        { transform: 'translateX(0)' },
      ],
      { duration: 260 }
    );
  }

  function showSuccess(msg) {
    if (!okEl) return;
    okEl.textContent = msg || 'Cancelled.';
    okEl.classList.remove('hidden');
  }

  async function doCancel() {
    btnYes.disabled = true;
    btnNo.disabled = true;
    btnStart.disabled = true;
    btnYes.textContent = 'Cancelling…';
    try {
      const path = `/registrations/${encodeURIComponent(regInfo.registration_id)}`;
      const res = await apiFetch(path, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json().catch(() => ({}));
      regInfo.status = data.status || 'cancelled_by_user';
      showSuccess('Registration cancelled. If eligible, refund will process automatically.');
      confirmWrap.classList.add('hidden');
      btnStart.classList.add('hidden');
      document.getElementById('payNowBtn')?.classList.add('hidden');
      document.getElementById('chooseProviderBtn')?.classList.add('hidden');
      renderRegistration();
    } catch (e) {
      showError(e.message || 'Cancellation failed.');
      btnYes.disabled = false;
      btnNo.disabled = false;
      btnStart.disabled = false;
      btnYes.textContent = 'Yes, cancel';
    }
  }

  if (btnStart) {
    btnStart.onclick = () => {
      btnStart.classList.add('hidden');
      confirmWrap?.classList.remove('hidden');
      btnYes?.focus();
    };
  }

  if (btnNo) {
    btnNo.onclick = () => {
      confirmWrap.classList.add('hidden');
      btnStart.classList.remove('hidden');
    };
  }

  if (btnYes) {
    btnYes.onclick = doCancel;
  }

  box.classList.remove('hidden');
}

/**
 * Renders the user's registration status, payment badges, and action buttons.
 */
function renderRegistration() {
  const regSection = document.getElementById('registrationStatusSection');
  const regBody = document.getElementById('registrationStatusBody');
  const payNowBtn = document.getElementById('payNowBtn');
  const chooseProviderBtn = document.getElementById('chooseProviderBtn');
  const badgesEl = document.getElementById('eventBadges');

  if (!regSection || !regBody) return;

  if (!registrationData) {
    regBody.innerHTML = `<div class="text-sm">You are either not registered yet or your registration hasn't been detected. If you believe this is an error, try:<ul class="list-disc ml-5 mt-1"><li>Refreshing this page</li><li>Re-opening the event list and ensuring you're registered</li><li>Submitting the Solo registration form again (safe if you originally registered solo)</li></ul></div>`;
    if (payNowBtn) payNowBtn.classList.add('hidden');
    return;
  }

  if (registrationData.mode === 'team' && !registrationData.registration_id) {
    regBody.innerHTML = `<div class="text-sm">You are registered as part of a <strong>team</strong>. Detailed team registration data (and payment initiation) isn't yet available on this page without backend support.<br><br><em>Workaround:</em> The team creator can open the registration modal again or an organizer can assist with payment if required.</div>`;
    const existingRegBadge = badgesEl.querySelector('[data-badge="registration-mode"]');
    const teamText = 'Team registered';
    if (existingRegBadge) {
      existingRegBadge.textContent = teamText;
      existingRegBadge.className =
        'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200';
    } else {
      const b = document.createElement('span');
      b.dataset.badge = 'registration-mode';
      b.className =
        'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200';
      b.textContent = teamText;
      badgesEl.appendChild(b);
    }
  } else if (registrationData.mode === 'solo' && registrationData.registration_id) {
    const amount = (eventData?.fee_cents || 0) / 100;
    const paid =
      /paid|succeeded/i.test(registrationData.payment_status || '') ||
      /paid|succeeded/i.test(registrationData.status || '');
    let payLine = '';
    if ((eventData?.fee_cents || 0) > 0 && !paid) {
      payLine = `<div class="mt-2 text-xs ${payNowBtn && !payNowBtn.classList.contains('hidden') ? 'text-amber-700' : 'text-gray-600'}">Event fee: €${amount.toFixed(2)}</div>`;
    }
    regBody.innerHTML = `<div class="text-sm">${payLine || ' '}</div>`;
    const existingRegBadge = badgesEl.querySelector('[data-badge="registration-mode"]');
    const statusLower = (registrationData.status || '').toLowerCase();
    let regText = 'Solo registered';
    let regCls =
      'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-teal-50 text-teal-700 ring-1 ring-teal-200';
    if (/cancelled|expired|refunded/.test(statusLower)) {
      regText = 'Registration cancelled';
      regCls =
        'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-red-50 text-red-700 ring-1 ring-red-200';
    }
    if (existingRegBadge) {
      existingRegBadge.textContent = regText;
      existingRegBadge.className = regCls;
    } else {
      const b = document.createElement('span');
      b.dataset.badge = 'registration-mode';
      b.className = regCls;
      b.textContent = regText;
      badgesEl.appendChild(b);
    }
    const existingPaymentBadge = badgesEl.querySelector('[data-badge="payment-status"]');
    if ((eventData?.fee_cents || 0) > 0 && paid) {
      const text = `Paid €${amount.toFixed(2)}${registrationData.payment_provider ? ` via ${registrationData.payment_provider}` : ''}`;
      if (existingPaymentBadge) {
        existingPaymentBadge.textContent = text;
        existingPaymentBadge.className =
          'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200';
      } else {
        const b = document.createElement('span');
        b.dataset.badge = 'payment-status';
        b.className =
          'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200';
        b.textContent = text;
        badgesEl.appendChild(b);
      }
      // Hide registrationStatusSection if paid
      if (regSection) regSection.style.display = 'none';
    } else if (existingPaymentBadge) {
      existingPaymentBadge.remove();
      // Show registrationStatusSection if not paid
      if (regSection) regSection.style.display = '';
    } else {
      // Show registrationStatusSection if not paid
      if (regSection) regSection.style.display = '';
    }
  } else {
    regBody.innerHTML = `<div class="text-sm">Registration detected.</div>`;
    if (regSection) regSection.style.display = '';
  }

  if (registrationData.registration_id) {
    const unpaid =
      (eventData?.fee_cents || 0) > 0 &&
      !(
        /paid|succeeded/i.test(registrationData.payment_status || '') ||
        /paid|succeeded/i.test(registrationData.status || '')
      );
    if (unpaid && payNowBtn) {
      payNowBtn.classList.remove('hidden');
      payNowBtn.disabled = false;
      payNowBtn.onclick = () => startPaymentFlow({ quick: true });
    }
    if (unpaid && chooseProviderBtn) {
      chooseProviderBtn.classList.remove('hidden');
      chooseProviderBtn.disabled = false;
      chooseProviderBtn.onclick = () => startPaymentFlow({ quick: false });
    } else {
      payNowBtn?.classList.add('hidden');
      chooseProviderBtn?.classList.add('hidden');
    }
    if (registrationData.mode === 'solo') {
      const deadline = eventData?.registration_deadline || eventData?.payment_deadline || null;
      const refundMeta = {
        feeCents: eventData?.fee_cents || 0,
        refundFlag: !!registrationData.refund_flag,
        refundableOnCancellation: !!eventData?.refund_on_cancellation,
      };
      setupStaticCancelBox(registrationData, deadline, refundMeta);
    }
  }
}

/**
 * Fetches and updates the event plan.
 */
async function logEventPlan() {
  try {
    const urlParams = new URLSearchParams(window.location.search);
    const event_id = urlParams.get('id');
    if (!event_id) {
      throw new Error(ERROR_MESSAGES.noEventId);
    }
    document.getElementById('loadingSpinner').classList.remove('hidden');
    document.getElementById('statusMessages').textContent = '';

    // Fetch event data, plan, and profile in parallel
    const [planResponse, profileResponse, eventResponse] = await Promise.all([
      apiFetch(`/events/${event_id}/my_plan`, { credentials: 'include' }),
      apiFetch('/profile', { credentials: 'include' }).catch(() => ({ ok: false })),
      apiFetch(`/events/${event_id}`, { credentials: 'include' }).catch(() => ({ ok: false })),
    ]);

    if (!planResponse.ok) {
      throw new Error(`${ERROR_MESSAGES.fetchPlanFailed}${planResponse.status}`);
    }

    const planData = await planResponse.json();
    const profile = profileResponse.ok ? await profileResponse.json() : null;
    const userFirstName = profile?.first_name || null;
    eventData = eventResponse.ok ? await eventResponse.json() : null;

    // Update capacity bar if event data is available
    if (eventData && eventData.capacity) {
      const capacityWrap = document.getElementById('capacityBarWrap');
      const capacityBar = document.getElementById('capacityBar');
      const capacityLabel = document.getElementById('capacityLabel');
      const count = Number(eventData.attendee_count) || 0;
      const pct = Math.min(100, Math.max(0, (count / eventData.capacity) * 100));
      if (capacityWrap && capacityBar && capacityLabel) {
        capacityWrap.classList.remove('hidden');
        capacityBar.setAttribute('aria-valuemax', String(eventData.capacity));
        capacityBar.setAttribute('aria-valuenow', String(count));
        const fill = capacityBar.firstElementChild;
        if (fill) fill.style.width = pct + '%';
        capacityLabel.textContent = `${count}/${eventData.capacity} registered (${Math.round(pct)}%)`;
      }
    }

    if (eventData && eventData.after_party_location) {
      const afterPartySection = document.getElementById('afterPartySection');
      const afterPartyAddressSpan = document.getElementById('afterPartyAddress');
      if (afterPartySection && afterPartyAddressSpan) {
        afterPartySection.classList.remove('hidden');
        afterPartyAddressSpan.textContent =
          eventData.after_party_location.address_public || 'Location not yet published';
      }
    }

    if (planData.message === ERROR_MESSAGES.noPlanYet) {
      document.querySelectorAll('section[data-meal-type]').forEach((section) => {
        section.classList.add('hidden');
      });
      document.getElementById('noPlanSection').classList.remove('hidden');
      return;
    }

    if (!Array.isArray(planData.sections) || planData.sections.length === 0) {
      console.warn(ERROR_MESSAGES.noSections);
      pushMessage('No meal sections found for this event.', 'warn');
      return;
    }

    planData.sections.forEach((section) => {
      const mealType = section.meal;
      const sectionElement = document.querySelector(`section[data-meal-type="${mealType}"]`);
      if (!sectionElement) {
        console.warn(`Section not found for meal type: ${mealType}`);
        return;
      }
      updateMealSection(sectionElement, section, userFirstName, mealType);
    });

    // Load registration data
    await loadRegistration();
    renderRegistration();
  } catch (err) {
    console.error('Error fetching event plan:', err);
    pushMessage(
      err.message.includes(ERROR_MESSAGES.fetchPlanFailed)
        ? err.message
        : 'Failed to load event plan. Please try again later.',
      'error'
    );
  } finally {
    document.getElementById('loadingSpinner').classList.add('hidden');
    document.getElementById('actionButtons').hidden = false;
  }
}

/**
 * Updates a meal section with plan data.
 */
function updateMealSection(sectionElement, section, userFirstName, mealType) {
  const mealTitle = sectionElement.querySelector('[data-meal-title]');
  if (mealTitle) {
    const isHostUser = isHost(section, userFirstName);
    mealTitle.textContent = isHostUser
      ? `${MEAL_TITLES[mealType]} (You are the Host)`
      : `${MEAL_TITLES[mealType]} (You are Invited)`;
  }

  // Discover current user's registration for this event without mutating server state.
  // Strategy:
  // - Prefer /registrations/registration-status (lists all of my registrations with payment info)
  // - Fallback to /events/?participant=me to infer membership if listing not available
  // - Do NOT POST /registrations/solo here (that could reactivate a cancelled registration)
  async function loadRegistration() {
    registrationData = null;
    try {
      // Primary: get all my registrations and pick the one matching this event
      let regs = null;
      try {
        const res = await apiFetch('/registrations/registration-status', {
          method: 'GET',
          headers: authHeaders({ Accept: 'application/json' }),
        });
        if (res.ok) {
          const body = await res.json().catch(() => ({}));
          regs = Array.isArray(body?.registrations) ? body.registrations : Array.isArray(body) ? body : [];
        }
      } catch (e) {
        // ignore and try fallback
      }
      if (Array.isArray(regs)) {
        const matches = regs.filter((r) => String(r.event_id || r.eventId) === String(eventId));
        if (matches.length > 0) {
          // Sort by created_at descending (newest first)
          matches.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
          const match = matches[0];
          registrationData = {
            registration_id: match.registration_id || match.id || match.registrationId,
            status: match.status,
            payment_status: match.payment?.status || match.payment_status,
            payment_provider: match.payment?.provider || match.payment_provider,
            payment_id: match.payment?.payment_id || match.payment_id,
            refund_flag: match.refund_flag,
            mode: match.registration_mode || match.mode || (match.team_size > 1 ? 'team' : 'solo'),
            team_size: match.team_size || 1,
            team_id: match.team_id,
            amount_due_cents: match.amount_due_cents,
          };
          registrationStatusLoaded = true;
          return;
        }
      }
      // Fallback inference: check if I'm a participant of this event
      try {
        const resp = await fetchJson('/events/?participant=me');
        if (Array.isArray(resp)) {
          const found = resp.find((e) => (e.id || e._id) === eventId);
          if (found) {
            registrationData = { status: 'registered', mode: 'unknown' };
          }
        }
      } catch (e) {
        // ignore
      }
    } catch (e) {
      console.warn('loadRegistration failed', e);
    }
  }

  const hostName = sectionElement.querySelector('[data-host-name]');
  if (hostName) {
    hostName.textContent = section.host_first_name || 'Host not specified';
  }

  function ShowSoloForm() {
    // Show form only if user NOT registered yet
    if (
      registrationData &&
      (registrationData.mode === 'solo' || registrationData.mode === 'team')
    ) {
      return; // already registered
    }
    // Event must be open for registration
    if (!eventData) return;
    const status = (eventData.status || '').toLowerCase();
    // If event is not open or coming soon, do not show the form and ensure register button is disabled
    if (!['open', 'coming_soon'].includes(status)) {
      // Hide solo form and show a helpful message
      soloRegSection.classList.add('hidden');
      pushMessage('Registration for this event is not open.', 'warn');
      return; // conservative: only open
    }
    // If registration_deadline passed, don't show the form
    if (eventData.registration_deadline) {
      try {
        const dl = new Date(eventData.registration_deadline);
        if (!isNaN(dl) && dl.getTime() < Date.now()) {
          soloRegSection.classList.add('hidden');
          pushMessage('Registration deadline has passed for this event.', 'warn');
          return;
        }
      } catch (e) {}
    }
    // Prefill values
    if (profileData) {
      if (srDiet && profileData.default_dietary_preference) {
        srDiet.value = profileData.default_dietary_preference;
      }
      if (srKitchen) srKitchen.checked = !!profileData.kitchen_available;
      if (srMain) srMain.checked = !!profileData.main_course_possible;
    }
    updateMainCourseAvailability();
    soloRegSection.classList.remove('hidden');
  }

  const locationLabel = sectionElement.querySelector('[data-location-label]');
  const radiusBanner = sectionElement.querySelector('[data-radius-banner]');
  const mapDiv = sectionElement.querySelector(`#${mealType}-map`);

  // Case 1: Event passed (host_location is null)
  if (!section.host_location) {
    if (radiusBanner) radiusBanner.classList.add('hidden');
    if (locationLabel) {
      locationLabel.parentElement.style.display = '';
      locationLabel.textContent = 'Event passed';
    }
    if (mapDiv) {
      mapDiv.innerHTML = '<div class="map-preview flex items-center justify-center text-gray-500 text-xs">Event passed</div>';
    }
    return;
  }

  // Case 2: Exact address (approx_radius_m === 0)
  if (section.host_location.approx_radius_m === 0) {
    if (radiusBanner) radiusBanner.classList.add('hidden');
    if (locationLabel) {
      locationLabel.parentElement.style.display = '';
      locationLabel.textContent =
        `${section.host_location.city || ''} ${section.host_location.street || ''} ${section.host_location.street_no || ''}`.trim() ||
        'Address not yet published';
    }
    if (mapDiv) {
      try {
        const diet = srDiet?.value || 'omnivore';
        const kitchen_available = !!srKitchen?.checked;
        const main_course_possible = !!srMain?.checked;
        const courseRadio = srCourseChoices?.querySelector('input[type=radio]:checked');
        const course_preference = courseRadio ? courseRadio.value || '' : '';
        if (course_preference === 'main' && !main_course_possible) {
          throw new Error(
            'Main Course preference requires that your kitchen is marked Main Course Possible.'
          );
        }
        const payload = {
          event_id: eventId,
          dietary_preference: diet,
          kitchen_available,
          main_course_possible,
        };
        if (course_preference) {
          payload.course_preference = course_preference;
        }
        const headers = { 'Content-Type': 'application/json', Accept: 'application/json' };
        const resp = await apiFetch('/registrations/solo', {
          method: 'POST',
          headers,
          body: JSON.stringify(payload),
        });
        const body = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          // Handle blocked re-registration (HTTP 409) specially
          if (resp.status === 409) {
            const msg = body.detail || body.message || 'You already have a registration in another event.';
            // Show message and disable submit to prevent repeat attempts
            srStatus.textContent = msg;
            srStatus.classList.remove('text-gray-500');
            srStatus.classList.add('text-red-600');
            // Optionally show link to existing registration if provided
            if (body.existing_registration && body.existing_registration.registration_id) {
              const rid = body.existing_registration.registration_id;
              const link = document.createElement('a');
              link.href = `/registration.html?id=${encodeURIComponent(rid)}`;
              link.textContent = 'View your existing registration';
              link.className = 'ml-2 text-sm text-indigo-600 underline';
              srStatus.appendChild(document.createElement('br'));
              srStatus.appendChild(link);
            }
            // Keep submit disabled and exit
            return;
          }
          const msg = body.detail || body.message || `Registration failed (HTTP ${resp.status})`;
          throw new Error(msg);
        }
        registrationData = registrationData || {};
        registrationData.mode = 'solo';
        registrationData.registration_id = body.registration_id || body.id || body.registrationId;
        registrationData.amount_cents = body.amount_cents;
        // hide form, render status
        soloRegSection.classList.add('hidden');
        renderRegistration();
        pushMessage('Registered successfully.', 'success');
        // Redirect to payment if fee due
        if ((eventData?.fee_cents || 0) > 0) {
          // Defer a tick to allow UI update
          setTimeout(() => {
            if (payNowBtn && !payNowBtn.classList.contains('hidden')) {
              payNowBtn.click();
            }
          }, 600);
        }
      } catch (err) {
        srStatus.textContent = err && err.message ? err.message : 'Registration failed';
        srStatus.classList.remove('text-gray-500');
        srStatus.classList.add('text-red-600');
        console.error(err);
      } finally {
        // If srStatus indicates a blocking condition (e.g., 409) we keep the button disabled to avoid retries
        if (!srStatus.textContent || !/already have a registration|already in an event/i.test(srStatus.textContent)) {
          srSubmit.disabled = false;
        }
        initMap(
          `${mealType}-map`,
          section.host_location.center.lat,
          section.host_location.center.lon
        );
      } catch (mapErr) {
        console.error(`${ERROR_MESSAGES.mapInitFailed}${mapErr}`);
        mapDiv.innerHTML = "<p class='text-gray-500 text-xs p-2'>Map could not be loaded.</p>";
      }
    }
  }

  function renderRegistration() {
    regSection.classList.remove('hidden');
    if (!registrationData) {
      regBody.innerHTML = `<div class="text-sm">You are either not registered yet or your registration hasn't been detected. If you believe this is an error, try:<ul class="list-disc ml-5 mt-1"><li>Refreshing this page</li><li>Re-opening the event list and ensuring you're registered</li><li>Submitting the Solo registration form again (safe if you originally registered solo)</li></ul></div>`;
      if (payNowBtn) payNowBtn.classList.add('hidden');
      return;
    }
    // Provide summary depending on mode
    if (registrationData.mode === 'team') {
      const teamSize = registrationData.team_size || 2;
      const totalAmount = registrationData.amount_due_cents 
        ? (registrationData.amount_due_cents / 100).toFixed(2)
        : ((eventData?.fee_cents || 0) * teamSize / 100).toFixed(2);
      const paid =
        /paid|succeeded/i.test(registrationData.payment_status || '') ||
        /paid|succeeded/i.test(registrationData.status || '');
      
      // Show payment info if not paid
      let payLine = '';
      if ((eventData?.fee_cents || 0) > 0 && !paid) {
        payLine = `<div class="mt-2 text-xs ${payNowBtn && !payNowBtn.classList.contains('hidden') ? 'text-amber-700' : 'text-gray-600'}">Team fee (${teamSize} people): €${totalAmount}</div>`;
      }
      regBody.innerHTML = `<div class="text-sm">${payLine || ' '}</div>`;
      
      // Registration badge (team)
      const existingRegBadge = badgesEl.querySelector('[data-badge="registration-mode"]');
      const teamText = `Team registered (${teamSize} people)`;
      const statusLower = (registrationData.status || '').toLowerCase();
      let regCls =
        'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200';
      if (/cancelled|expired|refunded/.test(statusLower)) {
        regCls =
          'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-red-50 text-red-700 ring-1 ring-red-200';
      }
      if (existingRegBadge) {
        existingRegBadge.textContent = teamText;
        existingRegBadge.className = regCls;
      } else {
        const b = document.createElement('span');
        b.dataset.badge = 'registration-mode';
        b.className = regCls;
        b.textContent = teamText;
        badgesEl.appendChild(b);
      }
      
      // Update / insert payment badge
      const existingPaymentBadge = badgesEl.querySelector('[data-badge="payment-status"]');
      if ((eventData?.fee_cents || 0) > 0 && paid) {
        const text = `Paid €${totalAmount}${registrationData.payment_provider ? ` via ${registrationData.payment_provider}` : ''}`;
        if (existingPaymentBadge) {
          existingPaymentBadge.textContent = text;
          existingPaymentBadge.className =
            'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200';
        } else {
          const b = document.createElement('span');
          b.dataset.badge = 'payment-status';
          b.className =
            'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200';
          b.textContent = text;
          badgesEl.appendChild(b);
        }
      } else if (existingPaymentBadge) {
        // If not paid yet, remove any previous payment badge
        existingPaymentBadge.remove();
      }
    } else if (registrationData.mode === 'solo' && registrationData.registration_id) {
      const amount = (eventData?.fee_cents || 0) / 100;
      const paid =
        /paid|succeeded/i.test(registrationData.payment_status || '') ||
        /paid|succeeded/i.test(registrationData.status || '');
      // Move payment info to badges instead of registration body per requirement
      let payLine = '';
      if ((eventData?.fee_cents || 0) > 0 && !paid) {
        payLine = `<div class="mt-2 text-xs ${payNowBtn && !payNowBtn.classList.contains('hidden') ? 'text-amber-700' : 'text-gray-600'}">Event fee: €${amount.toFixed(2)}</div>`;
      }
      regBody.innerHTML = `<div class="text-sm">${payLine || ' '}</div>`;
      // Registration badge (solo / cancelled etc.)
      const existingRegBadge = badgesEl.querySelector('[data-badge="registration-mode"]');
      const statusLower = (registrationData.status || '').toLowerCase();
      let regText = 'Solo registered';
      // If this solo registration was created via an invitation, show 'Invited' and the invited email
      const invite = registrationData.invitation || null;
      if (invite) {
        regText = invite.invitation_status === 'pending' || invite.invitation_status === 'accepted' ? 'Invited' : 'Invited';
        // If invited, do not show payment line (invited solo users don't pay)
        payLine = '';
      }
      let regCls =
        'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-teal-50 text-teal-700 ring-1 ring-teal-200';
      if (/cancelled|expired|refunded/.test(statusLower)) {
        regText = 'Registration cancelled';
        regCls =
          'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-red-50 text-red-700 ring-1 ring-red-200';
      }
      if (existingRegBadge) {
        existingRegBadge.textContent = regText;
        existingRegBadge.className = regCls;
      } else {
        const b = document.createElement('span');
        b.dataset.badge = 'registration-mode';
        b.className = regCls;
        b.textContent = regText;
        badgesEl.appendChild(b);
      }
      // Update / insert payment badge
      const existingPaymentBadge = badgesEl.querySelector('[data-badge="payment-status"]');
      if ((eventData?.fee_cents || 0) > 0 && paid) {
        const text = `Paid €${amount.toFixed(2)}${registrationData.payment_provider ? ` via ${registrationData.payment_provider}` : ''}`;
        if (existingPaymentBadge) {
          existingPaymentBadge.textContent = text;
          existingPaymentBadge.className =
            'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200';
        } else {
          const b = document.createElement('span');
          b.dataset.badge = 'payment-status';
          b.className =
            'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200';
          b.textContent = text;
          badgesEl.appendChild(b);
        }
      } else if (existingPaymentBadge) {
        // If not paid yet, remove any previous payment badge (will show fee badge from loadEvent)
        existingPaymentBadge.remove();
      }
    } else {
      // Fallback generic
      regBody.innerHTML = `<div class="text-sm">Registration detected.</div>`;
    }
  // Future: display status & payment
    if (registrationData.registration_id) {
      const unpaid =
        (eventData?.fee_cents || 0) > 0 &&
        !(
          /paid|succeeded/i.test(registrationData.payment_status || '') ||
          /paid|succeeded/i.test(registrationData.status || '')
        );
      // If event is closed or registration deadline passed, disable payment actions
      let registrationAllowed = true;
      const status = (eventData?.status || '').toLowerCase();
      if (!['open', 'coming_soon'].includes(status)) registrationAllowed = false;
      if (eventData?.registration_deadline) {
        try {
          const dl = new Date(eventData.registration_deadline);
          if (!isNaN(dl) && dl.getTime() < Date.now()) registrationAllowed = false;
        } catch (e) {}
      }

      if (unpaid && payNowBtn) {
        payNowBtn.classList.remove('hidden');
        payNowBtn.disabled = !registrationAllowed;
        payNowBtn.onclick = () => startPaymentFlow({ quick: true });
      }
      if (unpaid && chooseProviderBtn) {
        chooseProviderBtn.classList.remove('hidden');
        chooseProviderBtn.disabled = !registrationAllowed;
        chooseProviderBtn.onclick = () => startPaymentFlow({ quick: false });
      } else {
        // Paid -> hide buttons
        payNowBtn && payNowBtn.classList.add('hidden');
        chooseProviderBtn && chooseProviderBtn.classList.add('hidden');
      }
      // Inject cancellation UI
      if (registrationData.mode === 'solo') {
        const deadline = eventData?.registration_deadline || eventData?.payment_deadline || null;
        const refundMeta = {
          feeCents: eventData?.fee_cents || 0,
          refundFlag: !!registrationData.refund_flag,
          refundableOnCancellation: !!eventData?.refund_on_cancellation,
        };
        setupStaticCancelBox(registrationData, deadline, refundMeta);
      } else if (registrationData.mode === 'team') {
        const deadline = eventData?.registration_deadline || eventData?.payment_deadline || null;
        const refundMeta = {
          feeCents: eventData?.fee_cents || 0,
          refundFlag: !!registrationData.refund_flag,
          refundableOnCancellation: !!eventData?.refund_on_cancellation,
        };
        setupTeamCancelBox(registrationData, deadline, refundMeta);
  // Case 3: Approximate address (approx_radius_m > 0)
  else if (section.host_location.approx_radius_m > 0) {
    if (radiusBanner) radiusBanner.classList.remove('hidden');
    if (locationLabel) locationLabel.parentElement.style.display = 'none';
    if (mapDiv) {
      try {
        initMap(
          `${mealType}-map`,
          section.host_location.center.lat,
          section.host_location.center.lon,
          section.host_location.approx_radius_m
        );
      } catch (mapErr) {
        console.error(`${ERROR_MESSAGES.mapInitFailed}${mapErr}`);
        mapDiv.innerHTML = "<p class='text-gray-500 text-xs p-2'>Map could not be loaded.</p>";
      }
    }
    // Hide or show details buttons depending on cancellation status
    function updateDetailsButtonVisibility() {
      const statusLower = (registrationData.status || '').toLowerCase();
      const isCancelled = /cancelled|expired|refunded/.test(statusLower);
      // selectors to hide; adjust as needed for different frontends
      const selectors = ['#detailsBtn', '.details-button', '[data-role="details"]'];
      selectors.forEach((sel) => {
        try {
          const els = Array.from(document.querySelectorAll(sel));
          els.forEach((el) => {
            if (isCancelled) el.classList.add('hidden');
            else el.classList.remove('hidden');
          });
        } catch (e) {
          // ignore selector errors
        }
      });
    }

    // Run once after rendering
    try { updateDetailsButtonVisibility(); } catch (e) {}
  }
}



/**
 * Checks if the current user is the host for a section.
 */
function isHost(section, userFirstName) {
  return userFirstName?.toLowerCase() === section.host_first_name?.toLowerCase();
}

/**
 * Initializes a Leaflet map.
 */
function initMap(containerId, lat, lon, radius = 0) {
  const mapDiv = document.getElementById(containerId);
  if (!mapDiv) return;
  if (mapDiv.clientHeight === 0) mapDiv.style.height = '200px';
  const map = L.map(mapDiv).setView([lat, lon], 15);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors',
  }).addTo(map);
  const mealType = containerId.replace('-map', '');
  const color = MEAL_COLORS[mealType] || DEFAULT_COLOR;
  if (radius > 0) {
    L.circle([lat, lon], { radius, color, fillColor: color, fillOpacity: 0.2 }).addTo(map);
  } else {
    L.marker([lat, lon]).addTo(map);
  }
  setTimeout(() => map.invalidateSize(), 100);
}

  function buildProviderChooser(providers, def) {
    const overlay = document.createElement('div');
    overlay.className =
      'fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4';
    const panel = document.createElement('div');
    panel.className = 'bg-white rounded-lg shadow-xl w-full max-w-sm p-5 space-y-4';
    panel.innerHTML =
      '<h3 class="text-sm font-semibold text-gray-700">Select Payment Provider</h3>';
    const list = document.createElement('div');
    list.className = 'space-y-2';
    providers.forEach((p) => {
      const key = (p || '').toLowerCase();
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'w-full px-3 py-2 rounded border text-sm flex items-center justify-between gap-2 hover:bg-gray-50';
      btn.dataset.provider = key;
      if (key === 'paypal') {
        btn.innerHTML = '<div class="flex items-center gap-2"><img alt="PayPal" src="https://www.paypalobjects.com/webstatic/icon/pp258.png" class="w-5 h-5" /><span class="font-medium">Pay with PayPal</span></div>' + (key === def ? '<span class="text-xs text-teal-600">(default)</span>' : '');
      } else if (key === 'stripe') {
        btn.innerHTML = '<div class="flex items-center gap-2"><svg viewBox="0 0 28 28" class="w-5 h-5" aria-hidden="true"><path fill="#635BFF" d="M.5 9.3l7.8-1.4v13.6c0 3.2-1.9 4.6-4.8 4.6-1.3 0-2.2-.3-3-1v-3.8c.6.3 1.3.5 2 .5.8 0 1.2-.3 1.2-1.2V9.3zM27.5 14.9c0-4.1-2.5-5.7-7.4-6.5-3.5-.6-4.2-1-4.2-2 0-.8.8-1.4 2.2-1.4 1.3 0 2.6.3 3.9.8l.6-4c-1.5-.5-3.1-.8-4.7-.8-4 0-6.8 2.1-6.8 5.5 0 3.8 2.5 5.2 6.8 6 3.3.6 4.2 1.1 4.2 2.1 0 1-1 1.6-2.5 1.6-1.6 0-3.3-.4-4.8-1.1l-.7 4.1c1.8.7 3.8 1 5.7 1 4.2 0 7.7-2.1 7.7-5.8z"/></svg><span class="font-medium">Pay with Stripe</span></div>' + (key === def ? '<span class="text-xs text-teal-600">(default)</span>' : '');
      } else {
        btn.innerHTML = `<div class="flex items-center gap-2"><span class="font-medium">Pay with ${key.charAt(0).toUpperCase()}${key.slice(1)}</span></div>` + (key === def ? '<span class="text-xs text-teal-600">(default)</span>' : '');
      }
      list.appendChild(btn);
/**
 * Formats a time string (HH:MM) to a localized time.
 */
function formatTime(timeString) {
  try {
    const [hours, minutes] = timeString.split(':').map(Number);
    return new Date(0, 0, 0, hours, minutes).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch (e) {
    return timeString;
  }
}

/**
 * Fetches the user's registration data for the current event.
 */
async function loadRegistration() {
  try {
    const urlParams = new URLSearchParams(window.location.search);
    const event_id = urlParams.get('id');
    if (!event_id) return;

    const res = await apiFetch('/registrations/registration-status', {
      method: 'GET',
      credentials: 'include',
    });
    if (res.ok) {
      const data = await res.json();
      const regs = Array.isArray(data.registrations) ? data.registrations : [];
      const match = regs.find((r) => String(r.event_id) === event_id);
      if (match) {
        registrationData = {
          registration_id: match.registration_id || match.id,
          status: match.status,
          payment_status: match.payment?.status || match.payment_status,
          payment_provider: match.payment?.provider || match.payment_provider,
          payment_id: match.payment?.payment_id || match.payment_id,
          refund_flag: match.refund_flag,
          mode: match.registration_mode || match.mode || 'solo',
        };
      }
    }
  } catch (e) {
    console.warn('Failed to load registration:', e);
  }
}

/**
 * Placeholder for payment flow.
 */
async function startPaymentFlow({ quick }) {
  pushMessage('Functionality not implemented yet.', 'error');
}

// Call the function on page load
window.addEventListener('DOMContentLoaded', logEventPlan);
