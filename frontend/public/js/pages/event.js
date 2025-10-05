// event.js - Personalized Event Dashboard
// Responsibilities:
// - Parse ?id=EVENT_ID
// - Initialize CSRF/auth (supports legacy dh_token Bearer mode)
// - Fetch event details and user registration/payment state (if available)
// - Fetch user's plan/itinerary (sections: starter/appetizer, main, dessert, party)
// - Render actionable buttons: refresh plan (re-fetch), open group chats (navigates to chat), cancel registration (placeholder)
// - Show loading spinner until both event & plan attempts complete
// - Graceful fallbacks when data absent (no plan yet, not registered, etc.)

/* global window, document */

(async () => {
	const qs = new URLSearchParams(window.location.search);
	const eventId = qs.get('id');
	const titleEl = document.getElementById('eventTitle');
	const eventMetaEl = document.getElementById('eventMeta');
	const spinnerEl = document.getElementById('loadingSpinner');
	const planSection = document.getElementById('planSection');
	const planContainer = document.getElementById('planContainer');
	const statusMessages = document.getElementById('statusMessages');
	const afterPartySection = document.getElementById('afterPartySection');
	const afterPartyBody = document.getElementById('afterPartyBody');
	const regSection = document.getElementById('registrationStatusSection');
	const regBody = document.getElementById('registrationStatusBody');
	const missingIdBanner = document.getElementById('missingIdBanner');
	const actionButtons = document.getElementById('actionButtons');
	const payNowBtn = document.getElementById('payNowBtn');
	const chooseProviderBtn = document.getElementById('chooseProviderBtn');
	const capacityWrap = document.getElementById('capacityBarWrap');
	const capacityBar = document.getElementById('capacityBar');
	const capacityLabel = document.getElementById('capacityLabel');
	const badgesEl = document.getElementById('eventBadges');
	const refreshPlanBtn = document.getElementById('refreshPlanBtn');
	const openChatsBtn = document.getElementById('openChatsBtn');
	const cancelRegBtn = document.getElementById('cancelRegistrationBtn');
	// Solo registration form elements
	const soloRegSection = document.getElementById('soloRegistrationSection');
	const soloRegForm = document.getElementById('soloRegistrationForm');
	const srDiet = document.getElementById('srDiet');
	const srKitchen = document.getElementById('srKitchenAvailable');
	const srMain = document.getElementById('srMainPossible');
	const srMainWrapper = document.getElementById('srMainWrapper');
	const srCourseChoices = document.getElementById('srCourseChoices');
	const srStatus = document.getElementById('srStatus');
	const srSubmit = document.getElementById('srSubmit');
	const srMainHint = document.getElementById('srMainHint');

	// Shared fetch abstraction (prefer new namespaced dh.* if present)
	const apiFetch = (window.dh && window.dh.apiFetch) || window.apiFetch || fetch;
	const initCsrf = (window.dh && window.dh.initCsrf) || window.initCsrf || (async () => {});

	// Helper: add status message
	function pushMessage(html, variant = 'info') {
		const div = document.createElement('div');
		const colors = {
			info: 'bg-blue-50 border-blue-200 text-blue-700',
			warn: 'bg-amber-50 border-amber-200 text-amber-800',
			error: 'bg-red-50 border-red-200 text-red-700',
			success: 'bg-green-50 border-green-200 text-green-700'
		};
		div.className = `text-sm border rounded p-3 ${colors[variant] || colors.info}`;
		div.innerHTML = html;
		statusMessages.appendChild(div);
	}

	// If no event id -> show banner + redirect
	if (!eventId) {
		missingIdBanner.textContent = 'Missing event id (?id=EVENT_ID). Redirecting…';
		missingIdBanner.classList.remove('hidden');
		setTimeout(() => {
			window.location.href = '/home.html';
		}, 1800);
		return; // abort further logic
	}

	// Legacy token detection (dh_token cookie)
	function hasLegacyTokenCookie() {
		try { return document.cookie.split(';').some(c => c.trim().startsWith('dh_token=')); } catch { return false; }
	}

	const legacyBearer = hasLegacyTokenCookie();

	// Initialize CSRF if not using legacy bearer
	try {
		if (!legacyBearer) {
			await initCsrf();
		}
	} catch (e) {
		pushMessage('Failed to initialize CSRF protection. Some actions may fail.', 'warn');
	}

	// Concurrency: load event + plan in parallel. We'll also attempt registration lookup.
	let eventData = null;
	let planData = null;
	let registrationData = null; // placeholder (depends on available endpoint)
	let profileData = null; // to prefill registration form

	function authHeaders(base = {}) {
		// If legacy token present, attach Bearer header using cookie value
		if (!legacyBearer) return base; // cookie mode handled by credentials include inside apiFetch abstraction
		const token = (document.cookie.split(';').find(c => c.trim().startsWith('dh_token=')) || '').split('=')[1] || '';
		return { ...base, Authorization: `Bearer ${decodeURIComponent(token)}` };
	}

	async function fetchJson(path, opts = {}) {
		const finalOpts = { ...opts };
		finalOpts.headers = authHeaders(finalOpts.headers || {});
		// If using the shared apiFetch wrapper, it already sets credentials when cookie-based
		const resp = await apiFetch(path, finalOpts);
		if (!resp.ok) {
			const text = await resp.text().catch(() => resp.statusText);
			throw new Error(`Request failed (${resp.status}): ${text}`);
		}
		const ct = resp.headers.get('content-type') || '';
		if (ct.includes('application/json')) return resp.json();
		return resp.text();
	}

	async function loadEvent() {
		try {
			const data = await fetchJson(`/events/${encodeURIComponent(eventId)}`);
			eventData = data;
			// Title & meta
			if (data.title) {
				document.title = `${data.title} – Dinner Hopping`;
				titleEl.textContent = data.title;
			}
			const metaBits = [];
			if (data.date) metaBits.push(`<span>${data.date}</span>`);
			if (data.city) metaBits.push(`<span>${data.city}</span>`);
			if (data.status) metaBits.push(`<span class='capitalize'>${data.status}</span>`);
			eventMetaEl.innerHTML = metaBits.join(' · ');

			// Badges: fee / refund eligibility / chat
			badgesEl.innerHTML = '';
			if ((data.fee_cents||0) > 0){
				const feeB = document.createElement('span');
				feeB.className = 'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-amber-50 text-amber-700 ring-1 ring-amber-200';
				feeB.textContent = `Fee: €${(data.fee_cents/100).toFixed(2)}`;
				badgesEl.appendChild(feeB);
			}
			if (data.refund_on_cancellation && (data.fee_cents||0)>0){
				const refB = document.createElement('span');
				refB.className = 'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200';
				refB.textContent = 'Refundable on cancellation';
				badgesEl.appendChild(refB);
			}
			if (data.chat_enabled){
				const chatB = document.createElement('span');
				chatB.className = 'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200';
				chatB.textContent = 'Chat Enabled';
				badgesEl.appendChild(chatB);
			}

			// Capacity bar
			if (Number.isInteger(data.capacity) && data.capacity>0){
				const count = Number(data.attendee_count)||0;
				const pct = Math.min(100, Math.max(0, (count / data.capacity)*100));
				capacityWrap.classList.remove('hidden');
				capacityBar.setAttribute('aria-valuemax', String(data.capacity));
				capacityBar.setAttribute('aria-valuenow', String(count));
				const fill = capacityBar.firstElementChild; if (fill) fill.style.width = pct+'%';
				capacityLabel.textContent = `${count}/${data.capacity} registered (${Math.round(pct)}%)`;
			}

			// After party
			if (data.after_party_location && (data.after_party_location.address_public || data.after_party_location.address_public === null)) {
				const ap = data.after_party_location;
				afterPartySection.classList.remove('hidden');
				afterPartyBody.innerHTML = ap.address_public ? `${ap.address_public}` : 'Location not yet published';
			}
		} catch (e) {
			pushMessage(`Could not load event (id=${eventId}): ${e.message}`, 'error');
		}
	}

	async function loadProfileForPrefill(){
		// Only needed for showing solo registration form
		try {
			const resp = await fetchJson('/profile');
			profileData = resp || {};
		} catch(e){
			console.warn('Profile prefill failed', e);
		}
	}

	async function loadPlan() {
		try {
			const data = await fetchJson('/events/get_my_plan'); // backend helper at bottom of events.py (no id param)
			// Some deployments may namescape differently; if message indicates no plan we handle gracefully
			if (data && data.message && /No plan/i.test(data.message)) {
				pushMessage('No itinerary yet – matching has not been run or released.', 'info');
				planData = null;
				return;
			}
			// Ensure event match
			if (data && data.event_id && data.event_id !== eventId) {
				// User has a plan but for another event
				pushMessage('You have a plan for a different event. This event may not be matched yet.', 'warn');
			}
			planData = data;
		} catch (e) {
			pushMessage(`Could not load your itinerary: ${e.message}`, 'warn');
		}
	}

	// Placeholder: attempt to find registration for this user/event (by listing? or dedicated endpoint not present). We'll skip complex fetch if no endpoint.
	// Attempt to discover whether the current user is registered for this event.
	// Backend does not yet expose a direct "my registration for event" endpoint, so we:
	// 1. Call /events/?participant=me and see if this event id is in the list -> user is registered somehow (solo or team)
	// 2. Optimistically POST /registrations/solo (idempotent upsert for solo) to retrieve a registration_id when solo.
	//    - If user registered as team, backend returns 400 detail 'Already registered with a team for this event'. We then mark mode=team.
	//    - If previously cancelled solo, this reactivates the registration (acceptable UX for now).
	// 3. If fee exists but we cannot obtain a registration_id (team case), we display guidance.
	async function loadRegistration() {
		registrationData = null;
		try {
	            // Step 1: attempt to detect membership via /events/?participant=me across a set of status filters
	            const statusCandidates = [null,'open','coming_soon','matched','released','published','draft'];
	            let foundEvent = null;
	            for (const st of statusCandidates) {
	                if (foundEvent) break;
	                let url = '/events/?participant=me';
	                if (st) url += `&status=${encodeURIComponent(st)}`;
	                try {
	                    const resp = await fetchJson(url);
	                    if (Array.isArray(resp)) {
	                        foundEvent = resp.find(e => (e.id || e._id) === eventId) || foundEvent;
	                    }
	                } catch (err) {
	                    // Ignore individual filter failures
	                }
	            }
	            if (!foundEvent) return; // no evidence of registration
			// We have evidence of registration; initialize minimal object
	            registrationData = { status: 'registered', mode: 'unknown' };
			// Step 2: attempt to obtain a concrete registration_id via solo upsert
			try {
				const headers = { 'Accept': 'application/json', 'Content-Type': 'application/json' };
				const res = await apiFetch('/registrations/solo', { method: 'POST', headers, body: JSON.stringify({ event_id: eventId }) });
				if (res.ok) {
					const body = await res.json().catch(()=>({}));
					const regId = body.registration_id || body.registrationId || (Array.isArray(body.registration_ids)&&body.registration_ids[0]) || (Array.isArray(body.registrationIds)&&body.registrationIds[0]);
					if (regId) {
						registrationData.registration_id = String(regId);
						registrationData.amount_cents = body.amount_cents;
						registrationData.status = body.status || registrationData.status;
						registrationData.mode = 'solo';
					}
				} else {
					// Inspect error payload to detect team registration
					let detail = '';
					try { const errJson = await res.json(); detail = errJson.detail || errJson.message || ''; } catch {}
					if (/team for this event/i.test(detail)) {
						registrationData.mode = 'team';
						registrationData.team = true;
						// We cannot fetch team registration id without backend support.
					} else if (res.status === 401 || res.status === 419) {
						// Auth issue -> allow higher level handler to refresh
						console.warn('Auth error during registration discovery');
					} else {
						console.warn('Solo upsert failed', res.status, detail);
					}
				}
			} catch (e) {
				console.warn('Solo upsert attempt errored', e);
			}
		} catch (e) {
			console.warn('loadRegistration failed', e);
		}
	}

	function maybeShowSoloForm(){
		// Show form only if user NOT registered yet
		if (registrationData && (registrationData.mode === 'solo' || registrationData.mode === 'team')){
			return; // already registered
		}
		// Event must be open for registration
		if (!eventData) return;
		const status = (eventData.status||'').toLowerCase();
		if (!['open','coming_soon'].includes(status)) return; // conservative: only open (coming_soon maybe later)
		// Prefill values
		if (profileData){
			if (srDiet && profileData.default_dietary_preference){ srDiet.value = profileData.default_dietary_preference; }
			if (srKitchen) srKitchen.checked = !!profileData.kitchen_available;
			if (srMain) srMain.checked = !!profileData.main_course_possible;
		}
		updateMainCourseAvailability();
		soloRegSection.classList.remove('hidden');
	}

	function updateMainCourseAvailability(){
		if (!srMainWrapper) return;
		const canMain = srMain && srMain.checked;
		// Disable main course radio if main not possible
		const mainRadio = srCourseChoices?.querySelector('input[type=radio][value="main"]');
		if (mainRadio){
			if (!canMain){
				mainRadio.disabled = true;
				if (mainRadio.checked){
					// fallback to no preference
					const none = srCourseChoices.querySelector('input[type=radio][value=""]');
					if (none){ none.checked = true; }
				}
				srMainWrapper.classList.add('opacity-50');
				srMainHint.classList.remove('text-teal-700');
				srMainHint.classList.add('text-gray-500');
			}else{
				mainRadio.disabled = false;
				srMainWrapper.classList.remove('opacity-50');
				srMainHint.classList.remove('text-gray-500');
				srMainHint.classList.add('text-teal-700');
			}
		}
	}

	if (srMain){ srMain.addEventListener('change', updateMainCourseAvailability); }
	if (srKitchen){ srKitchen.addEventListener('change', () => {}); } // reserved future logic

	if (soloRegForm){
		soloRegForm.addEventListener('submit', async (e) => {
			e.preventDefault();
			if (!eventId) return;
			srStatus.textContent = '';
			srSubmit.disabled = true;
			try {
				const diet = srDiet?.value || 'omnivore';
				const kitchen_available = !!(srKitchen?.checked);
				const main_course_possible = !!(srMain?.checked);
				const courseRadio = srCourseChoices?.querySelector('input[type=radio]:checked');
				const course_preference = courseRadio ? (courseRadio.value||'') : '';
				if (course_preference === 'main' && !main_course_possible){
					throw new Error('Main Course preference requires that your kitchen is marked Main Course Possible.');
				}
				const payload = { event_id: eventId, dietary_preference: diet, kitchen_available, main_course_possible };
				if (course_preference){ payload.course_preference = course_preference; }
				const headers = { 'Content-Type':'application/json','Accept':'application/json' };
				const resp = await apiFetch('/registrations/solo',{ method:'POST', headers, body: JSON.stringify(payload) });
				const body = await resp.json().catch(()=>({}));
				if (!resp.ok){
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
				pushMessage('Registered successfully.','success');
				// Redirect to payment if fee due
				if ((eventData?.fee_cents||0) > 0){
					// Defer a tick to allow UI update
					setTimeout(()=>{ if (payNowBtn && !payNowBtn.classList.contains('hidden')){ payNowBtn.click(); } }, 600);
				}
			} catch(err){
				srStatus.textContent = (err && err.message) ? err.message : 'Registration failed';
				srStatus.classList.remove('text-gray-500');
				srStatus.classList.add('text-red-600');
				console.error(err);
			} finally {
				srSubmit.disabled = false;
			}
		});
	}

	function renderRegistration() {
		regSection.classList.remove('hidden');
		if (!registrationData) {
			regBody.innerHTML = `<div class="text-sm">You are either not registered yet or your registration hasn't been detected. If you believe this is an error, try:<ul class="list-disc ml-5 mt-1"><li>Refreshing this page</li><li>Re-opening the event list and ensuring you're registered</li><li>Submitting the Solo registration form again (safe if you originally registered solo)</li></ul></div>`;
			cancelRegBtn.disabled = true;
			if (payNowBtn) payNowBtn.classList.add('hidden');
			return;
		}
		// Provide summary depending on mode
		if (registrationData.mode === 'team' && !registrationData.registration_id) {
			regBody.innerHTML = `<div class="text-sm">You are registered as part of a <strong>team</strong>. Detailed team registration data (and payment initiation) isn't yet available on this page without backend support.<br><br><em>Workaround:</em> The team creator can open the registration modal again or an organizer can assist with payment if required.</div>`;
		} else if (registrationData.mode === 'solo' && registrationData.registration_id) {
			const amount = (eventData?.fee_cents || 0) / 100;
			let payLine = '';
			if ((eventData?.fee_cents||0) > 0) {
				payLine = `<div class="mt-2 text-xs ${payNowBtn && !payNowBtn.classList.contains('hidden') ? 'text-amber-700':'text-gray-600'}">Event fee: €${amount.toFixed(2)}</div>`;
			}
			regBody.innerHTML = `<div class="text-sm">Registered (solo).${payLine}</div>`;
		} else {
			// Fallback generic
			regBody.innerHTML = `<div class="text-sm">Registration detected.</div>`;
		}
		// Future: display status & payment
		if (registrationData.registration_id){
			const unpaid = (eventData?.fee_cents||0) > 0 && !(registrationData.payment_status||'').match(/paid|succeeded/i);
			if (unpaid && payNowBtn){
				payNowBtn.classList.remove('hidden');
				payNowBtn.disabled = false;
				payNowBtn.onclick = () => startPaymentFlow({ quick:true });
			}
			if (unpaid && chooseProviderBtn){
				chooseProviderBtn.classList.remove('hidden');
				chooseProviderBtn.disabled = false;
				chooseProviderBtn.onclick = () => startPaymentFlow({ quick:false });
			} else if (chooseProviderBtn){ chooseProviderBtn.classList.add('hidden'); }
		}
	}

	async function fetchProviders(){
		let providers=['wero']; let def='wero';
		try{ if (window.dh?.apiGet){ const { res, data } = await window.dh.apiGet('/payments/providers'); if (res.ok){ if (Array.isArray(data.providers)) providers = data.providers.slice(); else if (Array.isArray(data)) providers=data.slice(); if (typeof data.default==='string') def=data.default; } } }catch{}
		if (!providers.length) providers=['wero'];
		if (!providers.includes(def)) def = providers[0];
		return { providers, def };
	}

	function buildProviderChooser(providers, def){
		const overlay = document.createElement('div');
		overlay.className='fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4';
		const panel = document.createElement('div');
		panel.className='bg-white rounded-lg shadow-xl w-full max-w-sm p-5 space-y-4';
		panel.innerHTML = '<h3 class="text-sm font-semibold text-gray-700">Select Payment Provider</h3>';
		const list = document.createElement('div'); list.className='space-y-2';
		providers.forEach(p=>{
			const btn=document.createElement('button');
			btn.type='button';
			btn.className='w-full px-3 py-2 rounded border text-sm flex items-center justify-between hover:bg-gray-50';
			btn.dataset.provider=p;
			btn.innerHTML=`<span class="capitalize">${p}</span>${p===def?'<span class="text-xs text-teal-600">(default)</span>':''}`;
			list.appendChild(btn);
		});
		const cancel=document.createElement('button');
		cancel.type='button';
		cancel.className='w-full mt-2 px-3 py-2 rounded bg-gray-200 hover:bg-gray-300 text-gray-800 text-sm';
		cancel.textContent='Cancel';
		panel.appendChild(list); panel.appendChild(cancel); overlay.appendChild(panel);
		document.body.appendChild(overlay);
		return { overlay, panel, list, cancel };
	}

	async function startPaymentFlow({ quick }){
		if (!registrationData?.registration_id) return;
		const amount = eventData?.fee_cents || 0;
		if (!amount){ alert('No fee to pay.'); return; }
		payNowBtn && (payNowBtn.disabled=true);
		chooseProviderBtn && (chooseProviderBtn.disabled=true);
		try {
			const { providers, def } = await fetchProviders();
			let provider = def;
			if (!quick && providers.length>1){
				const ui = buildProviderChooser(providers, def);
				provider = await new Promise(resolve=>{
					ui.list.addEventListener('click', e=>{ const b=e.target.closest('button[data-provider]'); if(!b)return; resolve(b.dataset.provider); ui.overlay.remove(); });
					ui.cancel.addEventListener('click', ()=>{ resolve(null); ui.overlay.remove(); });
				});
				if (!provider){ return; }
			}
			const payPayload = { registration_id: registrationData.registration_id, provider, amount_cents: amount };
			const { res: createRes, data: payData } = await (window.dh?.apiPost ? window.dh.apiPost('/payments/create', payPayload) : { res:{ ok:false }, data:{} });
			if (!createRes.ok) throw new Error(`HTTP ${createRes.status}`);
			const link = payData.payment_link || (payData.instructions && (payData.instructions.approval_link || payData.instructions.link));
			if (link) window.location.assign(link.startsWith('http')? link: window.BACKEND_BASE_URL + link);
			else alert('Payment initiated. Follow provider instructions.');
		} catch(e){ alert(e.message || 'Payment failed'); }
		finally { payNowBtn && (payNowBtn.disabled=false); chooseProviderBtn && (chooseProviderBtn.disabled=false); }
	}

	function renderPlan() {
		if (!planData || !Array.isArray(planData.sections) || planData.sections.length === 0) {
			planSection.classList.add('hidden');
			return;
		}
		planSection.classList.remove('hidden');
		planContainer.innerHTML = '';
		planData.sections.forEach((section, idx) => {
			const card = document.createElement('div');
			card.className = 'border rounded-md p-4 bg-white shadow-sm';
			const meal = (section.meal || `Phase ${idx+1}`).replace(/_/g,' ');
			const host = section.host_email ? `<span class="font-medium">Host:</span> ${section.host_email}` : '<span class="italic text-gray-400">Host TBD</span>';
			const guests = (section.guests || []).filter(g => g !== section.host_email).map(g => `<li>${g}</li>`).join('');
			const loc = section.host_location ? `<div class="mt-1 text-xs text-gray-500">Approx. location: ${section.host_location.address_public || 'Area disclosed later'}</div>` : '';
			card.innerHTML = `
				<div class="flex items-center justify-between gap-2 flex-wrap">
					<h3 class="text-sm font-semibold tracking-wide uppercase text-gray-700">${meal}</h3>
					${section.time ? `<span class="text-xs rounded bg-gray-100 px-2 py-1 text-gray-600">${section.time}</span>` : ''}
				</div>
				<div class="mt-2 text-sm space-y-1">
					<div>${host}</div>
					${loc}
					<div class="mt-2">
						<span class="font-medium">Guests:</span>
						${(guests ? `<ul class="list-disc ml-5 text-xs mt-1 space-y-0.5">${guests}</ul>` : '<span class="text-xs text-gray-400">No guests listed</span>')}
					</div>
				</div>`;
			planContainer.appendChild(card);
		});
	}

	function finalizeUI() {
		spinnerEl.classList.add('hidden');
		renderRegistration();
		renderPlan();
		actionButtons.hidden = false;
	}

	refreshPlanBtn.addEventListener('click', async () => {
		refreshPlanBtn.disabled = true;
		try {
			await loadPlan();
			renderPlan();
			pushMessage('Itinerary refreshed.', 'success');
		} catch (e) {
			pushMessage('Failed to refresh plan: ' + e.message, 'error');
		} finally {
			refreshPlanBtn.disabled = false;
		}
	});

	openChatsBtn.addEventListener('click', () => {
		// Navigate to chats page with event context (if chat implementation expects it)
		const target = `/chat.html?event_id=${encodeURIComponent(eventId)}`;
		window.location.href = target;
	});

	cancelRegBtn.addEventListener('click', () => {
		pushMessage('Cancellation feature not yet wired to backend endpoint.', 'warn');
	});

	await Promise.all([loadEvent(), loadProfileForPrefill(), loadPlan(), loadRegistration()]);
	maybeShowSoloForm();
	finalizeUI();
})();

