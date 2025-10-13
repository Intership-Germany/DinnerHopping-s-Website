/* Invitation page script
   - Supports two entry flows:
     1) ?token=... (email link) - will call backend /invitations/{token} or /invitations/{token}/accept
     2) ?state=... (temporary login state) - used after login redirect; calls /invitations/by-state/{state}
   - Renders invitation metadata and wires Accept / Decline actions.
*/
(function () {
  const api = (window.dh && window.dh.apiFetch) || window.apiFetch || fetch;
  const BASE = window.BACKEND_BASE_URL || '';

  function qs() {
    return new URLSearchParams(window.location.search);
  }

  function el(id) { return document.getElementById(id); }

  function showLoading() {
    el('loading').classList.remove('hidden');
    el('content').classList.add('hidden');
    el('error').classList.add('hidden');
  }
  function showContent() {
    el('loading').classList.add('hidden');
    el('content').classList.remove('hidden');
    el('error').classList.add('hidden');
  }
  function showError(msg) {
    el('loading').classList.add('hidden');
    el('content').classList.add('hidden');
    el('error').classList.remove('hidden');
    const p = el('error').querySelector('p');
    if (p) p.textContent = msg || 'This invitation link is invalid or has expired.';
  }

  function openAuthModal() {
    const tpl = document.getElementById('tpl-invitation-auth-modal');
    if (!tpl) return null;
    const node = tpl.content.cloneNode(true);
    const overlay = node.querySelector('div');
    // wire links
    const reg = node.querySelector('[data-role="register-link"]');
    const log = node.querySelector('[data-role="login-link"]');
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    if (reg) reg.setAttribute('href', '/register.html?next=' + next);
    if (log) log.setAttribute('href', '/login.html?next=' + next);
    document.body.appendChild(overlay);
    overlay.querySelectorAll('.modal-close').forEach(b => b.addEventListener('click', ()=> overlay.remove()));
    overlay.addEventListener('click', (e)=> { if (e.target === overlay) overlay.remove(); });
    return overlay;
  }

  function formatDate(d) {
    try { return new Date(d).toLocaleString(); } catch { return d || '' }
  }

  async function fetchInvitationByToken(token) {
    const path = `/invitations/${encodeURIComponent(token)}`;
    const res = await api(path, { method: 'GET', credentials: 'include', headers: { Accept: 'application/json' } });
    // If backend redirected (to login page) follow the redirect in the browser
    if (res.redirected && res.url) {
      // let the browser handle the redirect (likely to login)
    const redirectUrl = res.url || '';
    try {
      const u = new URL(redirectUrl, window.location.href);
        const next = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = '/login.html?next=' + next;
    } catch (err) {
      // Fallback: navigate to the redirect URL or frontend login
      window.location.href = redirectUrl || '/login.html';
    }
    throw new Error('Redirecting to login');
    }
    if (!res.ok) throw new Error('Invitation not found');
    const ct = String(res.headers.get('content-type') || '');
    if (!/json/i.test(ct)) {
      // Non-JSON: probably HTML (login page) - navigate the browser to the fetched URL
      try { window.location.href = res.url || '/login.html'; } catch {};
      throw new Error('Redirecting to login');
    }
    return await res.json().catch(() => ({}));
  }

  async function fetchInvitationByState(state) {
    const path = `/invitations/by-state/${encodeURIComponent(state)}`;
    const res = await api(path, { method: 'GET', credentials: 'include', headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('Invitation state invalid or expired');
    return await res.json().catch(() => ({}));
  }

  function renderInvitation(inv) {
    el('title').textContent = inv.event_title || 'Invitation';
    el('subtitle').textContent = inv.invited_email ? `Invitation for ${inv.invited_email}` : 'Invitation details';
    const details = el('invitationDetails');
    details.innerHTML = '';
    const list = document.createElement('div');
    list.className = 'space-y-2';
    if (inv.event_title) list.appendChild(createRow('Event', inv.event_title));
    if (inv.event_date) list.appendChild(createRow('Event date', formatDate(inv.event_date)));
    if (inv.invited_email) list.appendChild(createRow('Invited email', inv.invited_email));
    if (inv.created_by) list.appendChild(createRow('Invited by', inv.created_by));
    if (inv.expires_at) list.appendChild(createRow('Expires at', formatDate(inv.expires_at)));
    details.appendChild(list);

    const actions = el('actions');
    actions.innerHTML = '';
    const accept = document.createElement('button');
    accept.className = 'px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700';
    accept.textContent = 'Accept Invitation';
    const decline = document.createElement('button');
    decline.className = 'px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700';
    decline.textContent = 'Decline Invitation';
    actions.appendChild(accept);
    actions.appendChild(decline);

    accept.addEventListener('click', async () => {
      accept.disabled = true;
      try {
        const token = qs().get('token');
        if (token) {
          // Use GET accept endpoint so the backend can redirect us to success page or create account flow
          // navigate the browser to the accept URL directly so cookies and redirects work as expected
          window.location.href = (window.BACKEND_BASE_URL || '') + `/invitations/${encodeURIComponent(token)}/accept`;
          return;
        }

        // If no token present, try to accept by id/state via authenticated POST
        if (inv && inv.id) {
          const path = (BASE || '') + `/invitations/by-id/${encodeURIComponent(inv.id)}/accept`;
          const res = await api(path, { method: 'POST', credentials: 'include', headers: { Accept: 'application/json' } });

          // If backend returned a redirect (e.g. to login) follow it in the browser
          if (res && res.redirected && res.url) {
            window.location.href = res.url;
            return;
          }

          // If not authenticated/authorized, go to frontend login with next param
          if (res && (res.status === 401 || res.status === 403)) {
            const next = encodeURIComponent(window.location.pathname + window.location.search);
            window.location.href = '/login.html?next=' + next;
            return;
          }

          if (!res || !res.ok) {
            const data = await res.json().catch(()=>({}));
            showMessage((data.detail||data.message) || `Failed to accept (${res.status})`, 'error');
            accept.disabled = false;
            return;
          }
          // success -> backend returns JSON with registration info; redirect to accepted landing
          const data = await res.json().catch(()=>null);
          if (data && data.registration_id) {
            // If payment required, redirect to accepted which may point to payment flow
            window.location.href = '/invitations-accepted.html';
            return;
          }
          window.location.href = '/invitations-accepted.html';
          return;
        }

        // fallback: prompt auth
        openAuthModal();
      } catch (e) {
        openAuthModal();
        accept.disabled = false;
      }
    });

    decline.addEventListener('click', async () => {
      if (!confirm('Are you sure you want to decline this invitation?')) return;
      decline.disabled = true;
      try {
        const token = qs().get('token');
        let path = '';
        let opts = { method: 'POST', credentials: 'include', headers: { Accept: 'application/json' } };
        if (token) path = `/invitations/${encodeURIComponent(token)}/decline`;
        else if (inv.id) path = `/invitations/by-id/${encodeURIComponent(inv.id)}/decline`;
        else {
          showMessage('Cannot decline this invitation', 'error');
          decline.disabled = false;
          return;
        }
        const res = await api(path, opts);
        if (!res.ok) {
          const data = await res.json().catch(()=>({}));
          showMessage((data.detail||data.message) || `Failed to decline (${res.status})`, 'error');
          decline.disabled = false;
          return;
        }
        showMessage('Invitation declined', 'success');
        actions.classList.add('hidden');
        setTimeout(()=>{ window.location.href = '/home.html'; }, 2000);
      } catch (e) {
        showMessage('Network error while declining', 'error');
        decline.disabled = false;
      }
    });
  }

  function createRow(label, value) {
    const d = document.createElement('div');
    d.className = 'flex justify-between gap-4';
    const l = document.createElement('div'); l.className = 'text-sm text-gray-600'; l.textContent = label;
    const v = document.createElement('div'); v.className = 'text-sm text-gray-900 font-medium'; v.textContent = value || '';
    d.appendChild(l); d.appendChild(v);
    return d;
  }

  function showMessage(text, type='info'){
    const m = el('message');
    if(!m) return;
    m.classList.remove('hidden');
    m.textContent = text || '';
    m.style.color = type === 'error' ? '#b91c1c' : '#065f46';
    m.style.background = type === 'error' ? '#fee2e2' : '#ecfdf5';
  }

  // Init
  (async function init(){
    showLoading();
    const params = qs();
    const token = params.get('token');
    const state = params.get('state');
    try {
      let inv = null;
      if (state) {
        inv = await fetchInvitationByState(state);
      } else if (token) {
        // Try to fetch metadata; server returns JSON when authenticated or metadata when not
        try {
          inv = await fetchInvitationByToken(token);
        } catch (e) {
          // If the server responded with a Redirect to login, the static file may handle it. Show error.
          showError(e.message);
          return;
        }
      } else {
        showError('No invitation token or state provided.');
        return;
      }
      renderInvitation(inv || {});
      showContent();
    } catch (e) {
      showError(e.message || 'Unable to load invitation');
    }
  })();

})();
