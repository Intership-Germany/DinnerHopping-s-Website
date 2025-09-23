// Home page script: verify authentication and fetch/render published events.
// This file is loaded by home.html and is responsible for dynamic content.

(function () {
    // Tiny helpers around <template> usage for cleaner DOM
    function $tpl(id) {
        const t = document.getElementById(id);
        return t && 'content' in t ? t.content.firstElementChild : null;
    }
    function cloneTpl(id) {
        const node = $tpl(id);
        return node ? node.cloneNode(true) : null;
    }

    function withAuthHeader(headers) {
        const out = { ...(headers || {}) };
        try {
            if (window.auth && typeof window.auth.getCookie === 'function') {
                const token = window.auth.getCookie('dh_token');
                if (token) out['Authorization'] = `Bearer ${token}`;
            } else {
                const m = document.cookie.match(/(?:^|; )dh_token=([^;]*)/);
                const token = m ? decodeURIComponent(m[1]) : null;
                if (token) out['Authorization'] = `Bearer ${token}`;
            }
        } catch {}
        return out;
    }

    // Render helpers for states using templates
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

    function formatFeeCents(cents) {
        if (typeof cents !== 'number') return '';
        if (cents <= 0) return 'Free';
        // Display in EUR with simple formatting; adjust as needed
        return `${(cents / 100).toFixed(2)} €`;
    }

    function renderEvents(container, events) {
        container.innerHTML = '';
        if (!Array.isArray(events) || events.length === 0) {
            return renderEmpty(container);
        }
        events.forEach((e) => {
            const node = cloneTpl('tpl-event-card');
            if (!node) return;

            const titleEl = node.querySelector('.event-title');
            const dateWrapEl = node.querySelector('.event-date');
            const dateTextEl = node.querySelector('.event-date-text') || dateWrapEl;
            const feeBadgeEl = node.querySelector('.event-fee-badge');
            const feeTextEl = node.querySelector('.event-fee-text') || feeBadgeEl;
            const descEl = node.querySelector('.event-desc');
            const spotsEl = node.querySelector('.event-spots');
            const ctaEl = node.querySelector('.event-cta');

            // Title
            titleEl.textContent = e.title || e.name || 'Untitled Event';

            // Date
            const d = e.registration_deadline ? new Date(e.registration_deadline) : null;
            const dateStr = d && !isNaN(d) ? d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : 'N/A';
            dateTextEl.textContent = `Registration deadline · ${dateStr}`;

            // Fee
            const fee = formatFeeCents(typeof e.fee_cents === 'number' ? e.fee_cents : 0);
            feeTextEl.textContent = fee ? `Fee · ${fee}` : 'Free';

            // Description (optional)
            if (descEl) {
                const desc = e.description || e.summary || '';
                descEl.textContent = desc;
                if (!desc) descEl.classList.add('hidden');
            }

            // Spots remaining (assuming 6 capacity for now)
            const capacity = 6; // default to 6
            const placeLeft = capacity - (Number(e.attendee_count) || 0);
            if (placeLeft <= 0) {
                spotsEl.textContent = 'Event Full';
                spotsEl.className = 'event-spots text-sm font-semibold text-red-600';
                ctaEl.classList.add('opacity-60', 'cursor-not-allowed');
                ctaEl.setAttribute('aria-disabled', 'true');
                ctaEl.tabIndex = -1;
            } else if (placeLeft === 1) {
                spotsEl.textContent = 'Last spot!';
                spotsEl.className = 'event-spots text-sm font-semibold text-red-600';
            } else {
                spotsEl.textContent = `${placeLeft} spots left`;
                spotsEl.className = 'event-spots text-sm font-semibold text-green-600';
            }

            // CTA -> register endpoint
            // Try to resolve an event id (API might return id/_id)
            const eventId = e.id || e._id || e.eventId || (e.event && (e.event.id || e.event._id));
            ctaEl.href = '#';
            ctaEl.addEventListener('click', async (ev) => {
                ev.preventDefault();
                if (!eventId || ctaEl.getAttribute('aria-disabled') === 'true') return;

                const origText = ctaEl.textContent;
                ctaEl.textContent = 'Applying…';
                ctaEl.classList.add('opacity-80');

                try {
                    const headers = withAuthHeader({ 'Accept': 'application/json', 'Content-Type': 'application/json' });
                    const path = `/events/${eventId}/register`;

                    // Important: avoid credentials: 'include' to bypass wildcard CORS restriction from server
                    // We authenticate with Bearer token extracted from cookie instead.
                    const res = await fetch((window.BACKEND_BASE_URL || '') + path, {
                        method: 'POST',
                        headers,
                        body: JSON.stringify({ team_size: 1, invited_emails: [] }),
                        credentials: 'omit'
                    });

                    if (res.status === 401 || res.status === 419) {
                        if (typeof window.handleUnauthorized === 'function') window.handleUnauthorized();
                        return;
                    }
                    if (!res.ok) throw new Error(`HTTP ${res.status}`);

                    const body = await res.json();
                    const paymentLink = body.payment_link || (body.payment && (body.payment.link || body.payment.url));
                    // Try to extract a registration id
                    const regId = (body && (body.registration_id || body.registrationId))
                        || (Array.isArray(body?.registration_ids) && body.registration_ids[0])
                        || (body?.registration && (body.registration.id || body.registration._id))
                        || body?.id || body?._id;

                    // Optimistic UI: reflect registration
                    spotsEl.textContent = Math.max(placeLeft - 1, 0) === 0 ? 'Event Full' : `${Math.max(placeLeft - 1, 0)} spots left`;
                    if (Math.max(placeLeft - 1, 0) === 0) {
                        ctaEl.classList.add('opacity-60', 'cursor-not-allowed');
                        ctaEl.setAttribute('aria-disabled', 'true');
                        ctaEl.tabIndex = -1;
                    }

                    // If event has a fee, prefer provider selection page
                    const feeCents = typeof e.fee_cents === 'number' ? e.fee_cents : 0;
                    if (feeCents > 0 && regId) {
                        const params = new URLSearchParams({ reg: String(regId), event: String(eventId), amount: String(feeCents) });
                        window.location.href = `payment-providers.html?${params.toString()}`;
                        return;
                    }
                    // Fallback: direct payment link if provided by backend
                    if (paymentLink) {
                        const base = window.BACKEND_BASE_URL || '';
                        window.location.href = paymentLink.startsWith('http') ? paymentLink : base + paymentLink;
                        return;
                    }

                    // Otherwise show a lightweight confirmation state
                    ctaEl.textContent = 'Registered';
                } catch (err) {
                    console.error('Registration failed', err);
                    ctaEl.textContent = 'Try Again';
                } finally {
                    setTimeout(() => {
                        if (ctaEl.textContent === 'Applying…') ctaEl.textContent = origText;
                        ctaEl.classList.remove('opacity-80');
                    }, 600);
                }
            });

            container.appendChild(node);
        });
    }

    // --- Filtering ---
    let __ALL_EVENTS = [];
    let __PAGE = 1;
    const PAGE_SIZE = 6;

    function buildActiveFilterPills(filters) {
        const pills = [];
        if (filters.q) pills.push({ key: 'q', label: `Title: "${filters.q}"` });
        if (filters.maxFee != null) pills.push({ key: 'maxFee', label: `≤ ${(filters.maxFee/100).toFixed(2)} €` });
        if (filters.deadline) pills.push({ key: 'deadline', label: `Before ${filters.deadline.toLocaleDateString()}` });
        if (filters.onlyAvail) pills.push({ key: 'avail', label: 'Only available' });
        if (filters.sort) {
            const map = { deadline_asc: 'Deadline ↑', fee_asc: 'Fee ↑', fee_desc: 'Fee ↓' };
            pills.push({ key: 'sort', label: map[filters.sort] || 'Default' });
        }
        return pills;
    }

    function renderActiveFiltersAndCounter(total, filters) {
        const pillsWrap = document.getElementById('active-filters');
        const counterEl = document.getElementById('results-count');
        if (counterEl) counterEl.textContent = `${total} result${total === 1 ? '' : 's'}`;
        if (!pillsWrap) return;
        pillsWrap.innerHTML = '';
        const pills = buildActiveFilterPills(filters);
        pills.forEach(p => {
            const span = document.createElement('span');
            span.className = 'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-gray-100 text-gray-700 ring-1 ring-gray-200';
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
        const maxFee = feeI?.value ? parseInt(feeI.value, 10) : null; // cents
        const deadline = deadlineI?.value ? new Date(deadlineI.value) : null;
        const onlyAvail = !!(availI && availI.checked);
        const sort = (sortI?.value || '').trim();

        return { q, maxFee, deadline, onlyAvail, sort };
    }

    function applyFilters() {
        const { q, maxFee, deadline, onlyAvail, sort } = readFilters();
        let filtered = (__ALL_EVENTS || []).filter(e => {
            // title
            const title = (e.title || e.name || '').toLowerCase();
            if (q && !title.includes(q)) return false;
            // fee
            const fee = typeof e.fee_cents === 'number' ? e.fee_cents : 0;
            if (maxFee != null && !(fee <= maxFee)) return false;
            // deadline
            if (deadline) {
                const d = e.registration_deadline ? new Date(e.registration_deadline) : null;
                if (!(d && !isNaN(d) && d <= deadline)) return false;
            }
            // availability (assuming capacity 6)
            if (onlyAvail) {
                const capacity = 6;
                const left = capacity - (Number(e.attendee_count) || 0);
                if (left <= 0) return false;
            }
            return true;
        });
        // sort
        filtered.sort((a,b) => {
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

        // pagination
        const total = filtered.length;
        const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
        if (__PAGE > maxPage) __PAGE = maxPage;
        const start = (__PAGE - 1) * PAGE_SIZE;
        const pageItems = filtered.slice(start, start + PAGE_SIZE);

        // render
        const listEl = document.getElementById('events-list');
        if (listEl) renderEvents(listEl, pageItems);

    // render pills + counter
        renderActiveFiltersAndCounter(total, { q, maxFee, deadline, onlyAvail, sort });

        // render pager
        const prevBtn = document.getElementById('page-prev');
        const nextBtn = document.getElementById('page-next');
        const info = document.getElementById('page-info');
        if (prevBtn) prevBtn.disabled = __PAGE <= 1;
        if (nextBtn) nextBtn.disabled = __PAGE >= maxPage;
        if (info) info.textContent = `Page ${__PAGE} of ${maxPage}`;

        // Persist filters into URL for share/refresh
        updateQueryString({ q, maxFee, deadline, onlyAvail, sort, page: __PAGE });
    }

    function bindFilterEvents() {
        const form = document.getElementById('events-filters');
        if (!form) return;
        form.addEventListener('input', () => { __PAGE = 1; applyFilters(); });
        form.addEventListener('change', () => { __PAGE = 1; applyFilters(); });
        form.addEventListener('submit', (e) => { e.preventDefault(); applyFilters(); });

        const resetBtn = document.getElementById('filter-reset');
        if (resetBtn) resetBtn.addEventListener('click', () => {
            setTimeout(() => { __PAGE = 1; applyFilters(); }, 0);
        });

        const prevBtn = document.getElementById('page-prev');
        const nextBtn = document.getElementById('page-next');
        if (prevBtn) prevBtn.addEventListener('click', (e) => { e.preventDefault(); if (__PAGE > 1) { __PAGE--; applyFilters(); } });
        if (nextBtn) nextBtn.addEventListener('click', (e) => { e.preventDefault(); __PAGE++; applyFilters(); });
    }

    // --- URL state (persist filters) ---
    function updateQueryString({ q, maxFee, deadline, onlyAvail, sort, page }) {
        const params = new URLSearchParams(window.location.search);
        if (q) params.set('q', q); else params.delete('q');
        if (typeof maxFee === 'number') params.set('fee', String(maxFee)); else params.delete('fee');
        if (deadline instanceof Date && !isNaN(deadline)) {
            // keep YYYY-MM-DD format
            const yyyy = deadline.getFullYear();
            const mm = String(deadline.getMonth() + 1).padStart(2, '0');
            const dd = String(deadline.getDate()).padStart(2, '0');
            params.set('deadline', `${yyyy}-${mm}-${dd}`);
        } else {
            params.delete('deadline');
        }
        if (onlyAvail) params.set('available', '1'); else params.delete('available');
        if (sort) params.set('sort', sort); else params.delete('sort');
        if (page && page > 1) params.set('page', String(page)); else params.delete('page');
        const query = params.toString();
        const url = query ? `${window.location.pathname}?${query}` : window.location.pathname;
        window.history.replaceState(null, '', url);
    }

    function setFiltersFromURL() {
        const params = new URLSearchParams(window.location.search);
        const titleI = document.getElementById('filter-title');
        const feeI = document.getElementById('filter-fee');
        const deadlineI = document.getElementById('filter-deadline');
        const availI = document.getElementById('filter-available');
        const sortI = document.getElementById('filter-sort');

        if (titleI && params.has('q')) titleI.value = params.get('q') || '';
        if (feeI && params.has('fee')) feeI.value = params.get('fee') || '';
        if (deadlineI && params.has('deadline')) deadlineI.value = params.get('deadline') || '';
        if (availI) availI.checked = params.get('available') === '1';
        if (sortI && params.has('sort')) sortI.value = params.get('sort') || '';
        const page = parseInt(params.get('page') || '1', 10);
        if (!isNaN(page) && page > 0) __PAGE = page; else __PAGE = 1;
    }

    async function fetchPublishedEvents() {
        // Proactively include Bearer token on first request and use apiFetch helper.
        // Also normalize URL with trailing slash to avoid a 307 redirect.
        const path = '/events/?status=published';
        const token = (function () {
            try {
                if (window.auth && typeof window.auth.getCookie === 'function') {
                    return window.auth.getCookie('dh_token');
                }
                const m = document.cookie.match(/(?:^|; )dh_token=([^;]*)/);
                return m ? decodeURIComponent(m[1]) : null;
            } catch { return null; }
        })();

        const headers = { 'Accept': 'application/json' };
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const res = await (window.apiFetch ? window.apiFetch(path, { method: 'GET', headers, credentials: 'omit' })
            : fetch(((window.BACKEND_BASE_URL || '') + path), { method: 'GET', credentials: 'omit', headers }));
        if (!res.ok) {
            // If unauthorized, trigger redirect and surface an error to break the flow
            if (res.status === 401 || res.status === 419) {
                if (typeof window.handleUnauthorized === 'function') window.handleUnauthorized();
                const err = new Error(`HTTP ${res.status}`);
                err.status = res.status;
                throw err;
            }
            throw new Error(`HTTP ${res.status}`);
        }
        return res.json();
    }

    document.addEventListener('DOMContentLoaded', async function () {
        const listEl = document.getElementById('events-list');
        if (!listEl) return;

        renderLoading(listEl);
        bindFilterEvents();
        // initialize filters from URL before first render
        setFiltersFromURL();
        try {
            const events = await fetchPublishedEvents();
            __ALL_EVENTS = Array.isArray(events) ? events : (events?.events || []);
            console.log(__ALL_EVENTS);
            applyFilters();
        } catch (err) {
            console.error('Failed to load events', err);
            // If unauthorized, rely on redirect and avoid rendering the generic error
            if (err && (err.status === 401 || err.status === 419)) {
                return; // redirect already handled
            }
            renderError(listEl, 'Could not load events. Please try again later.');
        }
    });
})();
