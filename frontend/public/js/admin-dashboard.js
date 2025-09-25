(function(){
  const $ = (sel, root)=> (root||document).querySelector(sel);
  const $$ = (sel, root)=> Array.from((root||document).querySelectorAll(sel));
  const fmtDate = (s)=> s ? new Date(s).toLocaleString() : '';

  // --- Edit mode state ---
  let editingId = null;

  // --- Matching details state ---
  let detailsVersion = null; // number
  let detailsGroups = [];    // [{phase, host_team_id, guest_team_ids, score?, travel_seconds?}]
  let teamDetails = {};      // { team_id: {size, team_diet, course_preference, can_host_main, lat, lon} }
  let unsaved = false;

  async function ensureCsrf(){ try{ await (window.initCsrf && window.initCsrf()); } catch(e){} }

  // Helpers to format values for inputs
  function toDateInputValue(v){
    if (!v) return '';
    if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v)) return v;
    const d = new Date(v);
    if (isNaN(d.getTime())) return '';
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth()+1).padStart(2,'0');
    const dd = String(d.getDate()).padStart(2,'0');
    return `${yyyy}-${mm}-${dd}`;
  }
  function toDateTimeLocalInputValue(v){
    if (!v) return '';
    if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(v)) return v;
    const d = new Date(v);
    if (isNaN(d.getTime())) return '';
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth()+1).padStart(2,'0');
    const dd = String(d.getDate()).padStart(2,'0');
    const hh = String(d.getHours()).padStart(2,'0');
    const mi = String(d.getMinutes()).padStart(2,'0');
    return `${yyyy}-${mm}-${dd}T${hh}:${mi}`;
  }

  function setForm(ev){
    const f = $('#create-event-form');
    f.title.value = ev.title || '';
    f.city.value = ev.city || '';
    f.date.value = toDateInputValue(ev.date);
    f.start_at.value = toDateTimeLocalInputValue(ev.start_at);
    f.registration_deadline.value = toDateTimeLocalInputValue(ev.registration_deadline);
    if (f.payment_deadline) f.payment_deadline.value = toDateTimeLocalInputValue(ev.payment_deadline);
    f.capacity.value = ev.capacity != null ? String(ev.capacity) : '';
    f.fee_cents.value = ev.fee_cents != null ? String(ev.fee_cents) : '';
    f.valid_zip_codes.value = Array.isArray(ev.valid_zip_codes) ? ev.valid_zip_codes.join(', ') : '';
    f.after_party_address.value = (ev.after_party_location && ev.after_party_location.address_public) ? ev.after_party_location.address_public : '';
    f.extra_info.value = ev.extra_info || '';
    f.refund_on_cancellation.checked = !!ev.refund_on_cancellation;
    f.chat_enabled.checked = !!ev.chat_enabled;
  }

  function readForm(){
    const f = $('#create-event-form');
    const fd = new FormData(f);
    const payload = {
      title: fd.get('title'),
      date: fd.get('date') || null,
      start_at: fd.get('start_at') || null,
      city: fd.get('city') || null,
      capacity: fd.get('capacity') ? Number(fd.get('capacity')) : null,
      fee_cents: fd.get('fee_cents') ? Number(fd.get('fee_cents')) : 0,
      registration_deadline: fd.get('registration_deadline') || null,
      payment_deadline: fd.get('payment_deadline') || null,
      extra_info: fd.get('extra_info') || null,
      refund_on_cancellation: fd.get('refund_on_cancellation') ? true : false,
      chat_enabled: fd.get('chat_enabled') ? true : false,
      valid_zip_codes: (fd.get('valid_zip_codes')||'').split(',').map(s=>s.trim()).filter(Boolean),
    };
    const addr = fd.get('after_party_address');
    if (addr) payload.after_party_location = { address: addr };
    return payload;
  }

  function enterCreateMode(){
    editingId = null;
    $('#create-form-title').textContent = 'Create New Event';
    const btn = $('#btn-submit-event');
    btn.textContent = 'Create Event (Draft)';
    $('#btn-cancel-edit').classList.add('hidden');
    $('#create-event-form').reset();
  }

  function enterEditMode(ev){
    editingId = ev.id;
    $('#create-form-title').textContent = 'Edit Event';
    const btn = $('#btn-submit-event');
    btn.textContent = 'Update Event';
    $('#btn-cancel-edit').classList.remove('hidden');
    setForm(ev);
    $('#create-event-form').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // ----- Matching Details rendering & DnD -----
  function renderTeamCard(tid){
    const det = teamDetails[tid] || {};
    const pref = det.course_preference ? `pref: ${det.course_preference}` : '';
    const diet = det.team_diet ? `diet: ${det.team_diet}` : '';
    const canMain = det.can_host_main ? 'main✔' : '';
    const names = Array.isArray(det.members) && det.members.length
      ? det.members.map(m=> (m.display_name || [m.first_name, m.last_name].filter(Boolean).join(' ') || m.email)).join(', ')
      : null;
    const header = names ? names : tid;
    const meta = [pref, diet, canMain].filter(Boolean).join(' · ');
    const el = document.createElement('div');
    el.className = 'team-card border rounded-lg p-2 text-xs bg-white cursor-move shadow-sm';
    el.draggable = true;
    el.dataset.teamId = tid;
    el.innerHTML = `<div class="font-semibold text-sm">${header}</div>${meta?`<div class=\"text-[#4a5568]\">${meta}</div>`:''}`;
    return el;
  }

  function groupsByPhase(){
    const by = { appetizer: [], main: [], dessert: [] };
    detailsGroups.forEach((g, idx)=>{ by[g.phase] = by[g.phase] || []; by[g.phase].push({ ...g, _idx: idx }); });
    return by;
  }

  function renderMatchDetailsBoard(){
    const box = $('#match-details');
    const msg = $('#match-details-msg');
    if (!detailsVersion){ box.innerHTML = ''; msg.textContent = 'No proposal loaded yet.'; return; }
    msg.textContent = unsaved ? 'You have unsaved changes. Metrics shown may be stale.' : '';
    const by = groupsByPhase();
    const phases = ['appetizer','main','dessert'];
    box.innerHTML = '';
    phases.forEach(phase=>{
      const section = document.createElement('div');
      section.innerHTML = `<div class="font-semibold mb-2 capitalize">${phase}</div>`;
      const wrap = document.createElement('div'); wrap.className = 'grid grid-cols-1 md:grid-cols-3 gap-3';
      (by[phase]||[]).forEach((g, localIdx)=>{
        const card = document.createElement('div');
        card.className = 'p-3 rounded-xl border border-[#f0f4f7] bg-[#fcfcfd]';
        // host
        const hostZone = document.createElement('div'); hostZone.className = 'host-zone mb-2 p-2 rounded border border-dashed';
        hostZone.dataset.phase = phase; hostZone.dataset.groupIdx = String(g._idx); hostZone.dataset.role = 'host';
        hostZone.innerHTML = '<div class="text-xs text-[#4a5568] mb-1">Host</div>';
        const hostCard = g.host_team_id ? renderTeamCard(String(g.host_team_id)) : null;
        if (hostCard){ hostCard.dataset.phase = phase; hostCard.dataset.groupIdx = String(g._idx); hostCard.dataset.role = 'host'; hostZone.appendChild(hostCard); }
        card.appendChild(hostZone);
        // guests
        const guestZone = document.createElement('div'); guestZone.className = 'guest-zone p-2 rounded border border-dashed min-h-10';
        guestZone.dataset.phase = phase; guestZone.dataset.groupIdx = String(g._idx); guestZone.dataset.role = 'guest';
        guestZone.innerHTML = '<div class="text-xs text-[#4a5568] mb-1">Guests</div>';
        (g.guest_team_ids||[]).forEach(tid=>{
          const t = renderTeamCard(String(tid));
          t.dataset.phase = phase; t.dataset.groupIdx = String(g._idx); t.dataset.role = 'guest';
          guestZone.appendChild(t);
        });
        card.appendChild(guestZone);
        // metrics line
        const metLine = document.createElement('div'); metLine.className = 'mt-2 text-xs text-[#4a5568]';
        const travel = (g.travel_seconds!=null) ? `${(g.travel_seconds||0).toFixed(0)}s` : '—';
        const score = (g.score!=null) ? `${(g.score||0).toFixed(1)}` : '—';
        const warns = (g.warnings && g.warnings.length) ? ` · warnings: ${g.warnings.join(', ')}` : '';
        metLine.textContent = `Travel: ${travel} · Score: ${score}${warns}`;
        card.appendChild(metLine);
        wrap.appendChild(card);
      });
      section.appendChild(wrap);
      box.appendChild(section);
    });
    // controls
    const ctrl = document.createElement('div'); ctrl.className = 'flex gap-2 items-center';
    ctrl.innerHTML = `
      <button id="btn-save-groups" class="bg-[#008080] text-white px-3 py-2 rounded-xl text-sm font-semibold hover:bg-[#00b3b3]">Save changes</button>
      <button id="btn-validate-groups" class="bg-[#ffc241] text-[#172a3a] px-3 py-2 rounded-xl text-sm font-semibold hover:bg-[#ffe5d0]">Validate</button>
      <button id="btn-reload-details" class="bg-[#4a5568] text-white px-3 py-2 rounded-xl text-sm font-semibold hover:opacity-90">Reload</button>
      <span id="details-issues" class="text-sm"></span>
    `;
    box.appendChild(ctrl);

    bindDnD();
    bindDetailsControls();
  }

  function bindDnD(){
    const dragData = { teamId: null, fromPhase: null, fromGroupIdx: null, role: null };
    $('#match-details').addEventListener('dragstart', (e)=>{
      const card = e.target.closest('.team-card'); if (!card) return;
      dragData.teamId = card.dataset.teamId;
      dragData.fromPhase = card.dataset.phase; dragData.fromGroupIdx = Number(card.dataset.groupIdx);
      dragData.role = card.dataset.role;
      e.dataTransfer.effectAllowed = 'move';
    });
    function allowDrop(ev){ ev.preventDefault(); ev.dataTransfer.dropEffect = 'move'; }
    $$('.host-zone, .guest-zone', $('#match-details')).forEach(zone=>{
      zone.addEventListener('dragover', allowDrop);
      zone.addEventListener('drop', async (ev)=>{
        ev.preventDefault();
        const toPhase = zone.dataset.phase; const toIdx = Number(zone.dataset.groupIdx); const toRole = zone.dataset.role;
        if (!dragData.teamId) return;
        if (toRole === 'host'){
          if (dragData.role !== 'guest' || dragData.fromGroupIdx !== toIdx || dragData.fromPhase !== toPhase) return;
          const g = detailsGroups[toIdx];
          const prevHost = g.host_team_id ? String(g.host_team_id) : null;
          g.guest_team_ids = (g.guest_team_ids||[]).filter(t=> String(t) !== dragData.teamId);
          g.host_team_id = dragData.teamId;
          if (prevHost){ g.guest_team_ids.push(prevHost); }
        } else if (toRole === 'guest'){
          const fromG = detailsGroups[dragData.fromGroupIdx];
          const toG = detailsGroups[toIdx];
          // remove from old spot (guest role only; disallow dragging host across groups)
          if (dragData.role === 'guest'){
            fromG.guest_team_ids = (fromG.guest_team_ids||[]).filter(t=> String(t) !== dragData.teamId);
          } else if (dragData.role === 'host'){
            return;
          }
          if (String(toG.host_team_id) === dragData.teamId) return;
          toG.guest_team_ids = toG.guest_team_ids || [];
          if (!toG.guest_team_ids.some(t=> String(t)===dragData.teamId)) toG.guest_team_ids.push(dragData.teamId);
        }
        unsaved = true;
        renderMatchDetailsBoard();
        await validateCurrentGroups();
      });
    });
  }

  async function validateCurrentGroups(){
    const evId = $('#matching-event-select').value;
    const res = await apiFetch(`/matching/${evId}/validate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ groups: detailsGroups }) });
    const data = await res.json().catch(()=>({ violations:[], phase_issues:[], group_issues:[] }));
    const issues = [];
    (data.violations||[]).forEach(v=> issues.push(`pair ${v.pair[0]}↔${v.pair[1]} ${v.count} times`));
    (data.phase_issues||[]).forEach(v=> issues.push(`[${v.phase}] team ${v.team_id}: ${v.issue}`));
    (data.group_issues||[]).forEach(v=> issues.push(`[${v.phase||'?'}] group#${v.group_idx}: ${v.issue}`));
    $('#details-issues').textContent = issues.length ? `Issues: ${issues.join(' · ')}` : 'No issues detected.';
  }

  function bindDetailsControls(){
    $('#btn-reload-details').addEventListener('click', async ()=>{ await loadMatchDetails(detailsVersion); });
    $('#btn-validate-groups').addEventListener('click', validateCurrentGroups);
    $('#btn-save-groups').addEventListener('click', async ()=>{
      const evId = $('#matching-event-select').value;
      let r = await apiFetch(`/matching/${evId}/set_groups`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version: detailsVersion, groups: detailsGroups }) });
      if (r.ok){
        const res = await r.json().catch(()=>({}));
        if (res.status === 'warning'){
          const msgs = [].concat((res.violations||[]).map(v=>`pair ${v.pair[0]}↔${v.pair[1]} ${v.count} times`), (res.phase_issues||[]).map(v=>`[${v.phase}] ${v.team_id} ${v.issue}`));
          if (confirm(`Warnings detected:\n${msgs.join('\n')}\nProceed anyway?`)){
            r = await apiFetch(`/matching/${evId}/set_groups`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version: detailsVersion, groups: detailsGroups, force: true }) });
          } else {
            return;
          }
        }
        if (r.ok){
          unsaved = false;
          await loadProposals();
          await loadMatchDetails(detailsVersion);
        } else {
          const t = await r.text(); alert(`Failed to save: ${t}`);
        }
      } else {
        const t = await r.text(); alert(`Failed to save: ${t}`);
      }
    });
  }

  async function loadMatchDetails(version){
    const evId = $('#matching-event-select').value;
    const url = version ? `/matching/${evId}/details?version=${version}` : `/matching/${evId}/details`;
    const res = await apiFetch(url);
    if (!res.ok){ $('#match-details').innerHTML = ''; $('#match-details-msg').textContent = 'No details available.'; return; }
    const data = await res.json().catch(()=>null);
    if (!data){ $('#match-details').innerHTML = ''; return; }
    detailsVersion = data.version; detailsGroups = data.groups || []; teamDetails = data.team_details || {}; unsaved = false;
    renderMatchDetailsBoard();
  }

  async function loadEvents(){
    const res = await apiFetch('/events/');
    const events = await res.json().catch(()=>[]);
    const tbody = $('#events-tbody');
    tbody.innerHTML = '';
    events.forEach(ev=>{
      const tr = document.createElement('tr');
      const currentStatus = (ev.status||'').toLowerCase();
      tr.innerHTML = `
        <td class="p-2 font-semibold">${ev.title||'Untitled'}</td>
        <td class="p-2">${ev.date||''}</td>
        <td class="p-2">${ev.city||''}</td>
        <td class="p-2"><span class="tag tag-${currentStatus}">${ev.status||''}</span></td>
        <td class="p-2"><span class="tag tag-${(ev.matching_status||'').toLowerCase()}">${ev.matching_status||''}</span></td>
        <td class="p-2">${ev.attendee_count||0}</td>
        <td class="p-2 space-y-1">
          <div class="flex items-center gap-2">
            <select data-action="set-status-select" data-id="${ev.id}" class="border border-[#f0f4f7] rounded-xl p-1 text-sm">
              <option value="draft" ${currentStatus==='draft'?'selected':''}>draft</option>
              <option value="coming_soon" ${currentStatus==='coming_soon'?'selected':''}>coming_soon</option>
              <option value="open" ${currentStatus==='open'?'selected':''}>open</option>
            </select>
            <button data-action="set-status" data-id="${ev.id}" class="bg-[#008080] text-white px-2 py-1 rounded-xl text-xs font-semibold hover:bg-[#00b3b3]">Set</button>
          </div>
          <div class="flex items-center gap-2">
            <button data-action="edit" data-id="${ev.id}" class="bg-[#ffc241] text-[#172a3a] px-2 py-1 rounded-xl text-xs font-semibold hover:bg-[#ffe5d0]">Edit</button>
            <button data-action="delete" data-id="${ev.id}" data-title="${ev.title||''}" class="bg-[#e53e3e] text-white px-2 py-1 rounded-xl text-xs font-semibold hover:opacity-90">Delete</button>
          </div>
        </td>`;
      tbody.appendChild(tr);
    });
    $('#events-count').textContent = `${events.length} events`;
    const selects = [$('#matching-event-select'), $('#issues-event-select'), $('#refunds-event-select')];
    selects.forEach(sel=>{ if (!sel) return; sel.innerHTML = events.map(e=>`<option value="${e.id}">${e.title} (${e.date||''})</option>`).join(''); });
    tbody.onclick = async (e)=>{
      const btn = e.target.closest('button'); if (!btn) return;
      const id = btn.getAttribute('data-id'); const action = btn.getAttribute('data-action');
      if (action === 'set-status'){
        const row = btn.closest('tr');
        const select = row && row.querySelector('select[data-action="set-status-select"][data-id="'+id+'"]');
        const newStatus = select ? select.value : null;
        if (!newStatus) return;
        const r = await apiFetch(`/events/${id}/status/${encodeURIComponent(newStatus)}`, { method: 'POST' });
        if (r.ok) { await loadEvents(); }
        else { const t = await r.text(); alert(`Failed to set status: ${t}`); }
      } else if (action === 'edit'){
        const r = await apiFetch(`/events/${id}`);
        if (!r.ok) return;
        const ev = await r.json().catch(()=>null);
        if (!ev) return;
        ev.id = id;
        enterEditMode(ev);
      } else if (action === 'delete'){
        const title = btn.getAttribute('data-title') || id;
        if (!confirm(`Delete event "${title}"? This will also remove related registrations, matches, plans, etc.`)) return;
        const r = await apiFetch(`/events/${id}`, { method: 'DELETE' });
        if (r.ok) { await loadEvents(); }
        else { const t = await r.text(); alert(`Failed to delete: ${t}`); }
      }
    }
  }

  async function handleCreate(){
    const f = $('#create-event-form');
    f.addEventListener('submit', async (e)=>{
      e.preventDefault();
      const payload = readForm();
      const out = $('#create-event-msg');
      let res;
      if (editingId){
        res = await apiFetch(`/events/${editingId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      } else {
        res = await apiFetch('/events/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      }
      if (res.ok) {
        out.textContent = editingId ? 'Event updated.' : 'Event created as draft.';
        enterCreateMode();
        await loadEvents();
      } else {
        const t = await res.text().catch(()=> '');
        out.textContent = `Failed to ${editingId ? 'update' : 'create'} event. ${t}`;
      }
    });
    $('#btn-cancel-edit').addEventListener('click', (e)=>{
      e.preventDefault();
      enterCreateMode();
    });
  }

  function readWeights(){
    return {
      dist: Number($('#w-dist').value||1),
      pref: Number($('#w-pref').value||5),
      allergy: Number($('#w-allergy').value||3),
    };
  }

  function selectedAlgorithms(){
    const algos = [];
    if ($('#algo-greedy').checked) algos.push('greedy');
    if ($('#algo-random').checked) algos.push('random');
    if ($('#algo-local').checked) algos.push('local_search');
    return algos;
  }

  async function startMatching(){
    $('#btn-start-matching').addEventListener('click', async ()=>{
      const evId = $('#matching-event-select').value;
      const weights = readWeights();
      const algorithms = selectedAlgorithms();
      const res = await apiFetch(`/matching/${evId}/start`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ algorithms, weights }) });
      const msg = $('#matching-msg');
      if (res.ok) msg.textContent = 'Matching started.'; else { const t = await res.text(); msg.textContent = `Failed: ${t}`; }
      await loadProposals();
      await loadMatchDetails();
    });
    $('#btn-refresh-matches').addEventListener('click', async ()=>{ await loadProposals(); await loadMatchDetails(detailsVersion); });
    const delAllBtn = $('#btn-delete-all-matches');
    if (delAllBtn){
      delAllBtn.addEventListener('click', async ()=>{
        const evId = $('#matching-event-select').value;
        if (!evId) return;
        if (!confirm('Delete ALL match proposals for this event?')) return;
        const r = await apiFetch(`/matching/${evId}/matches`, { method: 'DELETE' });
        if (r.ok){
          $('#matching-msg').textContent = 'All matches deleted.';
          detailsVersion = null; detailsGroups = []; teamDetails = {}; unsaved = false;
          $('#match-details').innerHTML = '';
          await loadProposals();
        } else {
          const t = await r.text(); alert(`Failed to delete: ${t}`);
        }
      });
    }
  }

  async function loadProposals(){
    const evId = $('#matching-event-select').value;
    const res = await apiFetch(`/matching/${evId}/matches`);
    const list = await res.json().catch(()=>[]);
    const box = $('#proposals'); box.innerHTML = '';
    list.forEach(m=>{
      const d = document.createElement('div');
      d.className = 'p-3 rounded-xl border border-[#f0f4f7]';
      const met = m.metrics || {}; const alg = m.algorithm||'';
      d.innerHTML = `
        <div class=\"flex items-center justify-between\">
          <div class=\"font-semibold\">v${m.version} · ${alg}</div>
          <div class=\"text-sm text-[#4a5568]\">Travel: ${(met.total_travel_seconds||0).toFixed(0)}s · Score: ${(met.aggregate_group_score||0).toFixed(1)}</div>
        </div>
        <div class=\"mt-2 flex gap-2\">
          <button data-view=\"${m.version}\" class=\"bg-[#4a5568] text-white rounded-xl px-3 py-1 text-sm\">View</button>
          <button data-finalize=\"${m.version}\" class=\"bg-[#1b5e20] text-white rounded-xl px-3 py-1 text-sm\">Release</button>
          <button data-issues=\"${m.version}\" class=\"bg-[#008080] text-white rounded-xl px-3 py-1 text-sm\">View issues</button>
          <button data-delete=\"${m.version}\" class=\"bg-[#e53e3e] text-white rounded-xl px-3 py-1 text-sm\">Delete</button>`;
      box.appendChild(d);
    });
    box.onclick = async (e)=>{
      const vbtn = e.target.closest('button[data-view]');
      const f = e.target.closest('button[data-finalize]');
      const i = e.target.closest('button[data-issues]');
      const del = e.target.closest('button[data-delete]');
      const evId = $('#matching-event-select').value;
      if (vbtn){
        const v = Number(vbtn.getAttribute('data-view'));
        await loadMatchDetails(v);
      } else if (f){
        const v = Number(f.getAttribute('data-finalize'));
        const r = await apiFetch(`/matching/${evId}/finalize?version=${v}`, { method: 'POST' });
        if (r.ok) { await loadEvents(); await loadProposals(); await loadMatchDetails(v); }
      } else if (i){
        const v = Number(i.getAttribute('data-issues'));
        $('#issues-event-select').value = evId; $('#issues-version').value = v;
        await loadIssues();
      } else if (del){
        const v = Number(del.getAttribute('data-delete'));
        if (!confirm(`Delete proposal v${v}?`)) return;
        const r = await apiFetch(`/matching/${evId}/matches?version=${v}`, { method: 'DELETE' });
        if (r.ok){
          await loadProposals();
          if (detailsVersion === v){
            // Reload latest details (may not exist anymore)
            await loadMatchDetails();
          }
        } else {
          const t = await r.text(); alert(`Failed to delete: ${t}`);
        }
      }
    }
  }

  async function loadIssues(){
    const evId = $('#issues-event-select').value;
    const v = $('#issues-version').value;
    const res = await apiFetch(`/matching/${evId}/issues${v?`?version=${v}`:''}`);
    const data = await res.json().catch(()=>({ groups:[], issues:[] }));
    const box = $('#issues-list'); box.innerHTML = '';
    data.issues.forEach(it=>{
      const g = it.group; const tags = (it.issues||[]).join(', ');
      const el = document.createElement('div');
      el.className = 'p-3 rounded-xl border border-[#fde2e1] bg-[#fff7f7]';
      el.textContent = `${g.phase || ''}: host ${g.host_team_id} → guests ${ (g.guest_team_ids||[]).join(', ') } [${tags}]`;
      box.appendChild(el);
    });
  }

  async function bindIssues(){
    $('#btn-load-issues').addEventListener('click', loadIssues);
    $('#btn-finalize').addEventListener('click', async ()=>{
      const evId = $('#issues-event-select').value; const v = Number($('#issues-version').value);
      if (!v) return;
      const r = await apiFetch(`/matching/${evId}/finalize?version=${v}`, { method: 'POST' });
      if (r.ok) { await loadEvents(); await loadProposals(); await loadIssues(); await loadMatchDetails(v); }
    });
    $('#btn-move-team').addEventListener('click', async ()=>{
      const evId = $('#issues-event-select').value; const v = Number($('#issues-version').value);
      const phase = $('#move-phase').value.trim();
      const fromIdx = Number($('#move-from').value); const toIdx = Number($('#move-to').value);
      const teamId = $('#move-team').value.trim();
      if (!evId || !v || !phase || !teamId) return;
      let payload = { version: v, phase, from_group_idx: fromIdx, to_group_idx: toIdx, team_id: teamId };
      let r = await apiFetch(`/matching/${evId}/move`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (r.ok){
        const res = await r.json().catch(()=>({}));
        if (res.status === 'warning' && Array.isArray(res.violations)){
          const msg = res.violations.map(v=>`${v.pair[0]} ↔ ${v.pair[1]} (${v.count} times)`).join('\n');
          if (confirm(`Warning: duplicate meetings detected:\n${msg}\nProceed anyway?`)){
            payload.force = true;
            await apiFetch(`/matching/${evId}/move`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
          }
        }
        await loadIssues();
        await loadMatchDetails(v);
      }
    });
  }

  async function bindRefunds(){
    $('#btn-load-refunds').addEventListener('click', async ()=>{
      const evId = $('#refunds-event-select').value;
      const res = await apiFetch(`/matching/${evId}/refunds`);
      const data = await res.json().catch(()=>({ enabled:false, items:[], total_refund_cents:0 }));
      const box = $('#refunds-overview');
      if (!data.enabled){ box.textContent = 'Refund option disabled for this event.'; return; }
      const rows = data.items.map(it=>`<tr><td class="p-1">${it.user_email||''}</td><td class="p-1">${(it.amount_cents/100).toFixed(2)} €</td><td class="p-1 text-xs">${it.registration_id}</td></tr>`).join('');
      box.innerHTML = `
        <div class="font-semibold">Total refunds: ${(data.total_refund_cents/100).toFixed(2)} €</div>
        <div class="overflow-x-auto mt-2">
          <table class="min-w-full text-sm"><thead><tr class="bg-[#f0f4f7]"><th class="p-1 text-left">User</th><th class="p-1 text-left">Amount</th><th class="p-1 text-left">Registration</th></tr></thead><tbody>${rows}</tbody></table>
        </div>`;
    });
  }

  async function init(){
    await ensureCsrf();
    await loadEvents();
    await handleCreate();
    await startMatching();
    await bindIssues();
    await bindRefunds();
    await loadProposals();
    await loadMatchDetails();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
