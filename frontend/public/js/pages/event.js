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
let profileData = null;
let paymentProvidersState = { providers: [], defaultProvider: null, fetched: false, lastError: null };

const MANUAL_MESSAGE_MIN_LEN = 12;
const MANUAL_MESSAGE_MAX_LEN = 800;

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

function authHeaders(init = {}) {
  return Object.assign({ Accept: 'application/json' }, init);
}

async function fetchJson(path, options) {
  const opts = Object.assign({ method: 'GET' }, options || {});
  opts.headers = authHeaders(opts.headers || {});
  const res = await apiFetch(path, opts);
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.response = res;
    throw err;
  }
  return res
    .clone()
    .json()
    .catch(() => ({}));
}

function providerLabel(key) {
  const map = {
    paypal: 'PayPal',
    stripe: 'Stripe',
    others: 'Manual review',
  };
  const normalized = (key || '').toLowerCase();
  if (map[normalized]) return map[normalized];
  if (!normalized) return '';
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function formatList(items) {
  if (!Array.isArray(items) || items.length === 0) return '';
  if (items.length === 1) return items[0];
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  const head = items.slice(0, -1).join(', ');
  return `${head}, and ${items[items.length - 1]}`;
}

function describePaymentOptions(providers) {
  if (!Array.isArray(providers) || providers.length === 0) return '';
  const hasManual = providers.includes('others');
  const online = providers.filter((p) => p !== 'others');
  if (!online.length && hasManual) {
    return '<p class="mt-1 text-xs text-gray-600">Online payments are unavailable. Use "Request manual review" to notify the organizers.</p>';
  }
  if (!online.length) return '';
  const labels = online.map(providerLabel).filter(Boolean);
  if (!labels.length) return '';
  let text = `Pay online via ${formatList(labels)}.`;
  if (hasManual) {
    text += ' Prefer a different method? Choose manual review to leave a note for the organizers.';
  }
  return `<p class="mt-1 text-xs text-gray-600">${text}</p>`;
}

async function fetchPaymentProviders(force = false) {
  if (!force && paymentProvidersState.fetched) return paymentProvidersState;
  let providers = [];
  let defaultProvider = null;
  let lastError = null;
  try {
    const res = await apiFetch('/payments/providers', { credentials: 'include' });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.clone().json().catch(() => ({}));
    const raw = Array.isArray(data?.providers)
      ? data.providers
      : Array.isArray(data)
      ? data
      : [];
    providers = raw.filter((p) => typeof p === 'string');
    if (typeof data?.default === 'string') {
      defaultProvider = data.default.toLowerCase();
    }
  } catch (err) {
    lastError = err;
    providers = [];
    defaultProvider = null;
  }

  const allowed = new Set(['paypal', 'stripe', 'others']);
  const normalized = Array.from(
    new Set(
      (providers || [])
        .map((p) => (p || '').toString().toLowerCase())
        .filter((p) => allowed.has(p))
    )
  );

  if (!normalized.includes('others')) normalized.push('others');
  if (!normalized.length) normalized.push('others');

  let activeDefault = defaultProvider ? defaultProvider.toLowerCase() : null;
  if (!activeDefault || !normalized.includes(activeDefault)) {
    activeDefault = normalized.find((p) => p !== 'others') || normalized[0];
  }

  paymentProvidersState = {
    providers: normalized,
    defaultProvider: activeDefault,
    fetched: true,
    lastError,
  };
  return paymentProvidersState;
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
    // show box but with message that cancellation is not available
    box.classList.remove('hidden');
    const errEl2 = document.getElementById('scb-error');
    if (errEl2) {
      errEl2.textContent = 'Cancellation not available for this registration (already cancelled/expired/refunded).';
      errEl2.classList.remove('hidden');
    }
    if (document.getElementById('scb-start')) document.getElementById('scb-start').disabled = true;
    return;
  }
  if (regInfo.mode !== 'solo') {
    // don't hide silently; show message elsewhere
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

  // If we don't yet have a registration id (data still loading), show the box
  // but disable controls so users aren't able to perform cancellation until
  // the registration is fully loaded.
  if (!regInfo.registration_id) {
    box.classList.remove('hidden');
    if (errEl) {
      errEl.textContent = 'Registration details are still loading. Cancellation is not yet available.';
      errEl.classList.remove('hidden');
    }
    if (btnStart) btnStart.disabled = true;
    if (btnYes) btnYes.disabled = true;
    if (btnNo) btnNo.disabled = true;
    return;
  }

  box.classList.remove('hidden');
}

function setupTeamCancelBox(regInfo, deadlineIso, refundMeta) {
  const box = document.getElementById('team-cancel-box');
  if (!box) return;

  const intro = box.querySelector('.tcb-intro');
  const refundEl = box.querySelector('.tcb-refund');
  const btnStart = document.getElementById('tcb-start');
  const confirmWrap = document.getElementById('tcb-confirm');
  const btnYes = document.getElementById('tcb-yes');
  const btnNo = document.getElementById('tcb-no');
  const errEl = document.getElementById('tcb-error');
  const okEl = document.getElementById('tcb-success');
  const originalYesText = btnYes ? btnYes.textContent : '';

  function showError(msg) {
    if (!errEl) return;
    errEl.textContent = msg || 'Cancellation failed.';
    errEl.classList.remove('hidden');
  }

  function showSuccess(msg) {
    if (!okEl) return;
    okEl.textContent = msg || 'Cancelled.';
    okEl.classList.remove('hidden');
  }

  const statusLower = (regInfo.status || '').toLowerCase();
  if (regInfo.mode !== 'team' || /cancelled|expired|refunded/.test(statusLower)) {
    box.classList.add('hidden');
    return;
  }

  // If we don't have a registration id for this member yet, surface the box disabled
  if (!regInfo.registration_id) {
    console.warn('No registration_id present for team cancellation; showing disabled box to inform user', regInfo);
    box.classList.remove('hidden');
    showError('Registration details are still loading. Please refresh the page or try again later.');
    if (btnStart) btnStart.disabled = true;
    if (btnYes) btnYes.disabled = true;
    if (btnNo) btnNo.disabled = true;
    return;
  }

  let deadlineStr = 'the deadline';
  if (deadlineIso) {
    try {
      const d = new Date(deadlineIso);
      if (!isNaN(d)) deadlineStr = d.toLocaleString();
    } catch {}
  }

  if (intro) {
    intro.innerHTML = `<strong>Need to cancel?</strong> You can cancel your team participation until <span class="font-semibold">${deadlineStr}</span>.`;
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

  async function determineRoleAndWire() {
    try {
      const teamId = regInfo.team_id || registrationData?.team_id;
      if (!teamId) {
        console.warn('No team id available for team cancel box', regInfo, registrationData);
        box.classList.remove('hidden');
        showError('Team details are not available yet. Please refresh the page.');
        if (btnStart) btnStart.disabled = true;
        if (btnYes) btnYes.disabled = true;
        if (btnNo) btnNo.disabled = true;
        return;
      }
      let team = null;
      try {
        team = await fetchJson(`/registrations/teams/${encodeURIComponent(teamId)}`);
      } catch (e) {
        console.warn('Failed to fetch team details', e);
        // show box but disable controls so user sees cancellation area and error
        box.classList.remove('hidden');
        showError('Unable to fetch full team details. Please try again or contact organizer.');
        if (btnStart) btnStart.disabled = true;
        if (btnYes) btnYes.disabled = true;
        if (btnNo) btnNo.disabled = true;
        return;
      }
      if (!team || typeof team !== 'object') {
        console.warn('Team endpoint returned invalid data', team);
        box.classList.add('hidden');
        return;
      }
      const creatorEmail = team.created_by_email || team.created_by || null;
      const myEmail = profileData?.email || window.__USER_EMAIL__ || null;
      const isCreator = creatorEmail && myEmail
        ? creatorEmail.toLowerCase() === myEmail.toLowerCase()
        : false;

      console.debug('Team cancel wiring: teamId=%s, isCreator=%s, creatorEmail=%s, myEmail=%s', teamId, isCreator, creatorEmail, myEmail);

      function disableControls() {
        if (btnStart) btnStart.disabled = true;
        if (btnYes) btnYes.disabled = true;
        if (btnNo) btnNo.disabled = true;
      }

      function enableControls() {
        if (btnStart) btnStart.disabled = false;
        if (btnYes) btnYes.disabled = false;
        if (btnNo) btnNo.disabled = false;
      }

      async function doCancelAsCreator() {
        disableControls();
        if (btnYes) btnYes.textContent = 'Cancelling…';
        try {
          const path = `/registrations/teams/${encodeURIComponent(teamId)}/cancel`;
          const res = await apiFetch(path, {
            method: 'POST',
            headers: authHeaders({}),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          showSuccess('Team cancelled. Partner(s) notified.');
          confirmWrap?.classList.add('hidden');
          btnStart?.classList.add('hidden');
          registrationData.status = 'cancelled_by_user';
          renderRegistration();
        } catch (e) {
          showError(e.message || 'Cancellation failed');
          enableControls();
          if (btnYes) btnYes.textContent = originalYesText || 'Yes, cancel';
        }
      }

      async function doCancelAsMember() {
        disableControls();
        if (btnYes) btnYes.textContent = 'Cancelling…';
        try {
          const path = `/registrations/teams/${encodeURIComponent(teamId)}/members/${encodeURIComponent(
            regInfo.registration_id
          )}/cancel`;
          const res = await apiFetch(path, {
            method: 'POST',
            headers: authHeaders({}),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          showSuccess('Your participation was cancelled. Creator has been notified.');
          confirmWrap?.classList.add('hidden');
          btnStart?.classList.add('hidden');
          registrationData.status = 'cancelled_by_user';
          renderRegistration();
        } catch (e) {
          showError(e.message || 'Cancellation failed');
          enableControls();
          if (btnYes) btnYes.textContent = originalYesText || 'Yes, cancel';
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
          confirmWrap?.classList.add('hidden');
          btnStart?.classList.remove('hidden');
        };
      }
      if (btnYes) {
        btnYes.onclick = () => {
          if (isCreator) return doCancelAsCreator();
          return doCancelAsMember();
        };
      }

      box.classList.remove('hidden');
    } catch (e) {
      console.warn('Team cancel wiring failed', e);
      box.classList.add('hidden');
    }
  }

  determineRoleAndWire();
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
  const soloCancelBox = document.getElementById('solo-cancel-box');
  const teamCancelBox = document.getElementById('team-cancel-box');

  if (payNowBtn && !payNowBtn.dataset.baseText) {
    payNowBtn.dataset.baseText = (payNowBtn.textContent || '').trim() || 'Pay now';
  }
  if (chooseProviderBtn && !chooseProviderBtn.dataset.baseText) {
    chooseProviderBtn.dataset.baseText = (chooseProviderBtn.textContent || '').trim() || 'Choose provider';
  }

  const providerInfo = paymentProvidersState || {};
  const providers = Array.isArray(providerInfo.providers) ? providerInfo.providers : [];
  const hasManualProvider = providers.includes('others');
  const onlineProviders = providers.filter((p) => p !== 'others');
  const manualOnly = providers.length === 1 && providers[0] === 'others';

  soloCancelBox?.classList.add('hidden');
  teamCancelBox?.classList.add('hidden');

  if (!regSection || !regBody) return;

  if (!registrationData) {
    regBody.innerHTML = `<div class="text-sm">You are either not registered yet or your registration hasn't been detected. If you believe this is an error, try:<ul class="list-disc ml-5 mt-1"><li>Refreshing this page</li><li>Re-opening the event list and ensuring you're registered</li><li>Submitting the Solo registration form again (safe if you originally registered solo)</li></ul></div>`;
    if (payNowBtn) payNowBtn.classList.add('hidden');
    return;
  }

  if (registrationData.mode === 'team' && !registrationData.registration_id) {
    const teamSizeText = registrationData.team_size ? `Team size: ${registrationData.team_size}` : '';
    const paidSummary = registrationData.payment_status ? `Status: ${registrationData.payment_status}` : '';
    regBody.innerHTML = `<div class="text-sm">You are registered as part of a <strong>team</strong>. ${teamSizeText ? `<div class="text-xs text-gray-600">${teamSizeText}</div>` : ''}${paidSummary ? `<div class="text-xs text-gray-600">${paidSummary}</div>` : ''}<br><br>Detailed team registration data (and payment initiation) isn't yet available on this page without backend support.<br><br><em>Workaround:</em> The team creator can open the registration modal again or an organizer can assist with payment if required.</div>`;
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
    let paymentSummary = '';
    if ((eventData?.fee_cents || 0) > 0 && !paid) {
      payLine = `<div class="mt-2 text-xs ${payNowBtn && !payNowBtn.classList.contains('hidden') ? 'text-amber-700' : 'text-gray-600'}">Event fee: €${amount.toFixed(2)}</div>`;
      paymentSummary = describePaymentOptions(providers);
    }
    const payContent = [payLine, paymentSummary].filter(Boolean).join('');

    // Build registration summary block
    const paidText = (eventData?.fee_cents || 0) > 0
      ? paid
        ? `Paid €${amount.toFixed(2)}${registrationData.payment_provider ? ` via ${registrationData.payment_provider}` : ''}`
        : `Unpaid — €${amount.toFixed(2)}`
      : 'No fee';

    const refundStatus = (() => {
      if (/refunded/.test((registrationData.status || '').toLowerCase())) return 'Refunded';
      if (registrationData.refund_flag) return 'Refund pending';
      if (eventData?.refund_on_cancellation) return 'Refund eligible on cancellation';
      return 'No refund available';
    })();

    const modeText = registrationData.mode === 'solo' ? 'Solo' : (registrationData.mode || 'Registration');

    const teamSizeText = registrationData.team_size ? `Team size: ${registrationData.team_size}` : '';

    const summaryHtml = `
      <div class="text-sm space-y-2">
        <div class="flex items-center justify-between text-sm">
          <div><span class="font-medium">${modeText} registration</span>${teamSizeText ? ` — <span class="text-gray-600">${teamSizeText}</span>` : ''}</div>
          <div class="text-xs text-gray-500">Status: <span class="font-semibold">${registrationData.status || 'unknown'}</span></div>
        </div>
        <div class="flex items-center justify-between text-sm">
          <div class="text-xs text-gray-700">${paidText}</div>
          <div class="text-xs text-gray-500">${refundStatus}</div>
        </div>
        ${payContent}
      </div>`;

    regBody.innerHTML = summaryHtml;
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

  // Payment buttons only make sense when we have a registration identifier.
  const hasRegistrationId = !!registrationData.registration_id;
  if (hasRegistrationId) {
    const unpaid =
      (eventData?.fee_cents || 0) > 0 &&
      !(
        /paid|succeeded/i.test(registrationData.payment_status || '') ||
        /paid|succeeded/i.test(registrationData.status || '')
      );
    if (unpaid && payNowBtn) {
      payNowBtn.classList.remove('hidden');
      payNowBtn.disabled = false;
      if (manualOnly) {
        payNowBtn.textContent = 'Request manual review';
      } else if (providerInfo.defaultProvider && providerInfo.defaultProvider !== 'others') {
        payNowBtn.textContent = `Pay with ${providerLabel(providerInfo.defaultProvider)}`;
      } else {
        payNowBtn.textContent = payNowBtn.dataset.baseText || 'Pay now';
      }
      payNowBtn.onclick = () => startPaymentFlow({ quick: true });
    } else if (payNowBtn) {
      payNowBtn.classList.add('hidden');
      payNowBtn.textContent = payNowBtn.dataset.baseText || 'Pay now';
    }

    if (unpaid && chooseProviderBtn) {
      const showChooser = providers.length > 1;
      if (showChooser) {
        chooseProviderBtn.classList.remove('hidden');
        chooseProviderBtn.disabled = false;
        chooseProviderBtn.textContent = chooseProviderBtn.dataset.baseText || 'Choose provider';
        chooseProviderBtn.onclick = () => startPaymentFlow({ quick: false });
      } else {
        chooseProviderBtn.classList.add('hidden');
      }
    } else if (chooseProviderBtn) {
      chooseProviderBtn.classList.add('hidden');
    }
  } else {
    // no registration id -> hide payment initiation controls
    payNowBtn?.classList.add('hidden');
    chooseProviderBtn?.classList.add('hidden');
  }

  // Show cancellation UI whenever we know the registration mode (solo/team),
  // even if a registration_id is not yet present. The setup functions will
  // handle disabled state and missing details gracefully.
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
    // Load registration data
    await loadRegistration();

    if (!planResponse.ok) {
      throw new Error(`${ERROR_MESSAGES.fetchPlanFailed}${planResponse.status}`);
    }

  const planData = await planResponse.json();
  const profile = profileResponse.ok ? await profileResponse.json() : null;
  profileData = profile;
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

    const afterPartySection = document.getElementById('afterPartySection');
    if (afterPartySection) {
      const afterPartyAddressSpan = document.getElementById('afterPartyAddress');
      const afterPartyTimeSpan = document.getElementById('afterPartyTime');
      const afterPartyLocation = eventData && eventData.after_party_location ? eventData.after_party_location : null;
      const afterPartyAddress = afterPartyLocation &&
        (afterPartyLocation.address_public || afterPartyLocation.address || '');

      if (afterPartyAddressSpan) {
        afterPartyAddressSpan.textContent = afterPartyAddress || 'Address not provided yet';
      }
      if (afterPartyTimeSpan) {
        afterPartyTimeSpan.textContent = formatFinalPartyTime(eventData ? eventData.start_at : null);
      }

      afterPartySection.classList.remove('hidden');
    }

    if ((eventData?.fee_cents || 0) > 0) {
      await fetchPaymentProviders();
    } else {
      paymentProvidersState = { providers: [], defaultProvider: null, fetched: true, lastError: null };
    }
    renderRegistration();
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

  const mealTime = sectionElement.querySelector('[data-meal-time]');
  if (mealTime) {
    mealTime.textContent = section.time ? formatTime(section.time) : 'Time not specified';
  }

  const hostName = sectionElement.querySelector('[data-host-name]');
  if (hostName) {
    hostName.textContent = section.host_first_name || 'Host not specified';
  }

  const coGuests = sectionElement.querySelector('[data-co-guests]');
  if (coGuests) {
    // Deduplicate guest list while preserving order. Sometimes the plan
    // contains repeated names (e.g., due to multiple meal sections pointing
    // to the same guests). Show each guest only once for clarity.
    const rawGuests = Array.isArray(section.guests) ? section.guests : [];
    const seen = new Set();
    const uniqueGuests = [];
    rawGuests.forEach((g) => {
      if (!g && g !== 0) return;
      const key = String(g).trim();
      if (!key) return;
      const lower = key.toLowerCase();
      if (!seen.has(lower)) {
        seen.add(lower);
        uniqueGuests.push(key);
      }
    });
    coGuests.textContent = uniqueGuests.length ? uniqueGuests.join(' & ') : 'No co-guests';
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

function formatFinalPartyTime(rawValue) {
  if (!rawValue && rawValue !== 0) return 'Time not specified';

  if (typeof rawValue === 'object' && rawValue !== null) {
    if (rawValue.$date) {
      return formatFinalPartyTime(rawValue.$date);
    }
    if (typeof rawValue.toString === 'function') {
      return formatFinalPartyTime(rawValue.toString());
    }
  }

  if (typeof rawValue === 'string') {
    const trimmed = rawValue.trim();
    if (!trimmed) return 'Time not specified';

    const timeOnly = trimmed.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/);
    if (timeOnly) {
      const hh = timeOnly[1].padStart(2, '0');
      const mm = timeOnly[2].padStart(2, '0');
      return formatTime(`${hh}:${mm}`);
    }

    const parsedDate = new Date(trimmed);
    if (!Number.isNaN(parsedDate.getTime())) {
      return parsedDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
  }

  return 'Time not specified';
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
          team_id: match.team_id || match.team?.id || null,
        };
      }
    }
  } catch (e) {
    console.warn('Failed to load registration:', e);
  }
}

async function startProviderPayment(provider, options = {}) {
  if (!registrationData || !registrationData.registration_id) {
    throw new Error('Registration not ready for payment.');
  }

  const payload = {
    registration_id: registrationData.registration_id,
    provider,
  };

  if (provider === 'others' && typeof options.message === 'string') {
    const cleaned = sanitizeManualMessage(options.message);
    if (cleaned) payload.message = cleaned;
  }

  const apiPost = window.dh?.apiPost;
  let res;
  let data;
  if (apiPost) {
    ({ res, data } = await apiPost('/payments/create', payload));
  } else {
    res = await apiFetch('/payments/create', {
      method: 'POST',
      credentials: 'include',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    });
    try {
      data = await res.clone().json();
    } catch {
      data = {};
    }
  }

  if (!res || !res.ok) {
    let detail = data?.detail || data?.message || data?.error;
    if (!detail && res) {
      try {
        const text = await res.text();
        if (text) detail = text;
      } catch {}
    }
    throw new Error(detail || 'Could not create the payment request.');
  }

  const created = data || {};
  if (provider === 'others') {
    pushMessage('Manual payment request sent. The organizing team will reach out to you soon.', 'success');
    await loadRegistration();
    renderRegistration();
    return created;
  }

  const next = created.next_action || {};
  if (next.type === 'redirect' && next.url) {
    window.location.href = next.url;
    return created;
  }
  if (next.type === 'paypal_order' && next.approval_link) {
    window.location.href = next.approval_link;
    return created;
  }
  if (created.payment_link) {
    window.location.href = created.payment_link;
    return created;
  }

  pushMessage('Payment initiated; follow the provider instructions.', 'info');
  return created;
}

function sanitizeManualMessage(value) {
  if (typeof value !== 'string') return '';
  const trimmed = value.trim();
  if (!trimmed) return '';
  if (trimmed.length > MANUAL_MESSAGE_MAX_LEN) {
    return trimmed.slice(0, MANUAL_MESSAGE_MAX_LEN);
  }
  return trimmed;
}

function openPaymentDialog({ providers, defaultProvider }) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'fixed inset-0 z-50 flex items-center justify-center px-4';
    const backdrop = document.createElement('div');
    backdrop.className = 'absolute inset-0 bg-black/40';
    const panel = document.createElement('div');
    panel.className = 'relative z-10 bg-white rounded-2xl shadow-2xl max-w-lg w-full p-6';
    overlay.appendChild(backdrop);
    overlay.appendChild(panel);

    const close = (result) => {
      overlay.remove();
      resolve(result);
    };

    backdrop.addEventListener('click', () => close(false));

    const header = document.createElement('div');
    header.className = 'flex items-start justify-between mb-4';
    header.innerHTML = '<h3 class="text-lg font-bold text-[#172a3a]">Complete your payment</h3>';
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'text-gray-500 hover:text-gray-700';
  closeBtn.setAttribute('aria-label', 'Close payment dialog');
  closeBtn.textContent = 'X';
    closeBtn.addEventListener('click', () => close(false));
    header.appendChild(closeBtn);
    panel.appendChild(header);

    const intro = document.createElement('p');
    intro.className = 'text-sm text-gray-600 mb-3';
    intro.textContent = 'Choose how you would like to complete your payment.';
    panel.appendChild(intro);

    const feedbackEl = document.createElement('p');
    feedbackEl.className = 'hidden text-sm text-red-600 mb-3';
    panel.appendChild(feedbackEl);

    let selected = defaultProvider && providers.includes(defaultProvider) ? defaultProvider : providers[0];
    const list = document.createElement('div');
    list.className = 'space-y-2';
    const buttons = new Map();

    const manualWrap = document.createElement('div');
    manualWrap.className = 'hidden mt-4';
    manualWrap.innerHTML = `
      <label class="block text-sm font-medium text-[#172a3a] mb-1" for="manual-message-input">
        Message for the organizers <span class="text-red-500">*</span>
      </label>
      <textarea id="manual-message-input" class="w-full border rounded-lg p-3 text-sm focus:outline-none focus:ring-2 focus:ring-[#008080]" rows="4" maxlength="${MANUAL_MESSAGE_MAX_LEN}" placeholder="Let the organizers know how you plan to complete the payment."></textarea>
      <div class="mt-1 flex items-center justify-between text-xs text-gray-500">
        <span>Minimum ${MANUAL_MESSAGE_MIN_LEN} characters.</span>
        <span data-counter>0/${MANUAL_MESSAGE_MAX_LEN}</span>
      </div>
      <p data-error class="hidden mt-2 text-xs text-red-600"></p>
    `;

    function updateSelection() {
      buttons.forEach((btn, key) => {
        if (key === selected) {
          btn.classList.add('ring-2', 'ring-[#008080]', 'bg-[#008080]/5');
        } else {
          btn.classList.remove('ring-2', 'ring-[#008080]', 'bg-[#008080]/5');
        }
      });
      const manualActive = selected === 'others';
      manualWrap.classList.toggle('hidden', !manualActive);
      if (manualActive && manualInput) {
        manualInput.focus();
      }
    }

    providers.forEach((key) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.dataset.provider = key;
      btn.className = 'w-full flex flex-col items-start gap-1 px-4 py-3 rounded-xl border border-gray-200 hover:bg-gray-50 transition';
      const title = document.createElement('span');
      title.className = 'font-medium text-[#172a3a]';
      title.textContent = providerLabel(key);
      const hint = document.createElement('span');
      hint.className = 'text-xs text-gray-500';
      if (key === 'others') {
        hint.textContent = 'Leave a note for the organizers to arrange payment manually.';
      } else {
        hint.textContent = 'Secure online checkout.';
      }
      btn.appendChild(title);
      btn.appendChild(hint);
      btn.addEventListener('click', () => {
        selected = key;
        feedbackEl.classList.add('hidden');
        updateSelection();
      });
      buttons.set(key, btn);
      list.appendChild(btn);
    });

    panel.appendChild(list);
    panel.appendChild(manualWrap);

    const manualInput = manualWrap.querySelector('#manual-message-input');
    const manualCounter = manualWrap.querySelector('[data-counter]');
    const manualError = manualWrap.querySelector('[data-error]');

    if (manualInput && manualCounter) {
      manualInput.addEventListener('input', () => {
        const value = manualInput.value || '';
        manualCounter.textContent = `${value.length}/${MANUAL_MESSAGE_MAX_LEN}`;
        if (manualError) manualError.classList.add('hidden');
      });
    }

    const actions = document.createElement('div');
    actions.className = 'mt-6 flex flex-wrap gap-3 justify-end';
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'px-4 py-2 rounded-lg border border-gray-200 text-sm text-gray-600 hover:bg-gray-100';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', () => close(false));
    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'px-4 py-2 rounded-lg bg-[#008080] text-white text-sm font-semibold hover:bg-[#006d6d] focus:outline-none focus:ring-2 focus:ring-[#008080]/40';
    confirmBtn.textContent = 'Continue';

    let submitting = false;
    async function handleSubmit() {
      if (submitting) return;
      if (!selected) {
        feedbackEl.textContent = 'Please choose a payment option.';
        feedbackEl.classList.remove('hidden');
        return;
      }
      let manualMessage = '';
      if (selected === 'others') {
        if (!manualInput) {
          throw new Error('Manual payment message field is unavailable.');
        }
        manualMessage = sanitizeManualMessage(manualInput.value);
        if (manualMessage.length < MANUAL_MESSAGE_MIN_LEN) {
          if (manualError) {
            manualError.textContent = `Please add at least ${MANUAL_MESSAGE_MIN_LEN} characters so the organizers have sufficient details.`;
            manualError.classList.remove('hidden');
          }
          manualInput.focus();
          return;
        }
      }
      submitting = true;
      confirmBtn.disabled = true;
  confirmBtn.textContent = 'Processing...';
      try {
        await startProviderPayment(selected, { message: manualMessage });
        close(true);
      } catch (err) {
        submitting = false;
        confirmBtn.disabled = false;
        confirmBtn.textContent = 'Continue';
        const message = err?.message || 'Payment request failed.';
        if (selected === 'others') {
          if (manualError) {
            manualError.textContent = message;
            manualError.classList.remove('hidden');
          }
        } else {
          feedbackEl.textContent = message;
          feedbackEl.classList.remove('hidden');
        }
      }
    }

    confirmBtn.addEventListener('click', handleSubmit);

    actions.appendChild(cancelBtn);
    actions.appendChild(confirmBtn);
    panel.appendChild(actions);

    document.body.appendChild(overlay);
    updateSelection();
  });
}

async function startPaymentFlow({ quick }) {
  try {
    if (!registrationData || !registrationData.registration_id) {
      pushMessage('Registration not ready for payment.', 'error');
      return;
    }

    const info = await fetchPaymentProviders();
    const providers = Array.isArray(info.providers) ? info.providers : [];
    if (!providers.length) {
      pushMessage('No payment providers are currently available.', 'warn');
      return;
    }

    const defaultProvider = info.defaultProvider || providers[0];
    const onlineProviders = providers.filter((p) => p !== 'others');
    const manualOnly = providers.length === 1 && providers[0] === 'others';

    if (quick) {
      const preferred = defaultProvider && defaultProvider !== 'others'
        ? defaultProvider
        : onlineProviders[0];
      if (preferred) {
        await startProviderPayment(preferred);
        return;
      }
      if (manualOnly) {
        // fall through to dialog to capture required manual message
      }
    }

    await openPaymentDialog({ providers, defaultProvider });
  } catch (err) {
    console.error(err);
    const message = err?.message || 'Unexpected error during payment flow.';
    pushMessage(`Payment error: ${message}`, 'error');
  }
}

// Call the function on page load
window.addEventListener('DOMContentLoaded', logEventPlan);