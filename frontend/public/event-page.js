// Event page script: loads event details and the user's plan, then populates the UI
// Requirements:
// - Read event id from query string (?id=...)
// - Fetch /events/{id} for event meta (after-party)
// - Fetch /api/my-plan for the user's sections (starter/main/dessert)
// - Update the three sections (titles, times, host/guest info)
// - Initialize Leaflet maps for appetizer and dessert approximate locations
// - Wire chat buttons to navigate to chat page with context

(function(){
	const qs = new URLSearchParams(window.location.search);
	const eventId = qs.get('id');

	const $ = (sel, root=document) => root.querySelector(sel);
	const $$ = (sel, root=document) => Array.from(root.querySelectorAll(sel));

	function normalizeMeal(meal){
		if (!meal) return null;
		const m = String(meal).toLowerCase();
		if (m.includes('entr') || m.includes('appet')) return 'starter';
		if (m.includes('plat') || m.includes('main')) return 'main';
		if (m.includes('dess')) return 'dessert';
		return m; // fallback
	}

	function setText(el, text){ if (el) el.textContent = text; }

	function findTimeSpan(section){
		// Look for the first <p> that contains "Time:" and return its <span>
		const ps = $$('p', section);
		for (const p of ps){
			if (/\btime\s*:/i.test(p.textContent)){
				return $('span', p) || p;
			}
		}
		return null;
	}

	function formatNameOrEmail(s){
		// For now we have emails only; display nicely
		if (!s) return '';
		const em = String(s).trim();
		return em;
	}

	function listToSentence(arr){
		const xs = (arr || []).map(formatNameOrEmail);
		if (xs.length <= 1) return xs.join('');
		return xs.slice(0, -1).join(', ') + ' & ' + xs[xs.length - 1];
	}

	function initLeafletCircle(divId, center, radiusM){
		try {
			if (!center || typeof L === 'undefined') return;
			const { lat, lon } = center;
			if (typeof lat !== 'number' || typeof lon !== 'number') return;
			const map = L.map(divId, { zoomControl: false, attributionControl: false });
			const tiles = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
			L.tileLayer(tiles, { maxZoom: 19 }).addTo(map);
			const c = L.latLng(lat, lon);
			const circle = L.circle(c, { radius: Math.max(200, Number(radiusM)||500), color: '#172a3a', fillColor: '#008080', fillOpacity: 0.2, weight: 1 });
			circle.addTo(map);
			map.setView(c, 14);
			// Ensure proper rendering when container becomes visible
			setTimeout(() => map.invalidateSize(), 0);
		} catch (e) {
			// ignore map errors
			console.warn('Map init failed:', e);
		}
	}

	function sectionNodes(){
		// Identify sections by existing anchors (map containers and ordering)
		const entreeMap = $('#map-entree');
		const dessertMap = $('#map-dessert');
		const allSections = $$('main section');
		// Fallback by order if maps not found
		const starterSec = entreeMap ? entreeMap.closest('section') : allSections[0];
		const dessertSec = dessertMap ? dessertMap.closest('section') : allSections[2];
		const mainSec = allSections[1];
		const closingSec = allSections[3];
		return { starterSec, mainSec, dessertSec, closingSec };
	}

	function populateCourseSection(sectionEl, opts){
		if (!sectionEl) return;
		const { courseLabel, isHost, time, hostEmail, guests, mapDivId, hostLocation } = opts;

		// Header: "Starter (You are Invited/Host)"
		const h2 = $('h2', sectionEl);
		if (h2){
			const statusTxt = isHost ? 'You are the Host' : 'You are Invited';
			setText(h2, `${courseLabel} (${statusTxt})`);
		}

		// Time
		const timeSpan = findTimeSpan(sectionEl);
		if (timeSpan) setText(timeSpan, time || '—');

		// Info lines container (first .mb-2 under section)
		const infoWrap = $('.mb-2', sectionEl);
		if (infoWrap){
			const guestSentence = listToSentence(guests || []);
			if (isHost){
				infoWrap.innerHTML = `
					<p class="text-sm">You are hosting ${courseLabel.toLowerCase()} for:</p>
					<p class="text-sm"><span class="font-semibold">${guestSentence || 'TBD'}</span>.</p>
				`;
			} else {
				infoWrap.innerHTML = `
					<p class="text-sm">You are invited to ${courseLabel.toLowerCase()} at <span class="font-semibold">${formatNameOrEmail(hostEmail) || 'TBD'}</span>'s place.</p>
					<p class="text-sm">Your co-guests: <span class="font-semibold">${guestSentence || 'TBD'}</span>.</p>
				`;
			}
		}

		// Location/map for appetizer and dessert
		if (mapDivId && hostLocation && hostLocation.center){
			const locP = $('div.flex-1 p.text-xs', sectionEl);
			if (locP){
				setText(locP, 'Location: Approximate area (see map)');
			}
			initLeafletCircle(mapDivId, hostLocation.center, hostLocation.approx_radius_m);
			// If invited and the design includes the yellow notice, keep it; otherwise do nothing
		}

		// Chat button navigation
		const btn = $('button', sectionEl);
		if (btn){
			const sectionKey = courseLabel.toLowerCase().includes('main') ? 'main' : (courseLabel.toLowerCase().includes('dessert') ? 'dessert' : 'starter');
			btn.addEventListener('click', (e) => {
				e.preventDefault();
				const url = new URL('chat.html', window.location.origin);
				url.searchParams.set('event_id', eventId || '');
				url.searchParams.set('section', sectionKey);
				window.location.href = url.toString();
			});
		}
	}

	async function loadData(){
		if (!eventId){
			console.error('Missing event id');
			return;
		}
		try {
			await (window.initCsrf ? window.initCsrf() : Promise.resolve());
		} catch {}

				// Prefer non-credentialed Bearer auth to avoid credentialed CORS when backend uses '*'
				const bearer = (window.auth && typeof window.auth.getCookie === 'function') ? window.auth.getCookie('dh_token') : null;
				const commonOpts = bearer ? { credentials: 'omit', headers: { 'Authorization': `Bearer ${bearer}` } } : {};
				// Fetch event and plan in parallel
			let ev = null, plan = null;
			try {
					const [evRes, planRes] = await Promise.all([
						window.apiFetch(`/events/${encodeURIComponent(eventId)}?anonymise=true`, commonOpts),
						window.apiFetch('/api/my-plan', commonOpts)
					]);
				ev = evRes.ok ? await evRes.json() : null;
				plan = planRes.ok ? await planRes.json() : null;
				if (!evRes.ok || !planRes.ok){
					throw new Error(`Failed to load data (${evRes.status}/${planRes.status})`);
				}
			} catch (networkErr){
				console.error('Failed to load event/plan:', networkErr);
				// Show a small inline notice to help debugging CORS/auth issues
				const notice = document.createElement('div');
				notice.className = 'mx-auto mt-6 max-w-3xl bg-red-50 border border-red-200 text-red-700 rounded-lg p-3 text-sm';
				notice.textContent = 'Unable to load event data. Please ensure you are logged in and that CORS is configured to allow http://'+ window.location.host +'.';
				const main = document.querySelector('main');
				if (main) main.prepend(notice);
				return;
			}

		const { starterSec, mainSec, dessertSec, closingSec } = sectionNodes();

		// Build a map of sections from plan
		let me = plan && plan.user_email;
		const byMeal = {};
		if (plan && Array.isArray(plan.sections)){
			for (const sec of plan.sections){
				const key = normalizeMeal(sec.meal);
				if (!key) continue;
				byMeal[key] = {
					meal: sec.meal,
					time: sec.time,
					hostEmail: sec.host_email,
					hostLocation: sec.host_location, // { center:{lat,lon}, approx_radius_m }
					guests: sec.guests || [],
				};
			}
		}

		// Starter (Appetizer)
		if (starterSec){
			const sec = byMeal['starter'] || null;
			const isHost = !!(sec && me && sec.hostEmail && me.toLowerCase() === String(sec.hostEmail).toLowerCase());
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

		// Main Course
		if (mainSec){
			const sec = byMeal['main'] || null;
			const isHost = !!(sec && me && sec.hostEmail && me.toLowerCase() === String(sec.hostEmail).toLowerCase());
			populateCourseSection(mainSec, {
				courseLabel: 'Main Course',
				isHost,
				time: sec ? sec.time : null,
				hostEmail: sec ? sec.hostEmail : null,
				guests: sec ? sec.guests : [],
				mapDivId: null,
				hostLocation: null,
			});
			// Hide the orange attention banner if we lack dietary info
			const warn = $(".bg-[#ffe5d0]", mainSec);
			if (warn) warn.style.display = 'none';
		}

		// Dessert
		if (dessertSec){
			const sec = byMeal['dessert'] || null;
			const isHost = !!(sec && me && sec.hostEmail && me.toLowerCase() === String(sec.hostEmail).toLowerCase());
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

		// Closing party location from event
		if (closingSec && ev){
			const whereP = $('div.mb-2 p.text-sm span.font-semibold', closingSec);
			const helperP = $('div.mb-2 p.text-xs', closingSec);
			let closingText = null;
			if (ev.after_party_location){
				const apl = ev.after_party_location;
				if (apl.address_public){
					closingText = apl.address_public;
					if (helperP) setText(helperP, 'The place is publicly shown here.');
				} else if (apl.center){
					closingText = 'After-party area (approximate)';
					if (helperP) setText(helperP, 'Approximate area only; exact address will be shared later.');
				}
			}
			if (whereP && closingText){ setText(whereP, closingText); }
		}

		// Update page title if we know the event title
		if (ev && ev.title){
			document.title = `Dinnerhopping - ${ev.title}`;
		}
	}

	// Kick off after DOM is ready (defer script means DOMContentLoaded likely fired already, but safe to guard)
	if (document.readyState === 'loading'){
		document.addEventListener('DOMContentLoaded', loadData);
	} else {
		loadData();
	}
})();

