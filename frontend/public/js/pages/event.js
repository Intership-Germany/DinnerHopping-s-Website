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
    let ev = null,
      plan = null;
    try {
      const fetcher = window.dh?.apiFetch || window.apiFetch;
      const [evRes, planRes] = await Promise.all([
        fetcher(`/events/${encodeURIComponent(eventId)}?anonymise=true`, commonOpts),
        fetcher('/api/my-plan', commonOpts),
      ]);
      ev = evRes.ok ? await evRes.json() : null;
      plan = planRes.ok ? await planRes.json() : null;
      if (!evRes.ok || !planRes.ok)
        throw new Error(`Failed to load data (${evRes.status}/${planRes.status})`);
    } catch (networkErr) {
      console.error('Failed to load event/plan:', networkErr);
      const notice = document.createElement('div');
      notice.className =
        'mx-auto mt-6 max-w-3xl bg-red-50 border border-red-200 text-red-700 rounded-lg p-3 text-sm';
      notice.textContent =
        'Unable to load event data. Please ensure you are logged in and CORS is configured.';
      const main = document.querySelector('main');
      if (main) main.prepend(notice);
      return;
    }
    const { starterSec, mainSec, dessertSec, closingSec } = sectionNodes();
    let me = plan && plan.user_email;
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
      const sec = byMeal['starter'] || null;
      const isHost = !!(
        sec &&
        me &&
        sec.hostEmail &&
        me.toLowerCase() === String(sec.hostEmail).toLowerCase()
      );
      populateCourseSection(starterSec, {
        courseLabel: 'Starter',
        isHost,
        time: sec ? sec.time : null,
        hostEmail: sec ? sec.hostEmail : null,
        guests: sec ? sec.guests : [],
        mapDivId: 'map-entree',
        hostLocation: sec ? sec.hostLocation : null,
      });
    }
    if (mainSec) {
      const sec = byMeal['main'] || null;
      const isHost = !!(
        sec &&
        me &&
        sec.hostEmail &&
        me.toLowerCase() === String(sec.hostEmail).toLowerCase()
      );
      populateCourseSection(mainSec, {
        courseLabel: 'Main Course',
        isHost,
        time: sec ? sec.time : null,
        hostEmail: sec ? sec.hostEmail : null,
        guests: sec ? sec.guests : [],
        mapDivId: null,
        hostLocation: null,
      });
      const warn = document.querySelector('.bg-[#ffe5d0]');
      if (warn) warn.style.display = 'none';
    }
    if (dessertSec) {
      const sec = byMeal['dessert'] || null;
      const isHost = !!(
        sec &&
        me &&
        sec.hostEmail &&
        me.toLowerCase() === String(sec.hostEmail).toLowerCase()
      );
      populateCourseSection(dessertSec, {
        courseLabel: 'Dessert',
        isHost,
        time: sec ? sec.time : null,
        hostEmail: sec ? sec.hostEmail : null,
        guests: sec ? sec.guests : [],
        mapDivId: 'map-dessert',
        hostLocation: sec ? sec.hostLocation : null,
      });
    }
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
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadData);
  } else loadData();
})();
