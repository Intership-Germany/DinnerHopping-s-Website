/**
 * Home page logic (formerly home-page.js)
 * Responsibilities:
 *  - Fetch & filter published events
 *  - Render event cards + registration modal (solo/team)
 *  - Manage pagination + active filter pills
 *  - Show a lightweight "My registrations" banner using localStorage hints
 *  - Offer payment provider chooser when multiple providers are available
 *
 * Notes:
 *  - Uses window.dh.apiFetch if available; falls back to fetch for resilience.
 *  - Intentionally kept framework‑free & SSR‑agnostic.
 */
// Slimmed-down home page script after utility extraction (see utils/*.js)
(function () {
  let __USER_PROFILE = null;
  let __USER_ZIP = null;
  const U = (window.dh && window.dh.utils) || {};
  const tpl =
    U.tpl ||
    U.template ||
    function (id) {
      const t = document.getElementById(id);
      return t && 'content' in t ? t.content.firstElementChild : null;
    };
  const cloneTpl =
    U.cloneTpl ||
    function (id) {
      const n = tpl(id);
      return n ? n.cloneNode(true) : null;
    };
  const formatFeeCents =
    U.formatFeeCents ||
    function (c) {
      if (typeof c !== 'number') return '';
      if (c <= 0) return 'Free';
      return (c / 100).toFixed(2) + ' €';
    };
  const fetchPublishedEvents =
    U.fetchPublishedEvents ||
    async function () {
      const api = window.dh?.apiFetch || window.apiFetch;
      const r = await api('/events?status=open', { headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    };
  const fetchMyEvents =
    U.fetchMyEvents ||
    async function () {
      const api = window.dh?.apiFetch || window.apiFetch;
      const r = await api('/events?participant=me', { headers: { Accept: 'application/json' } });
      if (!r.ok) return [];
      return r.json();
    };
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
  function openProviderModal() {
    return U.openModalFromTemplate
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
  }

  function renderLoading(container) {
    container.innerHTML = '';
    const skeleton = cloneTpl('tpl-loading');
    if (skeleton) container.appendChild(skeleton);
  }
  function renderError(container, message) {
    container.innerHTML = '';
    const node = cloneTpl('tpl-error');
    if (node) {
      if (message) node.textContent = message;
      container.appendChild(node);
    }
  }
  function renderEmpty(container) {
    container.innerHTML = '';
    const node = cloneTpl('tpl-empty');
    if (node) container.appendChild(node);
  }

  let __ALL_EVENTS = [];
  let __PAGE = 1;
  const PAGE_SIZE = 6;
  let __MY_EVENT_IDS = new Set();
  function parseFiltersFromURL() {
    const sp = new URLSearchParams(location.search);
    const q = (sp.get('q') || '').trim();
    const maxFee = sp.has('fee') && sp.get('fee') !== '' ? parseInt(sp.get('fee'), 10) : null;
    const deadline = sp.get('deadline') ? new Date(sp.get('deadline')) : null;
    const onlyAvail = sp.get('avail') === '1';
    const sort = (sp.get('sort') || '').trim();
    const page = Math.max(1, parseInt(sp.get('page') || '1', 10) || 1);
    return { q, maxFee, deadline, onlyAvail, sort, page };
  }
  function syncFormFromFilters(f) {
    const titleI = document.getElementById('filter-title');
    const feeI = document.getElementById('filter-fee');
    const deadlineI = document.getElementById('filter-deadline');
    const availI = document.getElementById('filter-available');
    const sortI = document.getElementById('filter-sort');
    if (titleI) titleI.value = f.q || '';
    if (feeI) feeI.value = f.maxFee != null && !Number.isNaN(f.maxFee) ? String(f.maxFee) : '';
    if (deadlineI) {
      if (f.deadline instanceof Date && !Number.isNaN(f.deadline)) {
        const d = f.deadline;
        const yyyy = d.getFullYear();
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        deadlineI.value = `${yyyy}-${mm}-${dd}`;
      } else deadlineI.value = '';
    }
    if (availI) availI.checked = !!f.onlyAvail;
    if (sortI) sortI.value = f.sort || '';
  }
  function writeFiltersToURL(f, page) {
    const sp = new URLSearchParams();
    if (f.q) sp.set('q', f.q);
    if (f.maxFee != null) sp.set('fee', String(f.maxFee));
    if (f.deadline instanceof Date && !Number.isNaN(f.deadline)) {
      const d = f.deadline;
      const yyyy = d.getFullYear();
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      sp.set('deadline', `${yyyy}-${mm}-${dd}`);
    }
    if (f.onlyAvail) sp.set('avail', '1');
    if (f.sort) sp.set('sort', f.sort);
    if (page && page > 1) sp.set('page', String(page));
    const newUrl = `${location.pathname}${sp.toString() ? '?' + sp.toString() : ''}${location.hash || ''}`;
    history.replaceState(null, '', newUrl);
  }
  function buildActiveFilterPills(f) {
    const pills = [];
    if (f.q) pills.push({ key: 'q', label: `Title: "${f.q}"` });
    if (f.maxFee != null)
      pills.push({ key: 'maxFee', label: `≤ ${(f.maxFee / 100).toFixed(2)} €` });
    if (f.deadline)
      pills.push({ key: 'deadline', label: `Before ${f.deadline.toLocaleDateString()}` });
    if (f.onlyAvail) pills.push({ key: 'avail', label: 'Only available' });
    if (f.sort) {
      const map = { deadline_asc: 'Deadline ↑', fee_asc: 'Fee ↑', fee_desc: 'Fee ↓' };
      pills.push({ key: 'sort', label: map[f.sort] || 'Default' });
    }
    return pills;
  }
  function renderActiveFiltersAndCounter(total, f) {
    const pillsWrap = document.getElementById('active-filters');
    const counterEl = document.getElementById('results-count');
    if (counterEl) counterEl.textContent = `${total} result${total === 1 ? '' : 's'}`;
    if (!pillsWrap) return;
    pillsWrap.innerHTML = '';
    buildActiveFilterPills(f).forEach((p) => {
      const span = document.createElement('span');
      span.className =
        'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-gray-100 text-gray-700 ring-1 ring-gray-200';
      span.textContent = p.label;
      pillsWrap.appendChild(span);
    });
  }
  function readFilters() {
    const titleI = document.getElementById('filter-title');
    const feeI = document.getElementById('filter-fee');
    const deadlineI = document.getElementById('filter-deadline');
    const availI = document.getElementById('filter-available');
    const sortI = document.getElementById('filter-sort');
    const q = (titleI?.value || '').trim().toLowerCase();
    const maxFee = feeI?.value ? parseInt(feeI.value, 10) : null;
    const deadline = deadlineI?.value ? new Date(deadlineI.value) : null;
    const onlyAvail = !!(availI && availI.checked);
    const sort = (sortI?.value || '').trim();
    return { q, maxFee, deadline, onlyAvail, sort };
  }
  function applyFilters() {
    const { q, maxFee, deadline, onlyAvail, sort } = readFilters();
    let filtered = (__ALL_EVENTS || []).filter((e) => {
      if (Array.isArray(e.valid_zip_codes) && e.valid_zip_codes.length > 0) {
        if (!__USER_ZIP) return false;
        if (!e.valid_zip_codes.includes(__USER_ZIP)) return false;
      }
      const title = (e.title || e.name || '').toLowerCase();
      if (q && !title.includes(q)) return false;
      const fee = typeof e.fee_cents === 'number' ? e.fee_cents : 0;
      if (maxFee != null && !(fee <= maxFee)) return false;
      if (deadline) {
        const d = e.registration_deadline ? new Date(e.registration_deadline) : null;
        if (!(d && !isNaN(d) && d <= deadline)) return false;
      }
      if (onlyAvail) {
        const capacity = 6;
        const left = capacity - (Number(e.attendee_count) || 0);
        if (left <= 0) return false;
      }
      return true;
    });
    filtered.sort((a, b) => {
      if (sort === 'deadline_asc') {
        const da = a.registration_deadline ? new Date(a.registration_deadline) : null;
        const db = b.registration_deadline ? new Date(b.registration_deadline) : null;
        return (da ? da.getTime() : Infinity) - (db ? db.getTime() : Infinity);
      }
      if (sort === 'fee_asc' || sort === 'fee_desc') {
        const fa = typeof a.fee_cents === 'number' ? a.fee_cents : 0;
        const fb = typeof b.fee_cents === 'number' ? b.fee_cents : 0;
        return sort === 'fee_asc' ? fa - fb : fb - fa;
      }
      return 0;
    });
    const total = filtered.length;
    const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (__PAGE > maxPage) __PAGE = maxPage;
    const start = (__PAGE - 1) * PAGE_SIZE;
    const pageItems = filtered.slice(start, start + PAGE_SIZE);
    const listEl = document.getElementById('events-list');
    if (listEl) {
      if (!Array.isArray(pageItems) || pageItems.length === 0) {
        renderEmpty(listEl);
      } else if (window.dh?.components?.renderEventCards) {
        window.dh.components.renderEventCards({
          container: listEl,
          events: pageItems,
          userZip: __USER_ZIP,
          cloneTpl,
          formatFeeCents,
          onRegister: (ctx) => {
            if (window.dh?.components?.openRegisterModal) {
              window.dh.components.openRegisterModal({ ...ctx, userProfile: __USER_PROFILE });
            } else {
              console.warn('Register modal component missing');
            }
          },
        });
      }
    }
    renderActiveFiltersAndCounter(total, { q, maxFee, deadline, onlyAvail, sort });
    const prevBtn = document.getElementById('page-prev');
    const nextBtn = document.getElementById('page-next');
    const info = document.getElementById('page-info');
    if (prevBtn) prevBtn.disabled = __PAGE <= 1;
    if (nextBtn) nextBtn.disabled = __PAGE >= maxPage;
    if (info) info.textContent = `Page ${__PAGE} of ${maxPage}`;
    writeFiltersToURL({ q, maxFee, deadline, onlyAvail, sort }, __PAGE);
  }
  function bindFilterEvents() {
    const form = document.getElementById('events-filters');
    if (!form) return;
    form.addEventListener('input', () => {
      __PAGE = 1;
      applyFilters();
    });
    form.addEventListener('change', () => {
      __PAGE = 1;
      applyFilters();
    });
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      applyFilters();
    });
    const resetBtn = document.getElementById('filter-reset');
    if (resetBtn)
      resetBtn.addEventListener('click', () => {
        setTimeout(() => {
          __PAGE = 1;
          applyFilters();
        }, 0);
      });
    const prevBtn = document.getElementById('page-prev');
    const nextBtn = document.getElementById('page-next');
    if (prevBtn)
      prevBtn.addEventListener('click', (e) => {
        e.preventDefault();
        if (__PAGE > 1) {
          __PAGE--;
          applyFilters();
        }
      });
    if (nextBtn)
      nextBtn.addEventListener('click', (e) => {
        e.preventDefault();
        __PAGE++;
        applyFilters();
      });
  }

  document.addEventListener('DOMContentLoaded', async () => {
    const listEl = document.getElementById('events-list');
    if (!listEl) return;
    renderLoading(listEl);
    bindFilterEvents();
    const urlFilters = parseFiltersFromURL();
    __PAGE = urlFilters.page || 1;
    syncFormFromFilters(urlFilters);
    try {
      const path = '/profile';
      const headers = { Accept: 'application/json' };
      const api =
        window.dh && window.dh.apiFetch
          ? window.dh.apiFetch
          : (p, opts) =>
              fetch(window.BACKEND_BASE_URL + p, { ...(opts || {}), credentials: 'include' });
      const res = await api(path, { method: 'GET', headers });
      if (res.ok) {
        __USER_PROFILE = await res.json();
        const addr = __USER_PROFILE.address || __USER_PROFILE.address_public || {};
        __USER_ZIP = addr.postal_code || __USER_PROFILE.postal_code || null;
      }
    } catch {}
    try {
      const events = await fetchPublishedEvents();
      __ALL_EVENTS = Array.isArray(events) ? events : events?.events || [];
      applyFilters();
    } catch (err) {
      console.error('Failed to load events', err);
      if (err && (err.status === 401 || err.status === 419)) return;
      renderError(listEl, 'Could not load events. Please try again later.');
    }
    try {
      const myEvents = await fetchMyEvents();
      try {
        __MY_EVENT_IDS = new Set(
          (Array.isArray(myEvents) ? myEvents : [])
            .map((ev) => ev && (ev.id || ev._id || ev.eventId))
            .filter(Boolean)
            .map(String)
        );
      } catch {}
      try {
        if (window.dh?.components?.renderMyRegistrations) {
          window.dh.components.renderMyRegistrations(Array.isArray(myEvents) ? myEvents : []);
        }
      } catch {}
      try {
        applyFilters();
      } catch {}
    } catch {}
  });
})();
