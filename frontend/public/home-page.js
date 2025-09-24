// Home page script: verify authentication and fetch/render published events.
// This file is loaded by home.html and is responsible for dynamic content.

(function () {
    // Cached profile info for ZIP eligibility and defaults
    let __USER_PROFILE = null;
    let __USER_ZIP = null;
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

    // Provider modal helpers
    function openProviderModal() {
        const tpl = $tpl('tpl-provider-modal');
        if (!tpl) return null;
        const node = tpl.cloneNode(true);
        document.body.appendChild(node);
        // close handlers
        const closer = () => node.remove();
        node.querySelector('.provider-close')?.addEventListener('click', closer);
        node.addEventListener('click', (e) => { if (e.target === node) closer(); });
        return node;
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
            const zipBadgeEl = node.querySelector('.event-zip-badge');

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

            // Spots remaining
            const capacity = e.capacity && Number.isInteger(e.capacity) && e.capacity > 0 ? e.capacity : 6; // assume 6 if not set
            spotsEl.textContent = 'Loading...';
            spotsEl.className = 'event-spots text-sm font-semibold text-gray-600';
            // Simple availability logic: capacity - attendee_count
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

            // ZIP eligibility hint (client-side only)
            try {
                if (zipBadgeEl && __USER_ZIP && Array.isArray(e.allowed_zips) && !e.allowed_zips.includes(__USER_ZIP)) {
                    zipBadgeEl.classList.remove('hidden');
                }
            } catch {}

            // CTA -> open registration modal (Solo/Team)
            // Try to resolve an event id (API might return id/_id)
            const eventId = e.id || e._id || e.eventId || (e.event && (e.event.id || e.event._id));
            ctaEl.href = '#';
            ctaEl.addEventListener('click', (ev) => {
                ev.preventDefault();
                if (!eventId || ctaEl.getAttribute('aria-disabled') === 'true') return;
                openRegisterModal({ event: e, eventId, spotsEl, ctaEl, placeLeft });
            });

            container.appendChild(node);
        });
    }

    // Registration modal (Solo/Team)
    function openRegisterModal(ctx) {
        const { event: eventObj, eventId, spotsEl, ctaEl, placeLeft } = ctx;
        const tpl = document.getElementById('tpl-register-modal');
        if (!tpl) return;
        const modalFrag = tpl.content.cloneNode(true);
        const overlay = modalFrag.querySelector('div.fixed');
        const form = modalFrag.querySelector('form.reg-form');
        form.elements.event_id.value = eventId;

        const close = () => overlay.remove();
        modalFrag.querySelector('.reg-close')?.addEventListener('click', close);
        modalFrag.querySelector('.reg-cancel')?.addEventListener('click', close);
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

        // Tabs
        const tabSolo = modalFrag.querySelector('.tab-btn.solo');
        const tabTeam = modalFrag.querySelector('.tab-btn.team');
        const formSolo = modalFrag.querySelector('.form-solo');
        const formTeam = modalFrag.querySelector('.form-team');
        function setMode(mode) {
            form.dataset.mode = mode;
            if (mode === 'solo') {
                formSolo.classList.remove('hidden');
                formTeam.classList.add('hidden');
                tabSolo.classList.add('bg-white','text-[#172a3a]','font-semibold');
                tabTeam.classList.remove('bg-white','text-[#172a3a]','font-semibold');
            } else {
                formTeam.classList.remove('hidden');
                formSolo.classList.add('hidden');
                tabTeam.classList.add('bg-white','text-[#172a3a]','font-semibold');
                tabSolo.classList.remove('bg-white','text-[#172a3a]','font-semibold');
            }
        }
        tabSolo.addEventListener('click', () => setMode('solo'));
        tabTeam.addEventListener('click', () => setMode('team'));

        // Team toggles
        const teamRoot = modalFrag.querySelector('.form-team');
        const partnerExisting = teamRoot.querySelector('.partner-existing');
        const partnerExternal = teamRoot.querySelector('.partner-external');
        teamRoot.addEventListener('change', (e) => {
            if (e.target.name === 'partner_mode') {
                const isExternal = e.target.value === 'external';
                partnerExternal.classList.toggle('hidden', !isExternal);
                partnerExisting.classList.toggle('hidden', isExternal);
            }
        });

        // Aggregated diet hint
        const teamDietSummary = modalFrag.querySelector('#team-diet-summary');
        teamRoot.addEventListener('input', () => {
            const partnerDiet = teamRoot.querySelector('[name="partner_dietary"]').value || '';
            const selfDiet = (__USER_PROFILE && __USER_PROFILE.preferences && __USER_PROFILE.preferences.default_dietary) || '';
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
                const payload = buildRegistrationPayload(form, __USER_PROFILE);
                const headers = withAuthHeader({ 'Accept': 'application/json', 'Content-Type': 'application/json' });
                const path = `/events/${encodeURIComponent(payload.event_id)}/register`;
                const res = await fetch((window.BACKEND_BASE_URL || '') + path, {
                    method: 'POST',
                    headers,
                    body: JSON.stringify(payload.body),
                    credentials: 'omit'
                });
                if (res.status === 401 || res.status === 419) {
                    if (typeof window.handleUnauthorized === 'function') window.handleUnauthorized();
                    return;
                }
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const body = await res.json();
                const paymentLink = body.payment_link || (body.payment && (body.payment.link || body.payment.url));

                // Optimistic UI update
                if (spotsEl) {
                    const left = Math.max((placeLeft || 1) - 1, 0);
                    if (left <= 0) {
                        spotsEl.textContent = 'Event Full';
                        ctaEl.classList.add('opacity-60', 'cursor-not-allowed');
                        ctaEl.setAttribute('aria-disabled','true');
                        ctaEl.tabIndex = -1;
                    } else {
                        spotsEl.textContent = `${left} spots left`;
                    }
                }

                // Direct payment link
                if (paymentLink) {
                    const base = window.BACKEND_BASE_URL || '';
                    window.location.href = paymentLink.startsWith('http') ? paymentLink : base + paymentLink;
                    return;
                }

                // Provider selection if needed
                const regId = body.registration_id || body.registrationId || (Array.isArray(body.registration_ids) && body.registration_ids[0]) || (Array.isArray(body.registrationIds) && body.registrationIds[0]);
                const amountCents = typeof eventObj.fee_cents === 'number' ? eventObj.fee_cents : 0;
                if (amountCents > 0 && regId) {
                    const base = window.BACKEND_BASE_URL || '';
                    let providers = ['paypal','stripe','wero'];
                    try {
                        const provRes = await fetch(base + '/payments/providers', { method: 'GET', credentials: 'omit', headers: withAuthHeader({ 'Accept': 'application/json' }) });
                        if (provRes.ok) {
                            const provs = await provRes.json();
                            providers = (provs && provs.providers) ? provs.providers : (Array.isArray(provs) ? provs : providers);
                        }
                    } catch {}

                    const provModal = openProviderModal();
                    if (provModal) {
                        provModal.querySelectorAll('[data-provider]')?.forEach(btn => {
                            const p = (btn.getAttribute('data-provider') || '').toLowerCase();
                            if (!providers.includes(p)) {
                                btn.classList.add('opacity-40', 'pointer-events-none');
                                btn.setAttribute('aria-disabled','true');
                            }
                        });
                        const onChoose = async (provider) => {
                            try {
                                const createRes = await fetch(base + '/payments/create', {
                                    method: 'POST',
                                    headers: withAuthHeader({ 'Accept': 'application/json', 'Content-Type': 'application/json' }),
                                    body: JSON.stringify({ registration_id: regId, provider })
                                });
                                if (!createRes.ok) throw new Error(`HTTP ${createRes.status}`);
                                const created = await createRes.json();
                                const link = created.payment_link || (created.instructions && (created.instructions.approval_link || created.instructions.link));
                                if (link) {
                                    window.location.href = link.startsWith('http') ? link : base + link;
                                    return;
                                }
                            } catch (err) {
                                console.error('Create payment failed', err);
                            } finally {
                                provModal.remove();
                            }
                        };
                        provModal.querySelectorAll('[data-provider]')?.forEach(btn => {
                            btn.addEventListener('click', (e) => {
                                e.preventDefault();
                                const p = (btn.getAttribute('data-provider') || '').toLowerCase();
                                if (providers.includes(p)) onChoose(p);
                            });
                        });
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

    function aggregateDiet(a, b) {
        const order = ['omnivore', 'vegetarian', 'vegan'];
        const ca = order.indexOf((a||'').toLowerCase());
        const cb = order.indexOf((b||'').toLowerCase());
        if (ca === -1) return b || a || '';
        if (cb === -1) return a || b || '';
        return order[Math.max(ca, cb)];
    }

    function buildRegistrationPayload(form, profile) {
        const event_id = form.elements.event_id.value;
        const mode = form.dataset.mode || 'solo';
        if (mode === 'solo') {
            const dietary = form.elements.dietary.value;
            const kitchenVal = form.elements.kitchen.value;
            const mainCourseVal = form.elements.main_course.value;
            const course = form.elements.course.value;
            if (course === 'main') {
                const profileMain = !!(profile && profile.preferences && profile.preferences.main_course_possible);
                if (mainCourseVal === 'no' || (!mainCourseVal && !profileMain)) {
                    throw new Error('Cannot select Main if main course is not possible.');
                }
            }
            const preferences = {};
            if (dietary) preferences.default_dietary = dietary;
            if (kitchenVal) preferences.kitchen_available = (kitchenVal === 'yes');
            if (mainCourseVal) preferences.main_course_possible = (mainCourseVal === 'yes');
            if (course) preferences.course_preference = course;
            return { event_id, body: { team_size: 1, preferences } };
        }
        // team mode
        const partnerMode = form.querySelector('[name="partner_mode"]:checked')?.value || 'existing';
        const preferences = {};
        const invited_emails = [];
        const teamCourse = form.elements.team_course.value;
        if (teamCourse) preferences.course_preference = teamCourse;
        const cookLocation = form.elements.cook_location.value; // 'self' or 'partner'
        if (cookLocation) preferences.cook_at = cookLocation;
        if (partnerMode === 'existing') {
            const email = (form.elements.partner_email.value || '').trim();
            if (!email) throw new Error('Partner email is required.');
            invited_emails.push(email);
        } else {
            const name = (form.elements.partner_name.value || '').trim();
            const email = (form.elements.partner_email_ext.value || '').trim();
            if (!name || !email) throw new Error('Partner name and email are required.');
            invited_emails.push(email);
            preferences.partner_external = {
                name,
                email,
                gender: form.elements.partner_gender.value || undefined,
                dietary: form.elements.partner_dietary.value || undefined,
                field_of_study: form.elements.partner_field.value || undefined
            };
        }
        // Basic validation for kitchen if user chooses self
        const selfKitchen = !!(profile && profile.preferences && profile.preferences.kitchen_available);
        if (cookLocation === 'self' && !selfKitchen) {
            throw new Error('Your profile says no kitchen available, but you selected to cook at your place.');
        }
        return { event_id, body: { team_size: 2, invited_emails, preferences } };
    }

    // --- Filtering ---
    let __ALL_EVENTS = [];
    let __PAGE = 1;
    const PAGE_SIZE = 6;

    // --- URL <-> state helpers ---
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

    function syncFormFromFilters(filters) {
        const titleI = document.getElementById('filter-title');
        const feeI = document.getElementById('filter-fee');
        const deadlineI = document.getElementById('filter-deadline');
        const availI = document.getElementById('filter-available');
        const sortI = document.getElementById('filter-sort');
        if (titleI) titleI.value = filters.q || '';
        if (feeI) feeI.value = filters.maxFee != null && !Number.isNaN(filters.maxFee) ? String(filters.maxFee) : '';
        if (deadlineI) {
            // format yyyy-mm-dd
            if (filters.deadline instanceof Date && !Number.isNaN(filters.deadline)) {
                const d = filters.deadline;
                const yyyy = d.getFullYear();
                const mm = String(d.getMonth() + 1).padStart(2, '0');
                const dd = String(d.getDate()).padStart(2, '0');
                deadlineI.value = `${yyyy}-${mm}-${dd}`;
            } else {
                deadlineI.value = '';
            }
        }
        if (availI) availI.checked = !!filters.onlyAvail;
        if (sortI) sortI.value = filters.sort || '';
    }

    function writeFiltersToURL(filters, page) {
        const sp = new URLSearchParams();
        if (filters.q) sp.set('q', filters.q);
        if (filters.maxFee != null) sp.set('fee', String(filters.maxFee));
        if (filters.deadline instanceof Date && !Number.isNaN(filters.deadline)) {
            const d = filters.deadline;
            const yyyy = d.getFullYear();
            const mm = String(d.getMonth() + 1).padStart(2, '0');
            const dd = String(d.getDate()).padStart(2, '0');
            sp.set('deadline', `${yyyy}-${mm}-${dd}`);
        }
        if (filters.onlyAvail) sp.set('avail', '1');
        if (filters.sort) sp.set('sort', filters.sort);
        if (page && page > 1) sp.set('page', String(page));
        const newUrl = `${location.pathname}${sp.toString() ? '?' + sp.toString() : ''}${location.hash || ''}`;
        history.replaceState(null, '', newUrl);
    }

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
        // write current filters to URL
        writeFiltersToURL({ q, maxFee, deadline, onlyAvail, sort }, __PAGE);
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

    async function fetchPublishedEvents() {
        // Proactively include Bearer token on first request and use apiFetch helper.
        // Also normalize URL with trailing slash to avoid a 307 redirect.
        const path = '/events/?status=open';
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

    async function fetchMyEvents() {
        // Use participant=me filter exposed by backend list_events implementation
        const path = '/events?participant=me';
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
        if (!res.ok) return [];
        return res.json();
    }

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
        events.forEach(ev => {
            const node = tpl.content.cloneNode(true);
            node.querySelector('.reg-title').textContent = ev.title || ev.name || 'Event';
            node.querySelector('.reg-date').textContent = ev.start_at ? new Date(ev.start_at).toLocaleString() : (ev.date || '');
            const badge = node.querySelector('.reg-badge');
            const match = node.querySelector('.reg-match');
            const note = node.querySelector('.reg-note');
            const btnPay = node.querySelector('.reg-pay');
            const aGo = node.querySelector('.reg-go');
            const eventId = ev.id || ev._id || ev.eventId;
            aGo.href = `/event.html?id=${encodeURIComponent(eventId)}`;

            // Try payment and registration hints from localStorage if present
            const key = `dh:lastReg:${eventId}`;
            const regInfoRaw = localStorage.getItem(key);
            let regInfo = null;
            try { regInfo = regInfoRaw ? JSON.parse(regInfoRaw) : null; } catch {}

            // Badge for status (best-effort)
            if (regInfo && regInfo.status) {
                badge.textContent = regInfo.status;
            } else {
                badge.textContent = 'registered';
            }

            // Payment hint
            if (typeof ev.fee_cents === 'number' && ev.fee_cents > 0) {
                if (!regInfo || (regInfo.payment_status && regInfo.payment_status !== 'succeeded')) {
                    note.classList.remove('hidden');
                    note.textContent = "You haven't paid the fee yet.";
                    if (regInfo && regInfo.registration_id) {
                        btnPay.classList.remove('hidden');
                        btnPay.addEventListener('click', async () => {
                            try {
                                const base = window.BACKEND_BASE_URL || '';
                                const headers = withAuthHeader({ 'Accept': 'application/json', 'Content-Type': 'application/json' });
                                const r = await fetch(base + '/payments/create', {
                                    method: 'POST',
                                    headers,
                                    credentials: 'omit',
                                    body: JSON.stringify({ registration_id: regInfo.registration_id })
                                });
                                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                                const data = await r.json();
                                const link = data.payment_link || (data.instructions && (data.instructions.approval_link || data.instructions.link));
                                if (link) {
                                    window.location.assign(link.startsWith('http') ? link : (base + link));
                                }
                            } catch (err) {
                                alert('Could not create payment.');
                            }
                        });
                    }
                }
            }

            // Matching hint (best-effort; backend has get_my_plan helper but not exposed via route)
            match.textContent = ev.matching_status ? `Matching: ${ev.matching_status}` : '';
            list.appendChild(node);
        });
        wrap.classList.remove('hidden');
    }

    document.addEventListener('DOMContentLoaded', async function () {
        const listEl = document.getElementById('events-list');
        if (!listEl) return;

        renderLoading(listEl);
        bindFilterEvents();
        // Initialize form from URL and page state
        const urlFilters = parseFiltersFromURL();
        __PAGE = urlFilters.page || 1;
        syncFormFromFilters(urlFilters);
        // Preload profile for ZIP eligibility and defaults
        try {
            const path = '/profile';
            const headers = withAuthHeader({ 'Accept': 'application/json' });
            const res = await (window.apiFetch ? window.apiFetch(path, { method: 'GET', headers, credentials: 'omit' })
                : fetch(((window.BACKEND_BASE_URL || '') + path), { method: 'GET', credentials: 'omit', headers }));
            if (res.ok) {
                __USER_PROFILE = await res.json();
                const addr = __USER_PROFILE.address || __USER_PROFILE.address_public || {};
                __USER_ZIP = addr.postal_code || __USER_PROFILE.postal_code || null;
            }
        } catch {}
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
        // Load my registrations banner (best-effort)
        try {
            const myEvents = await fetchMyEvents();
            renderMyRegistrations(Array.isArray(myEvents) ? myEvents : []);
        } catch {}
    });
})();
