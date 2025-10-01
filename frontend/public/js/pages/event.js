// Event page script moved under pages (unchanged logic except namespace usage)
(function () {
  const qs = new URLSearchParams(window.location.search);
  const eventId = qs.get('id');
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  function normalizeMeal(meal) {
    if (!meal) return null;
    const m = String(meal).toLowerCase();
    if (m.includes('entr') || m.includes('appet')) return 'starter';
    if (m.includes('plat') || m.includes('main')) return 'main';
    if (m.includes('dess')) return 'dessert';
    return m;
  }
  function setText(el, text) {
    if (el) el.textContent = text;
  }
  function findTimeSpan(section) {
    const ps = $$('p', section);
    for (const p of ps) {
      if (/\btime\s*:/i.test(p.textContent)) return $('span', p) || p;
    }
    return null;
  }
  function formatNameOrEmail(s) {
    if (!s) return '';
    return String(s).trim();
  }
  function listToSentence(arr) {
    const xs = (arr || []).map(formatNameOrEmail);
    if (xs.length <= 1) return xs.join('');
    return xs.slice(0, -1).join(', ') + ' & ' + xs[xs.length - 1];
  }
  function initLeafletCircle(divId, center, radiusM) {
    try {
      if (!center || typeof L === 'undefined') return;
      const { lat, lon } = center;
      if (typeof lat !== 'number' || typeof lon !== 'number') return;
      const map = L.map(divId, { zoomControl: false, attributionControl: false });
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
      const c = L.latLng(lat, lon);
      L.circle(c, {
        radius: Math.max(200, Number(radiusM) || 500),
        color: '#172a3a',
        fillColor: '#008080',
        fillOpacity: 0.2,
        weight: 1,
      }).addTo(map);
      map.setView(c, 14);
      setTimeout(() => map.invalidateSize(), 0);
    } catch (e) {
      console.warn('Map init failed', e);
    }
  }
  function sectionNodes() {
    const entreeMap = $('#map-entree');
    const dessertMap = $('#map-dessert');
    const all = $$('main section');
    const starterSec = entreeMap ? entreeMap.closest('section') : all[0];
    const dessertSec = dessertMap ? dessertMap.closest('section') : all[2];
    const mainSec = all[1];
    const closingSec = all[3];
    return { starterSec, mainSec, dessertSec, closingSec };
  }
  function populateCourseSection(sectionEl, opts) {
    if (!sectionEl) return;
    const { courseLabel, isHost, time, hostEmail, guests, mapDivId, hostLocation } = opts;
    const h2 = $('h2', sectionEl);
    if (h2) {
      const statusTxt = isHost ? 'You are the Host' : 'You are Invited';
      setText(h2, `${courseLabel} (${statusTxt})`);
    }
    const timeSpan = findTimeSpan(sectionEl);
    if (timeSpan) setText(timeSpan, time || '—');
    const infoWrap = $('.mb-2', sectionEl);
    if (infoWrap) {
      const guestSentence = listToSentence(guests || []);
      if (isHost) {
        infoWrap.innerHTML = `<p class="text-sm">You are hosting ${courseLabel.toLowerCase()} for:</p><p class="text-sm"><span class="font-semibold">${guestSentence || 'TBD'}</span>.</p>`;
      } else {
        infoWrap.innerHTML = `<p class="text-sm">You are invited to ${courseLabel.toLowerCase()} at <span class="font-semibold">${formatNameOrEmail(hostEmail) || 'TBD'}</span>'s place.</p><p class="text-sm">Your co-guests: <span class="font-semibold">${guestSentence || 'TBD'}</span>.</p>`;
      }
    }
    if (mapDivId && hostLocation && hostLocation.center) {
      const locP = $('div.flex-1 p.text-xs', sectionEl);
      if (locP) setText(locP, 'Location: Approximate area (see map)');
      initLeafletCircle(mapDivId, hostLocation.center, hostLocation.approx_radius_m);
    }
    const btn = $('button', sectionEl);
    if (btn) {
      const sectionKey = courseLabel.toLowerCase().includes('main')
        ? 'main'
        : courseLabel.toLowerCase().includes('dessert')
          ? 'dessert'
          : 'starter';
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        const url = new URL('chat.html', window.location.origin);
        url.searchParams.set('event_id', eventId || '');
        url.searchParams.set('section', sectionKey);
        window.location.href = url.toString();
      });
    }
  }
  async function loadData() {
    if (!eventId) {
      console.error('Missing event id');
      return;
    }
    try {
      await (window.dh?.initCsrf
        ? window.dh.initCsrf()
        : window.initCsrf
          ? window.initCsrf()
          : Promise.resolve());
    } catch {}
    const bearer =
      window.auth && typeof window.auth.getCookie === 'function'
        ? window.auth.getCookie('dh_token')
        : null;
    const commonOpts = bearer
      ? { credentials: 'omit', headers: { Authorization: `Bearer ${bearer}` } }
      : {};
    let ev = null;
    let plan = null;
    try {
      const fetcher = window.dh?.apiFetch || window.apiFetch;
      const evRes = await fetcher(`/events/${encodeURIComponent(eventId)}?anonymise=true`, commonOpts);
      if (!evRes.ok) throw new Error(`Event load failed (${evRes.status})`);
      ev = await evRes.json();
      plan = await fetchPlan(commonOpts).catch(() => null);
    } catch (networkErr) {
      console.error('Failed to load event/plan:', networkErr);
      const notice = document.createElement('div');
      notice.className =
        'mx-auto mt-6 max-w-3xl bg-red-50 border border-red-200 text-red-700 rounded-lg p-3 text-sm';
      notice.textContent = 'Unable to load event data. Please ensure you are logged in.';
      const main = document.querySelector('main');
      if (main) main.prepend(notice);
      return;
    }
    // Apply plan data (if any) to sections
    applyPlan(plan);

    const { closingSec } = sectionNodes();
    if (closingSec && ev) {
      const whereP = $('div.mb-2 p.text-sm span.font-semibold', closingSec);
      const helperP = $('div.mb-2 p.text-xs', closingSec);
      let closingText = null;
      if (ev.after_party_location) {
        const apl = ev.after_party_location;
        if (apl.address_public) {
          closingText = apl.address_public;
          if (helperP) setText(helperP, 'The place is publicly shown here.');
        } else if (apl.center) {
          closingText = 'After-party area (approximate)';
          if (helperP)
            setText(helperP, 'Approximate area only; exact address will be shared later.');
        }
      }
      if (whereP && closingText) setText(whereP, closingText);
    }
    if (ev && ev.title) document.title = `Dinnerhopping - ${ev.title}`;
    if (!plan || (plan && plan.message)) {
      const info = document.createElement('div');
      info.className = 'mx-auto mt-4 max-w-3xl bg-blue-50 border border-blue-200 text-blue-700 rounded-lg p-3 text-xs';
      info.textContent = 'Matching not started yet – your detailed route will appear here later.';
      const main = document.querySelector('main');
      if (main) main.prepend(info);
    }

    // Registration status banner & refresh plan button
    showRegistrationStatus(ev);
    injectRefreshPlanButton(commonOpts);

    // --- Solo cancellation logic ---
    try {
      const deadlineIso = ev && (ev.registration_deadline || ev.payment_deadline || ev.date);
      const deadline = deadlineIso ? new Date(deadlineIso) : null;
      const now = new Date();
      const canCancel = deadline && !isNaN(deadline) && now < deadline;
      // Load local registration snapshot
      const snapshotKey = `dh:lastReg:${eventId}`;
      let regInfo = null;
      try { const raw = localStorage.getItem(snapshotKey); regInfo = raw ? JSON.parse(raw) : null; } catch {}
      if (regInfo && regInfo.registration_id && regInfo.status !== 'cancelled_by_user' && canCancel) {
        const refundFlag = !!(ev && ev.refund_on_cancellation);
        const feeCents = ev && typeof ev.fee_cents === 'number' ? ev.fee_cents : 0;
        injectCancelUI(regInfo, snapshotKey, deadline, { refundFlag, feeCents });
      }
    } catch (e) { console.warn('Cancel logic init failed', e); }
  }

  // Centralized plan fetcher
  async function fetchPlan(commonOpts) {
    const fetcher = window.dh?.apiFetch || window.apiFetch;
    const res = await fetcher('/api/my-plan', commonOpts || {});
    if (!res.ok) throw new Error(`plan ${res.status}`);
    return res.json();
  }

  function applyPlan(plan) {
    const { starterSec, mainSec, dessertSec } = sectionNodes();
    const me = plan && plan.user_email;
    const byMeal = {};
    if (plan && Array.isArray(plan.sections)) {
      for (const sec of plan.sections) {
        const key = normalizeMeal(sec.meal);
        if (!key) continue;
        byMeal[key] = {
          meal: sec.meal,
          time: sec.time,
          hostEmail: sec.host_email,
          hostLocation: sec.host_location,
          guests: sec.guests || [],
        };
      }
    }
    if (starterSec) {
      const sec = byMeal['starter'];
      const isHost = !!(sec && me && sec.hostEmail && me.toLowerCase() === String(sec.hostEmail).toLowerCase());
      populateCourseSection(starterSec, {
        courseLabel: 'Starter',
        isHost,
        time: sec && sec.time,
        hostEmail: sec && sec.hostEmail,
        guests: sec && sec.guests,
        mapDivId: 'map-entree',
        hostLocation: sec && sec.hostLocation,
      });
    }
    if (mainSec) {
      const sec = byMeal['main'];
      const isHost = !!(sec && me && sec.hostEmail && me.toLowerCase() === String(sec.hostEmail).toLowerCase());
      populateCourseSection(mainSec, {
        courseLabel: 'Main Course',
        isHost,
        time: sec && sec.time,
        hostEmail: sec && sec.hostEmail,
        guests: sec && sec.guests,
        mapDivId: null,
        hostLocation: null,
      });
    }
    if (dessertSec) {
      const sec = byMeal['dessert'];
      const isHost = !!(sec && me && sec.hostEmail && me.toLowerCase() === String(sec.hostEmail).toLowerCase());
      populateCourseSection(dessertSec, {
        courseLabel: 'Dessert',
        isHost,
        time: sec && sec.time,
        hostEmail: sec && sec.hostEmail,
        guests: sec && sec.guests,
        mapDivId: 'map-dessert',
        hostLocation: sec && sec.hostLocation,
      });
    }
  }

  function showRegistrationStatus(ev) {
    if (!ev || !eventId) return;
    const main = document.querySelector('main');
    if (!main) return;
    const existing = document.getElementById('reg-status-banner');
    if (existing) existing.remove();
    let snapshot = null;
    try { const raw = localStorage.getItem(`dh:lastReg:${eventId}`); snapshot = raw ? JSON.parse(raw) : null; } catch {}
    if (!snapshot) return; // nothing to show
    const feeCents = typeof ev.fee_cents === 'number' ? ev.fee_cents : 0;
    const paid = snapshot.payment_status && ['succeeded','paid'].includes(snapshot.payment_status);
    let label = 'registered';
    let color = 'bg-gray-100 text-gray-700 border-gray-300';
    if (snapshot.status && /cancelled/i.test(snapshot.status)) { label = 'cancelled'; color = 'bg-red-100 text-red-700 border-red-300'; }
    else if (paid) { label = 'paid'; color = 'bg-green-100 text-green-700 border-green-300'; }
    else if (feeCents > 0) { label = 'pending payment'; color = 'bg-yellow-100 text-yellow-700 border-yellow-300'; }
    const div = document.createElement('div');
    div.id = 'reg-status-banner';
    div.className = 'mb-4 flex items-center gap-3 rounded-lg px-4 py-2 border text-sm ' + color;
    div.innerHTML = `<span class="font-semibold">Status:</span><span class="uppercase tracking-wide font-bold">${label}</span>` + (paid && snapshot.paid_at ? `<span class="text-xs opacity-70">(paid)</span>` : '');
    main.prepend(div);
  }

  function injectRefreshPlanButton(commonOpts) {
    const main = document.querySelector('main');
    if (!main) return;
    if (document.getElementById('plan-refresh-btn')) return;
    const btn = document.createElement('button');
    btn.id = 'plan-refresh-btn';
    btn.type = 'button';
    btn.className = 'mb-4 ml-auto block px-4 py-1.5 rounded-md bg-teal-600 hover:bg-teal-700 text-white text-xs font-semibold shadow focus:outline-none focus:ring-2 focus:ring-teal-400';
    btn.textContent = 'Refresh plan';
    btn.addEventListener('click', async () => {
      btn.disabled = true; const prev = btn.textContent; btn.textContent = 'Refreshing…';
      try {
        const p = await fetchPlan(commonOpts).catch(() => null);
        applyPlan(p);
      } finally { btn.disabled = false; btn.textContent = prev; }
    });
    main.prepend(btn);
  }

  function injectCancelUI(regInfo, snapshotKey, deadline, refundMeta) {
    const main = document.querySelector('main');
    if (!main) return;
    // Avoid duplicate
    if (document.getElementById('solo-cancel-box')) return;
    const box = document.createElement('div');
    box.id = 'solo-cancel-box';
    box.className = 'mb-6 p-4 rounded-xl border border-red-200 bg-red-50 text-red-700 text-sm flex flex-col gap-2';
    const deadlineStr = deadline ? deadline.toLocaleString() : 'deadline';
    let refundLine = '';
    if (refundMeta && refundMeta.feeCents > 0) {
      if (refundMeta.refundFlag) {
        refundLine = '<div class="text-red-600/90"><span class="font-semibold">Refund:</span> A refund will be initiated automatically after cancellation (processing may take a few days).</div>';
      } else {
        refundLine = '<div class="text-red-600/90"><span class="font-semibold">Refund:</span> Please be aware that there will be <span class="font-semibold uppercase">no refund</span> for this event.</div>';
      }
    }
    box.innerHTML = `<div><strong>Need to cancel?</strong> You can cancel your solo registration until <span class="font-semibold">${deadlineStr}</span>. This cannot be undone.</div>${refundLine}`;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'self-start px-4 py-2 rounded-lg bg-red-600 hover:bg-red-700 text-white font-semibold shadow focus:outline-none focus:ring-4 focus:ring-red-300';
    btn.textContent = 'Cancel my participation';
    // Inline error message (hidden by default)
    const errorMsg = document.createElement('div');
    errorMsg.className = 'hidden px-3 py-2 rounded-lg bg-red-600/10 border border-red-300 text-red-700 text-xs';
    errorMsg.setAttribute('role', 'alert');
    const showError = (msg) => {
      errorMsg.textContent = msg || 'Cancellation failed.';
      errorMsg.classList.remove('hidden');
      // brief shake animation for visibility
      errorMsg.animate([
        { transform: 'translateX(0)' },
        { transform: 'translateX(-4px)' },
        { transform: 'translateX(4px)' },
        { transform: 'translateX(0)' }
      ], { duration: 260 });
    };
    // Two-step inline confirmation UI (no native confirm dialog)
    const confirmWrap = document.createElement('div');
    confirmWrap.className = 'hidden mt-2 p-3 rounded-lg border border-red-300 bg-white/70 text-xs flex flex-col gap-2';
    confirmWrap.innerHTML = '<div class="text-red-700 font-semibold">Confirm cancellation?</div><div class="text-red-600">This cannot be undone and your spot might go to someone else.</div>';
    const actions = document.createElement('div');
    actions.className = 'flex gap-2';
    const confirmYes = document.createElement('button');
    confirmYes.type = 'button';
    confirmYes.className = 'px-3 py-1.5 rounded-md bg-red-600 hover:bg-red-700 text-white font-semibold text-xs shadow focus:outline-none focus:ring-2 focus:ring-red-300';
    confirmYes.textContent = 'Yes, cancel';
    const confirmNo = document.createElement('button');
    confirmNo.type = 'button';
    confirmNo.className = 'px-3 py-1.5 rounded-md bg-gray-200 hover:bg-gray-300 text-gray-800 font-medium text-xs focus:outline-none focus:ring-2 focus:ring-gray-300';
    confirmNo.textContent = 'Keep my spot';
    actions.appendChild(confirmYes); actions.appendChild(confirmNo); confirmWrap.appendChild(actions);

    const startCancellation = async () => {
      confirmYes.disabled = true; confirmNo.disabled = true; btn.disabled = true; confirmYes.textContent = 'Cancelling…';
      try {
        const path = `/registrations/${encodeURIComponent(regInfo.registration_id)}`;
        const fetcher = window.dh?.apiFetch || window.apiFetch;
        const res = await fetcher(path, { method: 'DELETE', headers: { 'Accept': 'application/json' } });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json().catch(() => ({}));
        try { regInfo.status = data.status || 'cancelled_by_user'; localStorage.setItem(snapshotKey, JSON.stringify(regInfo)); } catch {}
        box.innerHTML = '<div class="font-semibold">Registration cancelled.</div><div>If eligible, a refund will be processed automatically.</div>';
      } catch (err) {
        showError(err && err.message ? err.message : 'Cancellation failed. Please try again.');
        confirmYes.disabled = false; confirmNo.disabled = false; btn.disabled = false; confirmYes.textContent = 'Yes, cancel';
      }
    };

    btn.addEventListener('click', () => {
      // show inline confirmation
      btn.classList.add('hidden');
      confirmWrap.classList.remove('hidden');
      // accessibility focus
      confirmYes.focus();
    });
    confirmNo.addEventListener('click', () => {
      confirmWrap.classList.add('hidden');
      btn.classList.remove('hidden');
      btn.focus();
    });
    confirmYes.addEventListener('click', startCancellation);
    box.appendChild(btn);
    box.appendChild(confirmWrap);
    box.appendChild(errorMsg);
    main.prepend(box);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadData);
  } else loadData();
})();
