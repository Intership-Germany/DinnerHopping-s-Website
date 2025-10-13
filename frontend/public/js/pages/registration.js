/**
 * Registration listing page (formerly registration.js)
 * Shows active events via /registrations/events/active and provides
 * quick solo/team registration shortcuts.
 */
(function () {
  const BASE = window.BACKEND_BASE_URL;
  function el(tag, attrs, ...children) {
    const n = document.createElement(tag);
    if (attrs) {
      Object.entries(attrs).forEach(([k, v]) => {
        if (k === 'class') n.className = v;
        else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
        else n.setAttribute(k, v);
      });
    }
    children.flat().forEach((ch) => {
      if (ch == null) return;
      n.appendChild(typeof ch === 'string' ? document.createTextNode(ch) : ch);
    });
    return n;
  }
  async function fetchActiveEvents() {
    const api =
      window.dh && window.dh.apiFetch
        ? window.dh.apiFetch
        : (p, opts) => fetch(BASE + p, { ...(opts || {}), credentials: 'include' });
    const res = await api('/registrations/events/active', { method: 'GET' });
    if (!res.ok) throw new Error('Failed to load events');
    return await res.json();
  }

  async function fetchUserRegistrations() {
    const api =
      window.dh && window.dh.apiFetch
        ? window.dh.apiFetch
        : (p, opts) => fetch(BASE + p, { ...(opts || {}), credentials: 'include' });
    try {
      const res = await api('/registrations/registration-status', { method: 'GET' });
      if (!res.ok) return null;
      const data = await res.json();
      return data?.registrations || [];
    } catch {
      return null;
    }
  }

  async function fetchUserInvitations() {
    const api =
      window.dh && window.dh.apiFetch
        ? window.dh.apiFetch
        : (p, opts) => fetch(BASE + p, { ...(opts || {}), credentials: 'include' });
    try {
      // Try with and without trailing slash to be tolerant
      const paths = ['/invitations', '/invitations/'];
      for (const p of paths) {
        try {
          const res = await api(p, { method: 'GET' });
          if (!res.ok) continue;
          const data = await res.json().catch(() => ({}));
          // The backend may return an array or { invitations: [...] }
          const arr = Array.isArray(data) ? data : Array.isArray(data?.invitations) ? data.invitations : null;
          if (Array.isArray(arr)) {
            if (arr.length) console.info('registration: found invitations', arr.length);
            return arr;
          }
        } catch (e) { /* try next path */ }
      }
      return [];
    } catch (e) {
      console.warn('registration: failed to fetch invitations', e);
      return [];
    }
  }

  function hasActiveRegistration(registrations) {
    if (!Array.isArray(registrations)) return false;
    const cancelledStatuses = ['cancelled_by_user', 'cancelled_admin'];
    return registrations.some(reg => reg.status && !cancelledStatuses.includes(reg.status));
  }
  async function startSolo(eventId) {
    try {
      const api =
        window.dh && window.dh.apiFetch
          ? window.dh.apiFetch
          : (p, opts) => fetch(BASE + p, { ...(opts || {}), credentials: 'include' });
      const res = await api('/registrations/solo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event_id: eventId }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // Handle 409 conflict for existing active registration (detail may be object)
        const detail = data && typeof data.detail === 'object' ? data.detail : data;
        if (res.status === 409 && detail && detail.existing_registration) {
          const existing = detail.existing_registration;
          alert(
            `${detail.message || 'You already have an active registration.'}\n\n` +
              `Event: ${existing.event_title || 'Unknown'}\n` +
              `Status: ${existing.status || 'Unknown'}\n\n` +
              `Please cancel that registration first, or wait until it completes.`
          );
          return;
        }
        const msg = typeof data.detail === 'string' ? data.detail : (detail && (detail.message || detail.detail)) || data.message;
        alert(msg || 'Failed to register');
        return;
      }
      // Determine provider(s)
  let providers = ['paypal', 'stripe'];
      let defaultProvider = 'paypal';
      try {
        const pr = await api('/payments/providers', {
          method: 'GET',
          headers: { Accept: 'application/json' },
        });
        if (pr.ok) {
          const provs = await pr.json();
          if (provs?.providers) providers = provs.providers;
          else if (Array.isArray(provs)) providers = provs;
          if (typeof provs?.default === 'string') defaultProvider = provs.default;
        }
      } catch {}
      if (!Array.isArray(providers)) providers = [];
      providers = providers
        .map((p) => (typeof p === 'string' ? p.toLowerCase() : ''))
        .filter((p) => p && ['paypal', 'stripe'].includes(p));
      if (!providers.includes(defaultProvider) && providers.length) defaultProvider = providers[0];
      if (!providers.length) {
        alert('Online payments are currently unavailable. Please contact support to complete your registration.');
        return;
      }
      const chosen = providers.length === 1 ? providers[0] : defaultProvider;
      // Prefer backend-advertised endpoint if present
      const payCreatePath = data.payment_create_endpoint || '/payments/create';
      const payRes = await api(payCreatePath, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ registration_id: data.registration_id, provider: chosen }),
      });
      const pay = await payRes.json().catch(() => ({}));
      if (pay.status === 'no_payment_required') {
        alert('No payment required.');
        return;
      }
      let link = null;
      if (pay.next_action) {
        if (pay.next_action.type === 'redirect') link = pay.next_action.url;
        else if (pay.next_action.type === 'paypal_order') link = pay.next_action.approval_link;
      }
      if (!link) link = pay.payment_link;
      if (link)
        window.location.href = link.startsWith('http') ? link : window.BACKEND_BASE_URL + link;
      else alert('Payment created. Please follow provider instructions.');
    } catch (e) {
      alert('Registration failed.');
    }
  }
  async function startTeam(eventId) {
    try {
      const api =
        window.dh && window.dh.apiFetch
          ? window.dh.apiFetch
          : (p, opts) => fetch(BASE + p, { ...(opts || {}), credentials: 'include' });
      
      // Load user profile to get kitchen/course info
      let userProfile = null;
      try {
        const profileRes = await api('/users/me', { method: 'GET' });
        if (profileRes.ok) {
          userProfile = await profileRes.json();
        }
      } catch (e) {
        console.warn('Failed to load user profile:', e);
      }

      // Collect minimal required info for backend: exactly one of partner_existing or partner_external
      let mode = (prompt('Team registration: type "existing" to invite a registered user by email, or "external" for a partner without account.\nLeave empty to cancel.') || '').trim().toLowerCase();
      if (!mode) return;
      if (mode !== 'existing' && mode !== 'external') {
        alert('Invalid choice. Please type existing or external.');
        return;
      }
      
      let payload = { 
        event_id: eventId, 
        cooking_location: 'creator',
        dietary_preference: userProfile?.default_dietary_preference || null,
        kitchen_available: userProfile?.kitchen_available || false,
        main_course_possible: userProfile?.main_course_possible || false,
        course_preference: null
      };
      
      if (mode === 'existing') {
        const email = (prompt('Enter partner email (existing user):') || '').trim();
        if (!email) {
          alert('Email required.');
          return;
        }
        
        // Try to fetch partner info
        try {
          const partnerRes = await api(`/registrations/search-user?email=${encodeURIComponent(email)}`, { method: 'GET' });
          if (partnerRes.ok) {
            const partner = await partnerRes.json();
            
            // Ask who will host
            const hostChoice = prompt(
              `Partner found: ${partner.full_name || email}\n\n` +
              `Who will host the cooking?\n` +
              `Type "me" for your kitchen or "partner" for their kitchen:\n\n` +
              `Your kitchen: ${payload.kitchen_available ? 'Available' : 'Not available'}\n` +
              `Partner kitchen: ${partner.kitchen_available ? 'Available' : 'Not available'}`
            );
            
            if (!hostChoice) return;
            
            const normalizedHost = hostChoice.trim().toLowerCase();
            if (normalizedHost === 'me') {
              payload.cooking_location = 'creator';
              if (!payload.kitchen_available) {
                alert('You indicated your kitchen is not available. Please update your profile or choose partner as host.');
                return;
              }
            } else if (normalizedHost === 'partner') {
              payload.cooking_location = 'partner';
              if (!partner.kitchen_available) {
                alert('Partner kitchen is not available. Please choose a different location.');
                return;
              }
            } else {
              alert('Invalid choice. Please type "me" or "partner".');
              return;
            }
            
            // Ask for course preference
            const courseChoice = prompt(
              'Which course would your team prefer to cook?\n' +
              'Type: appetizer, main, or dessert\n' +
              (payload.cooking_location === 'creator' && !payload.main_course_possible ? 
                '\nNote: Main course not possible at your location.' : 
                payload.cooking_location === 'partner' && !partner.main_course_possible ?
                '\nNote: Main course not possible at partner location.' : '')
            );
            
            if (courseChoice) {
              const normalizedCourse = courseChoice.trim().toLowerCase();
              if (['appetizer', 'main', 'dessert'].includes(normalizedCourse)) {
                payload.course_preference = normalizedCourse;
                
                // Validate main course choice
                if (normalizedCourse === 'main') {
                  if (payload.cooking_location === 'creator' && !payload.main_course_possible) {
                    alert('Main course not possible at your location.');
                    return;
                  }
                  if (payload.cooking_location === 'partner' && !partner.main_course_possible) {
                    alert('Main course not possible at partner location.');
                    return;
                  }
                }
              }
            }
          }
        } catch (e) {
          console.warn('Could not fetch partner details:', e);
        }
        
        payload.partner_existing = { email };
      } else {
        const name = (prompt('Enter partner name:') || '').trim();
        const email = (prompt('Enter partner email:') || '').trim();
        if (!name || !email) {
          alert('Name and email required for external partner.');
          return;
        }
        
        const gender = prompt('Partner gender (optional - female/male/diverse/prefer_not_to_say):') || null;
        const diet = prompt('Partner dietary preference (optional - vegan/vegetarian/omnivore):') || null;
        const fieldOfStudy = prompt('Partner field of study (optional):') || null;
        const hasKitchen = confirm('Does partner have a kitchen available?');
        const canCookMain = hasKitchen ? confirm('Can partner location host main course?') : false;
        
        payload.partner_external = { 
          name, 
          email,
          gender: gender || null,
          dietary_preference: diet || null,
          field_of_study: fieldOfStudy || null,
          kitchen_available: hasKitchen,
          main_course_possible: canCookMain
        };
        
        // Ask who will host
        let hostChoice = null;
        if (payload.kitchen_available || hasKitchen) {
          hostChoice = prompt(
            'Who will host the cooking?\n' +
            'Type "me" for your kitchen or "partner" for their kitchen:\n\n' +
            `Your kitchen: ${payload.kitchen_available ? 'Available' : 'Not available'}\n` +
            `Partner kitchen: ${hasKitchen ? 'Available' : 'Not available'}`
          );
        } else {
          alert('Neither you nor your partner has a kitchen available. At least one kitchen is required.');
          return;
        }
        
        if (!hostChoice) return;
        
        const normalizedHost = hostChoice.trim().toLowerCase();
        if (normalizedHost === 'me') {
          payload.cooking_location = 'creator';
          if (!payload.kitchen_available) {
            alert('Your kitchen is not available.');
            return;
          }
        } else if (normalizedHost === 'partner') {
          payload.cooking_location = 'partner';
          if (!hasKitchen) {
            alert('Partner kitchen is not available.');
            return;
          }
        } else {
          alert('Invalid choice. Please type "me" or "partner".');
          return;
        }
        
        // Ask for course preference
        const courseChoice = prompt(
          'Which course would your team prefer to cook?\n' +
          'Type: appetizer, main, or dessert\n' +
          (payload.cooking_location === 'creator' && !payload.main_course_possible ? 
            '\nNote: Main course not possible at your location.' : 
            payload.cooking_location === 'partner' && !canCookMain ?
            '\nNote: Main course not possible at partner location.' : '')
        );
        
        if (courseChoice) {
          const normalizedCourse = courseChoice.trim().toLowerCase();
          if (['appetizer', 'main', 'dessert'].includes(normalizedCourse)) {
            payload.course_preference = normalizedCourse;
            
            // Validate main course choice
            if (normalizedCourse === 'main') {
              if (payload.cooking_location === 'creator' && !payload.main_course_possible) {
                alert('Main course not possible at your location.');
                return;
              }
              if (payload.cooking_location === 'partner' && !canCookMain) {
                alert('Main course not possible at partner location.');
                return;
              }
            }
          }
        }
      }
      
      const res = await api('/registrations/team', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // Handle 409 conflict for existing active registration (detail may be object)
        const detail = data && typeof data.detail === 'object' ? data.detail : data;
        if (res.status === 409 && detail && detail.existing_registration) {
          const existing = detail.existing_registration;
          alert(
            `${detail.message || 'You already have an active registration.'}\n\n` +
              `Event: ${existing.event_title || 'Unknown'}\n` +
              `Status: ${existing.status || 'Unknown'}\n\n` +
              `Please cancel that registration first, or wait until it completes.`
          );
          return;
        }
        const msg = typeof data.detail === 'string' ? data.detail : (detail && (detail.message || detail.detail)) || data.message;
        alert(msg || 'Failed to register team');
        return;
      }
      let providers = ['paypal', 'stripe'];
      let defaultProvider = 'paypal';
      try {
        const pr = await api('/payments/providers', {
          method: 'GET',
          headers: { Accept: 'application/json' },
        });
        if (pr.ok) {
          const provs = await pr.json();
          if (provs?.providers) providers = provs.providers;
          else if (Array.isArray(provs)) providers = provs;
          if (typeof provs?.default === 'string') defaultProvider = provs.default;
        }
      } catch {}
      if (!Array.isArray(providers)) providers = [];
      providers = providers
        .map((p) => (typeof p === 'string' ? p.toLowerCase() : ''))
        .filter((p) => p && ['paypal', 'stripe'].includes(p));
      if (!providers.includes(defaultProvider) && providers.length) defaultProvider = providers[0];
      if (!providers.length) {
        alert('Online payments are currently unavailable. Please contact support to finalize the team registration.');
        return;
      }
      const chosen = providers.length === 1 ? providers[0] : defaultProvider;
      const payCreatePath = data.payment_create_endpoint || '/payments/create';
      const payRes = await api(payCreatePath, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ registration_id: data.registration_id, provider: chosen }),
      });
      const pay = await payRes.json().catch(() => ({}));
      if (pay.status === 'no_payment_required') {
        alert('Team created. No payment required.');
        return;
      }
      let link = null;
      if (pay.next_action) {
        if (pay.next_action.type === 'redirect') link = pay.next_action.url;
        else if (pay.next_action.type === 'paypal_order') link = pay.next_action.approval_link;
      }
      if (!link) link = pay.payment_link;
      if (link)
        window.location.href = link.startsWith('http') ? link : window.BACKEND_BASE_URL + link;
      else alert('Payment created. Please follow provider instructions.');
    } catch (e) {
      alert('Team registration failed.');
    }
  }
  async function init() {
    const list = document.getElementById('events-list');
    if (!list) return;
    try {
      // Fetch events, user registrations and invitations
      const [events, userRegistrations, userInvitations] = await Promise.all([
        fetchActiveEvents(),
        fetchUserRegistrations(),
        fetchUserInvitations()
      ]);

      const hasActiveReg = hasActiveRegistration(userRegistrations);
      // Consider any invitation that is not in a terminal state as a blocking pending invitation.
      const isTerminal = (s) => {
        if (!s) return false;
        const t = s.toLowerCase();
        return ['accepted', 'revoked', 'expired', 'cancelled', 'declined'].includes(t);
      };
      const hasPendingInvitation = Array.isArray(userInvitations) && userInvitations.some(inv => !isTerminal(inv.status));
      const hasActive = hasActiveReg || hasPendingInvitation;
      // Debug: log counts so we can inspect in browser console when troubleshooting
      try { console.debug('registration:init', { regs: (userRegistrations || []).length, invitations: (userInvitations || []).length, hasActiveReg, hasPendingInvitation, hasActive }); } catch(e) {}
      
      if (hasActive && Array.isArray(userRegistrations)) {
        // User has active registration - show warning message
        const activeReg = userRegistrations.find(reg => {
          const cancelledStatuses = ['cancelled_by_user', 'cancelled_admin'];
          return reg.status && !cancelledStatuses.includes(reg.status);
        });
        
        const warningDiv = el(
          'div',
          { class: 'p-4 mb-4 border-l-4 border-yellow-500 bg-yellow-50 rounded' },
          el('div', { class: 'flex items-start' },
            el('div', { class: 'ml-3' },
              el('h3', { class: 'text-sm font-medium text-yellow-800' }, 'Active Registration Found'),
              el('div', { class: 'mt-2 text-sm text-yellow-700' },
                `You already have an active registration for "${activeReg?.event_title || 'an event'}". ` +
                `You must cancel your current registration before registering for another event. ` +
                `Please visit your profile or registrations page to cancel.`
              )
            )
          )
        );
        list.appendChild(warningDiv);
      }
      // If user is blocked because of a pending invitation (no active regs), show a different message
      if (!hasActiveReg && hasPendingInvitation) {
        const warningInv = el(
          'div',
          { class: 'p-4 mb-4 border-l-4 border-blue-500 bg-blue-50 rounded' },
          el('div', { class: 'flex items-start' },
            el('div', { class: 'ml-3' },
              el('h3', { class: 'text-sm font-medium text-blue-800' }, 'Pending Invitation'),
              el('div', { class: 'mt-2 text-sm text-blue-700' },
                'You have a pending invitation for an event. You cannot register for another event until you accept or decline that invitation. Please visit My registrations to manage your invitations.'
              )
            )
          )
        );
        list.appendChild(warningInv);
      }

      if (!events.length) {
        list.appendChild(el('p', { class: 'text-gray-600' }, 'No active events right now.'));
        return;
      }
      
      events.forEach((ev) => {
        const soloBtn = el(
          'button',
          {
            class: hasActive 
              ? 'px-3 py-1 bg-gray-400 text-white rounded cursor-not-allowed' 
              : 'px-3 py-1 bg-emerald-600 text-white rounded hover:bg-emerald-700',
            onclick: hasActive ? () => {
              alert('You already have an active registration. Please cancel it first before registering for another event.');
            } : () => startSolo(ev.id),
            disabled: hasActive,
            title: hasActive ? 'You must cancel your active registration first' : 'Register as solo participant'
          },
          'Register Solo'
        );

        const teamBtn = el(
          'button',
          {
            class: hasActive 
              ? 'px-3 py-1 bg-gray-400 text-white rounded cursor-not-allowed' 
              : 'px-3 py-1 bg-indigo-600 text-white rounded hover:bg-indigo-700',
            onclick: hasActive ? () => {
              alert('You already have an active registration. Please cancel it first before registering for another event.');
            } : () => startTeam(ev.id),
            disabled: hasActive,
            title: hasActive ? 'You must cancel your active registration first' : 'Register as a team'
          },
          'Register Team'
        );

        const row = el(
          'div',
          { class: 'p-3 border rounded mb-2 flex items-center justify-between' },
          el(
            'div',
            null,
            el('div', { class: 'font-semibold' }, ev.title || 'Event'),
            el('div', { class: 'text-xs text-gray-500' }, ev.date || ev.start_at || '')
          ),
          el('div', { class: 'space-x-2' }, soloBtn, teamBtn)
        );
        list.appendChild(row);
      });
    } catch {
      list.appendChild(el('p', { class: 'text-red-600' }, 'Failed to load events.'));
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
  window.dh = window.dh || {};
  window.dh.pages = window.dh.pages || {};
  window.dh.pages.registration = { startSolo, startTeam };
})();
