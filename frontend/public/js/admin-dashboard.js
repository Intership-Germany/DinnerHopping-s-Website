(function(){
  // Provide apiFetch alias for new client namespace
  const apiFetch = (window.dh && window.dh.apiFetch) || window.apiFetch;
  const $ = (sel, root)=> (root||document).querySelector(sel);
  const $$ = (sel, root)=> Array.from((root||document).querySelectorAll(sel));
  const fmtDate = (s)=> s ? new Date(s).toLocaleString() : '';
  const toast = (msg, opts)=> (window.dh && window.dh.toast) ? window.dh.toast(msg, opts||{}) : null;
  const toastLoading = (msg)=> (window.dh && window.dh.toastLoading) ? window.dh.toastLoading(msg) : { update(){}, close(){} };

  // --- Edit mode state ---
  let editingId = null;

  // --- Matching details state ---
  let detailsVersion = null; // number
  let detailsGroups = [];    // [{phase, host_team_id, guest_team_ids, score?, travel_seconds?, host_address_public?}]
  let teamDetails = {};      // { team_id: {size, team_diet, course_preference, can_host_main, lat, lon} }
  let unsaved = false;

  // --- Map state ---
  let mainMap = null;
  let mainLayers = [];
  let teamMap = null;
  let teamLayers = [];
  let teamMapCurrentId = null;
  const teamNamesCache = {};

  async function ensureCsrf(){
    try{
      if (window.dh && typeof window.dh.initCsrf === 'function') {
        await window.dh.initCsrf();
      } else if (typeof window.initCsrf === 'function') {
        await window.initCsrf();
      }
    } catch(e){}
  }

  // Helpers
  function setBtnLoading(btn, text){ if (!btn) return; btn.dataset._orig = btn.textContent; btn.textContent = text; btn.disabled = true; btn.classList.add('opacity-70'); }
  function clearBtnLoading(btn){ if (!btn) return; const t = btn.dataset._orig; if (t) btn.textContent = t; btn.disabled = false; btn.classList.remove('opacity-70'); }

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
    const titleInput = f.querySelector('input[name="title"]');
    if (titleInput) titleInput.value = ev.title || '';
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
    // Inform zip autocomplete script that form values have been populated
    document.dispatchEvent(new CustomEvent('dh:event_form_loaded'));
  }

  function computeTeamLabel(team, fallbackId){
    if (!team) return fallbackId != null ? `Team ${fallbackId}` : 'Team';
    const members = Array.isArray(team.members) ? team.members : [];
    const names = members.map(m=>{
      if (!m || typeof m !== 'object') return '';
      const display = (m.display_name || '').trim();
      if (display) return display;
      const first = (m.first_name || '').trim();
      const last = (m.last_name || '').trim();
      const combined = [first, last].filter(Boolean).join(' ');
      if (combined) return combined;
      const email = (m.email || '').trim();
      return email;
    }).filter(Boolean);
    if (names.length) return names.join(', ');
    if (team.name && typeof team.name === 'string' && team.name.trim()) return team.name.trim();
    return fallbackId != null ? `Team ${fallbackId}` : 'Team';
  }

  function versionKey(version){
    return version != null ? String(version) : '__current__';
  }

  function updateTeamNameCache(version, teamMap){
    const key = versionKey(version);
    const cache = teamNamesCache[key] = teamNamesCache[key] || {};
    Object.entries(teamMap || {}).forEach(([tid, team])=>{
      const id = String(tid);
      cache[id] = computeTeamLabel(team, id);
    });
    return cache;
  }

  function getTeamLabel(teamId, version){
    if (teamId == null) return '—';
    const id = String(teamId);
    const key = versionKey(version);
    if (teamNamesCache[key] && teamNamesCache[key][id]) return teamNamesCache[key][id];
    if (teamDetails[id]){
      const lbl = computeTeamLabel(teamDetails[id], id);
      const cache = teamNamesCache[key] = teamNamesCache[key] || {};
      cache[id] = lbl;
      return lbl;
    }
    // try any other cached version as fallback
    for (const otherKey of Object.keys(teamNamesCache)){
      if (teamNamesCache[otherKey] && teamNamesCache[otherKey][id]) return teamNamesCache[otherKey][id];
    }
    return `Team ${id}`;
  }

  async function ensureTeamNames(evId, version){
    const key = versionKey(version);
    if (teamNamesCache[key]) return teamNamesCache[key];
    const params = version != null ? `?version=${encodeURIComponent(version)}` : '';
    try {
      const res = await apiFetch(`/matching/${evId}/details${params}`);
      if (!res.ok) return null;
      const data = await res.json().catch(()=>null);
      if (!data || !data.team_details) return null;
      return updateTeamNameCache(data.version, data.team_details);
    } catch (err){
      return null;
    }
  }

  async function confirmAndRelease(evId, version, btn){
    if (!evId || version == null){
      toast('No proposal loaded to release.', { type: 'warning' });
      return false;
    }
    const button = btn || null;
    const baseMsg = `Release proposal v${version}?`;
    if (button) setBtnLoading(button, 'Checking...');
    let prompt = baseMsg;
    try {
      await ensureTeamNames(evId, version);
      const res = await apiFetch(`/matching/${evId}/issues?version=${version}`);
      const data = await res.json().catch(()=>({ issues: [] }));
      const items = Array.isArray(data.issues) ? data.issues : [];
      if (items.length){
        const counts = {};
        items.forEach(entry=>{ (entry.issues||[]).forEach(kind=>{ counts[kind] = (counts[kind]||0) + 1; }); });
        const summary = Object.entries(counts).map(([kind,total])=>`- ${kind.replace(/_/g,' ')}: ${total}`).join('\n');
        const sample = items.slice(0,5).map(entry=>{
          const g = entry.group || {};
          const hostLabel = getTeamLabel(g.host_team_id, version);
          const guestLabels = (g.guest_team_ids||[]).map(id=> getTeamLabel(id, version)).join(', ') || '—';
          const tags = (entry.issues||[]).map(k=> k.replace(/_/g,' ')).join(', ');
          return `• ${g.phase||'?'} host ${hostLabel} → guests ${guestLabels} (${tags})`;
        }).join('\n');
        prompt = `${baseMsg}\n\nMatching issues detected:\n${summary}${sample ? `\n\nExamples:\n${sample}` : ''}`;
        if (items.length > 5){ prompt += `\n...and ${items.length - 5} more group(s)`; }
      } else {
        prompt = `${baseMsg}\n\nNo matching issues detected.`;
      }
    } catch(err){
      prompt = `${baseMsg}\n\n(Unable to fetch matching issues. Proceed anyway?)`;
    }
    if (button) clearBtnLoading(button);
    if (!confirm(prompt)) return false;
    if (button) setBtnLoading(button, 'Releasing...');
    const t = toastLoading('Releasing final plan...');
    const res = await apiFetch(`/matching/${evId}/finalize?version=${version}`, { method: 'POST' });
    if (res.ok){
      t.update('Plan released');
      await loadEvents();
      await loadProposals();
      await loadMatchDetails(version);
      t.close();
      if (button) clearBtnLoading(button);
      return true;
    }
    const errText = await res.text().catch(()=> 'Release failed');
    t.update('Release error'); t.close();
    toast(errText || 'Release failed', { type: 'error' });
    if (button) clearBtnLoading(button);
    return false;
  }

  function formatMemberName(member){
    if (!member || typeof member !== 'object') return '';
    const display = (member.display_name || '').trim();
    if (display) return display;
    const first = (member.first_name || '').trim();
    const last = (member.last_name || '').trim();
    const combined = [first, last].filter(Boolean).join(' ');
    if (combined) return combined;
    const email = (member.email || '').trim();
    if (email) return email;
    return '';
  }

  function buildMemberPreview(member, team, fallbackLabel, teamId){
    const lines = [];
    const name = formatMemberName(member) || fallbackLabel || `Team ${teamId || ''}`;
    lines.push(name);
    const email = (member && member.email) ? member.email : null;
    if (email) lines.push(`Email: ${email}`);
    if (member && member.phone) lines.push(`Phone: ${member.phone}`);
    if (team){
      if (team.team_diet) lines.push(`Diet: ${team.team_diet}`);
      if (team.course_preference) lines.push(`Preference: ${team.course_preference}`);
      if (team.can_host_main != null) lines.push(`Can host main: ${team.can_host_main ? 'yes' : 'no'}`);
    }
    return lines.join('\n');
  }

  function buildTeamPreview(team, fallbackLabel, teamId){
    const lines = [];
    const label = fallbackLabel || computeTeamLabel(team, teamId);
    lines.push(label);
    if (team){
      if (team.team_diet) lines.push(`Diet: ${team.team_diet}`);
      if (team.course_preference) lines.push(`Preference: ${team.course_preference}`);
      if (team.can_host_main != null) lines.push(`Can host main: ${team.can_host_main ? 'yes' : 'no'}`);
      if (Array.isArray(team.host_allergies) && team.host_allergies.length){
        lines.push(`Host allergies: ${team.host_allergies.join(', ')}`);
      }
    }
    return lines.join('\n');
  }

  async function copyEmailToClipboard(email){
    if (!email) throw new Error('No email provided');
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function'){
      await navigator.clipboard.writeText(email);
      return;
    }
    await new Promise((resolve, reject)=>{
      try {
        const ta = document.createElement('textarea');
        ta.value = email;
        ta.setAttribute('readonly', '');
        ta.style.position = 'absolute';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand('copy');
        document.body.removeChild(ta);
        if (ok) resolve(); else reject(new Error('Copy command failed'));
      } catch(err){
        reject(err);
      }
    });
  }


  // Admin menu toggle (if present on the page)
  (function bindAdminMenu(){
    try{
      const btn = document.getElementById('admin-menu-btn');
      if (!btn) return;
      const menu = document.getElementById('admin-menu');
      if (!menu) return;
      btn.addEventListener('click', (e)=>{ menu.classList.toggle('hidden'); });
      document.addEventListener('click', (ev)=>{ if (!btn.contains(ev.target) && !menu.contains(ev.target)){ menu.classList.add('hidden'); } });
    }catch(e){}
  })();

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

  function renderTeamCard(tid){
    const det = teamDetails[tid] || {};
    const pref = det.course_preference ? `pref: ${det.course_preference}` : '';
    const diet = det.team_diet ? `diet: ${det.team_diet}` : '';
    const canMain = det.can_host_main ? 'main✔' : '';
    const pay = (det.payment)||{};
    const payStatus = pay.status; // 'paid'|'partial'|'unpaid'|'n/a'
    let colorClasses = 'bg-white border border-[#e5e7eb]';
    let dotClass = null;
    let dotTitle = '';
    if (payStatus === 'unpaid'){
      colorClasses = 'bg-[#fef2f2] border border-[#fecaca]';
      dotClass = 'bg-[#dc2626]';
      dotTitle = 'Unpaid';
    } else if (payStatus === 'partial'){
      colorClasses = 'bg-[#fffbeb] border border-[#fde68a]';
      dotClass = 'bg-[#f59e0b]';
      dotTitle = 'Partial payment';
    } else if (payStatus === 'paid'){
      colorClasses = 'bg-[#f0fdf4] border border-[#bbf7d0]';
      dotClass = 'bg-[#16a34a]';
      dotTitle = 'Paid';
    }

    const el = document.createElement('div');
    el.className = `team-card ${colorClasses} rounded-lg p-2 text-xs cursor-move shadow-sm flex items-start justify-between gap-2 transition-colors duration-150`;
    el.draggable = true;
    el.dataset.teamId = tid;

    const infoWrap = document.createElement('div');
    infoWrap.className = 'min-w-0 flex-1';

    const nameRow = document.createElement('div');
    nameRow.className = 'flex items-center gap-1 font-semibold text-sm flex-wrap';

    const members = Array.isArray(det.members) ? det.members : [];
    if (members.length){
      members.forEach((member, idx)=>{
        const label = formatMemberName(member) || `Member ${idx+1}`;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'team-member-btn truncate text-left focus:outline-none focus-visible:ring-1 focus-visible:ring-[#2563eb]';
        btn.dataset.teamId = tid;
        btn.dataset.memberIndex = String(idx);
        btn.textContent = label;
  btn.style.background = 'transparent';
  btn.style.border = 'none';
  btn.style.padding = '0';
  btn.style.margin = '0';
  btn.style.cursor = 'pointer';
        const email = (member && member.email) ? member.email : '';
        if (email) btn.dataset.email = email;
        btn.title = buildMemberPreview(member, det, label, tid);
        nameRow.appendChild(btn);
        if (idx < members.length - 1){
          const comma = document.createElement('span');
          comma.textContent = ',';
          comma.className = 'text-[#4a5568]';
          nameRow.appendChild(comma);
        }
      });
    } else {
      const label = computeTeamLabel(det, tid);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'team-name-btn truncate text-left focus:outline-none focus-visible:ring-1 focus-visible:ring-[#2563eb]';
      btn.dataset.teamId = tid;
      btn.textContent = label;
  btn.style.background = 'transparent';
  btn.style.border = 'none';
  btn.style.padding = '0';
  btn.style.margin = '0';
  btn.style.cursor = 'pointer';
      btn.title = buildTeamPreview(det, label, tid);
      const primaryEmail = members.length ? (members.find(m=> (m.email||'').trim()) || {}).email : null;
      if (primaryEmail) btn.dataset.email = primaryEmail;
      nameRow.appendChild(btn);
    }

    if (dotClass){
      const dotEl = document.createElement('span');
      dotEl.className = `inline-block w-2.5 h-2.5 rounded-full ${dotClass}`;
      if (dotTitle) dotEl.title = dotTitle;
      nameRow.appendChild(dotEl);
    }

    infoWrap.appendChild(nameRow);

    const meta = [pref, diet, canMain].filter(Boolean).join(' · ');
    if (meta){
      const metaEl = document.createElement('div');
      metaEl.className = 'text-[#4a5568] truncate';
      metaEl.textContent = meta;
      infoWrap.appendChild(metaEl);
    }

    if (payStatus && payStatus !== 'n/a'){
      const statusEl = document.createElement('div');
      statusEl.className = `mt-0.5 text-[10px] uppercase tracking-wide ${payStatus==='unpaid'?'text-[#b91c1c]':'text-[#92400e]'}`;
      statusEl.textContent = payStatus;
      infoWrap.appendChild(statusEl);
    }

    const allergyList = Array.isArray(det.allergies) ? det.allergies.map(v=> (v == null ? '' : String(v).trim())).filter(Boolean) : [];
    if (allergyList.length){
      const allergyEl = document.createElement('div');
      allergyEl.className = 'mt-1 text-[11px] text-[#0f172a] truncate';
      allergyEl.textContent = `allergies: ${allergyList.join(', ')}`;
      infoWrap.appendChild(allergyEl);
    }

    const mapBtn = document.createElement('button');
    mapBtn.className = 'team-map-btn text-[13px]';
    mapBtn.title = 'View path';
    mapBtn.dataset.teamId = tid;
    mapBtn.textContent = '🗺️';

    el.appendChild(infoWrap);
    el.appendChild(mapBtn);
    return el;
  }

  function bindTeamNameButtons(){
    const root = $('#match-details');
    if (!root || root.dataset.nameBound) return;
    root.addEventListener('click', async (ev)=>{
      const btn = ev.target.closest('.team-member-btn, .team-name-btn');
      if (!btn || !root.contains(btn)) return;
      const email = (btn.dataset.email || '').trim();
      if (!email){
        toast('No email available for this contact.', { type: 'info' });
        return;
      }
      try {
        await copyEmailToClipboard(email);
        toast(`Copied ${email} to clipboard.`, { type: 'success' });
      } catch (err){
        toast('Unable to copy email to clipboard.', { type: 'error' });
      }
    });
    root.dataset.nameBound = '1';
  }

  function bindWeightInfo(){
    if (bindWeightInfo._bound) return;
    document.addEventListener('click', (ev)=>{
      const btn = ev.target.closest('.weight-info');
      if (!btn) return;
      ev.preventDefault();
      const info = (btn.dataset && btn.dataset.info) ? btn.dataset.info : 'No description available.';
      alert(info);
    });
    bindWeightInfo._bound = true;
  }

  function bindAdvancedWeightsToggle(){
    if (bindAdvancedWeightsToggle._bound) return;
    const btn = document.getElementById('advanced-weight-toggle');
    const panel = document.getElementById('advanced-weight-panel');
    if (!btn || !panel) return;
    const icon = document.getElementById('advanced-weight-toggle-icon');
    const syncState = ()=>{
      const hidden = panel.classList.contains('hidden');
      btn.setAttribute('aria-expanded', hidden ? 'false' : 'true');
      if (icon) icon.textContent = hidden ? '▼' : '▲';
    };
    btn.addEventListener('click', ()=>{
      panel.classList.toggle('hidden');
      syncState();
    });
    syncState();
    bindAdvancedWeightsToggle._bound = true;
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
    msg.textContent = unsaved ? 'You have unsaved changes. Metrics auto-updated from preview (not saved yet).' : '';
    const by = groupsByPhase();
    const phases = ['appetizer','main','dessert'];
    box.innerHTML = '';
    const normalizeList = (value)=>{
      const arr = Array.isArray(value) ? value : [];
      const out = [];
      const seen = new Set();
      arr.forEach(item=>{
        if (item == null) return;
        const s = String(item).trim();
        if (!s || seen.has(s)) return;
        seen.add(s);
        out.push(s);
      });
      return out;
    };
    const teamAllergies = (tid)=>{
      if (tid == null) return [];
      const det = teamDetails[String(tid)] || {};
      return normalizeList(det.allergies);
    };
    const hostFallbackAllergies = (tid)=>{
      if (tid == null) return [];
      const det = teamDetails[String(tid)] || {};
      const hostVals = normalizeList(det.host_allergies);
      if (hostVals.length) return hostVals;
      return normalizeList(det.allergies);
    };
    // Legend (payment coloring)
    const legend = document.createElement('div');
    legend.className = 'flex flex-wrap gap-4 items-center text-[11px] bg-[#f8fafc] p-2 rounded-lg border border-[#e2e8f0]';
    legend.innerHTML = `
      <div class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-[#dc2626]"></span> unpaid</div>
      <div class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-[#f59e0b]"></span> partial</div>
      <div class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-[#16a34a]"></span> paid</div>
      <div class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-[#9ca3af]"></span> n/a</div>`;
    box.appendChild(legend);
    phases.forEach(phase=>{
      const section = document.createElement('div');
      section.innerHTML = `<div class="font-semibold mb-2 capitalize">${phase}</div>`;
      const wrap = document.createElement('div'); wrap.className = 'grid grid-cols-1 md:grid-cols-3 gap-3';
      (by[phase]||[]).forEach((g, localIdx)=>{
        const card = document.createElement('div');
        card.className = 'p-3 rounded-xl border border-[#f0f4f7] bg-[#fcfcfd] group relative';
        // host
        const hostZone = document.createElement('div'); hostZone.className = 'host-zone mb-2 p-2 rounded border border-dashed bg-white/40';
        hostZone.dataset.phase = phase; hostZone.dataset.groupIdx = String(g._idx); hostZone.dataset.role = 'host';
        hostZone.innerHTML = '<div class="text-xs text-[#4a5568] mb-1 flex items-center justify-between"><span>Host</span></div>';
        const hostTeamId = g.host_team_id != null ? String(g.host_team_id) : null;
        const hostAllergies = normalizeList(Array.isArray(g.host_allergies) && g.host_allergies.length ? g.host_allergies : hostFallbackAllergies(hostTeamId));
        const hostCard = g.host_team_id ? renderTeamCard(String(g.host_team_id)) : null;
        if (hostCard){ hostCard.dataset.phase = phase; hostCard.dataset.groupIdx = String(g._idx); hostCard.dataset.role = 'host'; hostZone.appendChild(hostCard); }
        // host address (public) if available
        const addr = g.host_address_public || g.host_address;
        const addrEl = document.createElement('div');
        addrEl.className = 'text-[11px] text-[#4a5568] mt-1 truncate';
        addrEl.textContent = `Host address: ${addr ? addr : '—'}`;
        hostZone.appendChild(addrEl);
        if (hostAllergies.length){
          const allergyEl = document.createElement('div');
          allergyEl.className = 'text-[11px] text-[#334155] mt-1 truncate';
          allergyEl.textContent = `Host allergies: ${hostAllergies.join(', ')}`;
          hostZone.appendChild(allergyEl);
        }
        card.appendChild(hostZone);
        // guests
        const guestZone = document.createElement('div'); guestZone.className = 'guest-zone p-2 rounded border border-dashed min-h-10 bg-white/40';
        guestZone.dataset.phase = phase; guestZone.dataset.groupIdx = String(g._idx); guestZone.dataset.role = 'guest';
        guestZone.innerHTML = '<div class="text-xs text-[#4a5568] mb-1">Guests</div>';
        (g.guest_team_ids||[]).forEach(tid=>{
          const t = renderTeamCard(String(tid));
          t.dataset.phase = phase; t.dataset.groupIdx = String(g._idx); t.dataset.role = 'guest';
          guestZone.appendChild(t);
        });
        card.appendChild(guestZone);
        const guestUnionSet = new Set(normalizeList(g.guest_allergies_union));
        const guestMap = (g.guest_allergies && typeof g.guest_allergies === 'object') ? g.guest_allergies : {};
        Object.values(guestMap).forEach(list=>{
          normalizeList(list).forEach(item=> guestUnionSet.add(item));
        });
        (g.guest_team_ids || []).forEach(tid=>{
          teamAllergies(tid).forEach(item=> guestUnionSet.add(item));
        });
        const guestUnion = Array.from(guestUnionSet);
        let uncovered = normalizeList(g.uncovered_allergies);
        if (!uncovered.length && guestUnion.length){
          const hostSet = new Set(hostAllergies);
          uncovered = guestUnion.filter(item=> !hostSet.has(item));
        }
        // metrics line
        const metLine = document.createElement('div'); metLine.className = 'mt-2 text-xs text-[#4a5568]';
        const travel = (g.travel_seconds!=null) ? `${(g.travel_seconds||0).toFixed(0)}s` : '—';
        const score = (g.score!=null) ? `${(g.score||0).toFixed(1)}` : '—';
        const warns = (g.warnings && g.warnings.length) ? ` · warnings: ${g.warnings.join(', ')}` : '';
        metLine.textContent = `Travel: ${travel} · Score: ${score}${warns}`;
        card.appendChild(metLine);
        const allergySummary = document.createElement('div');
        allergySummary.className = 'mt-2 text-[11px] leading-snug text-[#4a5568]';
        if (guestUnion.length){
          const guestLine = document.createElement('div');
          guestLine.textContent = `Guest allergies: ${guestUnion.join(', ')}`;
          allergySummary.appendChild(guestLine);
        }
        if (uncovered.length){
          const uncoveredLine = document.createElement('div');
          uncoveredLine.className = 'mt-1 text-[#b91c1c]';
          uncoveredLine.textContent = `Uncovered: ${uncovered.join(', ')}`;
          allergySummary.appendChild(uncoveredLine);
          card.classList.remove('border-[#f0f4f7]');
          card.classList.remove('bg-[#fcfcfd]');
          card.classList.add('border-[#fecaca]', 'bg-[#fef2f2]');
        } else if (guestUnion.length){
          const coveredLine = document.createElement('div');
          coveredLine.className = 'mt-1 text-[#16a34a]';
          coveredLine.textContent = 'Host covers listed allergies';
          allergySummary.appendChild(coveredLine);
        }
        if (allergySummary.childNodes.length){
          card.appendChild(allergySummary);
        }
        wrap.appendChild(card);
      });
      section.appendChild(wrap);
      box.appendChild(section);
    });
    // controls
    const ctrl = document.createElement('div'); ctrl.className = 'flex gap-2 items-center flex-wrap';
    ctrl.innerHTML = `
      <button id="btn-save-groups" class="bg-[#008080] text-white px-3 py-2 rounded-xl text-sm font-semibold hover:bg-[#00b3b3]">Save changes</button>
      <button id="btn-validate-groups" class="bg-[#ffc241] text-[#172a3a] px-3 py-2 rounded-xl text-sm font-semibold hover:bg-[#ffe5d0]">Validate</button>
      <button id="btn-release-groups" class="bg-[#2563eb] text-white px-3 py-2 rounded-xl text-sm font-semibold hover:bg-[#1d4ed8]">Release</button>
      <button id="btn-reload-details" class="bg-[#4a5568] text-white px-3 py-2 rounded-xl text-sm font-semibold hover:opacity-90">Reload</button>
      <span id="details-issues" class="text-sm"></span>
    `;
    if (unsaved){
      const releaseBtn = ctrl.querySelector('#btn-release-groups');
      if (releaseBtn){ releaseBtn.disabled = true; releaseBtn.classList.add('opacity-60'); releaseBtn.title = 'Save changes before releasing'; }
    }
    box.appendChild(ctrl);

    bindDnD();
    bindDetailsControls();
    bindTeamMapButtons();
    bindTeamNameButtons();
    // async fetch payment issues summary
    fetchIssuesForDetails();
  }

  async function fetchIssuesForDetails(){
    try {
      if (!detailsVersion) return;
      const evId = $('#matching-event-select').value; if (!evId) return;
      const res = await apiFetch(`/matching/${evId}/issues?version=${detailsVersion}`);
      if (!res.ok) return;
      const data = await res.json().catch(()=>null); if (!data) return;
      const groups = data.issues || [];
      let missing = 0, partial = 0, cancelled = 0, incomplete = 0;
      groups.forEach(g=>{
        (g.issues||[]).forEach(isu=>{
          if (isu==='payment_missing') missing++; else if (isu==='payment_partial') partial++; else if (isu==='faulty_team_cancelled') cancelled++; else if (isu==='team_incomplete') incomplete++;
        });
      });
      const el = $('#details-issues');
      const parts = [];
      if (missing) parts.push(`${missing} missing payment`);
      if (partial) parts.push(`${partial} partial payment`);
      if (cancelled) parts.push(`${cancelled} with cancelled team`);
      if (incomplete) parts.push(`${incomplete} incomplete team`);
      if (parts.length){
        el.textContent = (el.textContent? el.textContent + ' | ' : '') + parts.join(' · ');
      } else if (!el.textContent){
        el.textContent = 'No payment issues.';
      }
    } catch(e){}
  }

  function bindTeamMapButtons(){
    $('#match-details').addEventListener('click', async (e)=>{
      const btn = e.target.closest('.team-map-btn'); if (!btn) return;
      const tid = btn.getAttribute('data-team-id');
      await openTeamMap(tid);
    });
  }

  async function previewCurrentGroups(){
    const evId = $('#matching-event-select').value;
    if (!evId || !detailsGroups || !detailsGroups.length) return;
    // Call preview (fast travel estimation) to refresh score/travel/warnings
    try {
      const res = await apiFetch(`/matching/${evId}/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ groups: detailsGroups }) });
      if (!res.ok) return;
      const data = await res.json().catch(()=>null);
      if (!data || !Array.isArray(data.groups)) return;
      detailsGroups = data.groups;
      renderMatchDetailsBoard();
    } catch (e) {}
  }

  function bindDnD(){
    const root = $('#match-details');
    const dragData = { teamId: null, fromPhase: null, fromGroupIdx: null, role: null };
    if (!root.dataset.dndBound){
      // dragstart on cards
      root.addEventListener('dragstart', (e)=>{
        const card = e.target.closest('.team-card'); if (!card || !root.contains(card)) return;
        dragData.teamId = card.dataset.teamId;
        dragData.fromPhase = card.dataset.phase; dragData.fromGroupIdx = Number(card.dataset.groupIdx);
        dragData.role = card.dataset.role;
        try { e.dataTransfer.setData('text/plain', dragData.teamId || ''); } catch(_) {}
        e.dataTransfer.effectAllowed = 'move';
      }, true);
      // dragend anywhere within root
      root.addEventListener('dragend', ()=>{
        dragData.teamId = null; dragData.fromPhase = null; dragData.fromGroupIdx = null; dragData.role = null;
      }, true);
      // delegated dragover on zones
      root.addEventListener('dragover', (ev)=>{
        const zone = ev.target && (ev.target.closest && ev.target.closest('.host-zone, .guest-zone'));
        if (!zone || !root.contains(zone)) return;
        ev.preventDefault(); ev.stopPropagation();
        try{ ev.dataTransfer.dropEffect = 'move'; } catch(_){}
      }, true);
      // delegated drop on zones
      root.addEventListener('drop', async (ev)=>{
        const zone = ev.target && (ev.target.closest && ev.target.closest('.host-zone, .guest-zone'));
        if (!zone || !root.contains(zone)) return;
        ev.preventDefault(); ev.stopPropagation();
        const toPhase = zone.dataset.phase; const toIdx = Number(zone.dataset.groupIdx); const toRole = zone.dataset.role;
        if (!dragData.teamId) return;
        let changed = false;
        if (toRole === 'host'){
          const sameGroup = (dragData.fromGroupIdx === toIdx) && (dragData.fromPhase === toPhase);
          if (dragData.role === 'guest'){
            const toG = detailsGroups[toIdx];
            if (sameGroup){
              const prevHost = toG.host_team_id ? String(toG.host_team_id) : null;
              toG.guest_team_ids = (toG.guest_team_ids||[]).filter(t=> String(t) !== dragData.teamId);
              toG.host_team_id = dragData.teamId;
              if (prevHost){ toG.guest_team_ids.push(prevHost); }
              changed = true;
            } else {
              const fromG = detailsGroups[dragData.fromGroupIdx];
              const prevHost = toG.host_team_id ? String(toG.host_team_id) : null;
              fromG.guest_team_ids = (fromG.guest_team_ids||[]).filter(t=> String(t) !== dragData.teamId);
              toG.host_team_id = dragData.teamId;
              toG.guest_team_ids = (toG.guest_team_ids||[]).filter(t=> String(t) !== dragData.teamId);
              if (prevHost){ if (!toG.guest_team_ids.some(t=> String(t)===prevHost)) toG.guest_team_ids.push(prevHost); }
              changed = true;
            }
          } else {
            toast("Dragging a host onto another 'Host' isn't supported. Move a guest into 'Host' to promote it.", { type: 'warning' });
            return;
          }
        } else if (toRole === 'guest'){
          const fromG = detailsGroups[dragData.fromGroupIdx];
          const toG = detailsGroups[toIdx];
          if (dragData.role === 'guest'){
            fromG.guest_team_ids = (fromG.guest_team_ids||[]).filter(t=> String(t) !== dragData.teamId);
          } else if (dragData.role === 'host'){
            toast("Moving a host into 'Guests' isn't supported.", { type: 'warning' });
            return;
          }
          if (String(toG.host_team_id) !== dragData.teamId){
            toG.guest_team_ids = toG.guest_team_ids || [];
            if (!toG.guest_team_ids.some(t=> String(t)===dragData.teamId)) toG.guest_team_ids.push(dragData.teamId);
            changed = true;
          }
        }
        if (!changed) return;
        unsaved = true;
        // Clear drag context before any DOM changes
        dragData.teamId = null; dragData.fromPhase = null; dragData.fromGroupIdx = null; dragData.role = null;
        // Defer UI update until after drop/dragend completes to avoid breaking subsequent drags
        const doUpdate = async ()=>{
          renderMatchDetailsBoard();
          try { await validateCurrentGroups(); } catch(_) {}
          try { await previewCurrentGroups(); } catch(_) {}
          toast('Unsaved changes (preview updated).', { type: 'info' });
        };
        if (typeof requestAnimationFrame === 'function') requestAnimationFrame(()=>{ doUpdate(); }); else setTimeout(()=>{ doUpdate(); }, 0);
      }, true);
      root.dataset.dndBound = '1';
    }
  }

  async function validateCurrentGroups(){
    const evId = $('#matching-event-select').value;
    const res = await apiFetch(`/matching/${evId}/validate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ groups: detailsGroups }) });
    const data = await res.json().catch(()=>({ violations:[], phase_issues:[], group_issues:[] }));
    const issues = [];
    (data.violations||[]).forEach(v=>{
      const names = (v.pair||[]).map(id=> getTeamLabel(id, detailsVersion));
      issues.push(`pair ${names[0]||'—'} ↔ ${names[1]||'—'} ${v.count} times`);
    });
    (data.phase_issues||[]).forEach(v=>{
      const teamLabel = getTeamLabel(v.team_id, detailsVersion);
      issues.push(`[${v.phase}] team ${teamLabel}: ${v.issue}`);
    });
    (data.group_issues||[]).forEach(v=> issues.push(`[${v.phase||'?'}] group#${v.group_idx}: ${v.issue}`));
    $('#details-issues').textContent = issues.length ? `Issues: ${issues.join(' · ')}` : 'No issues detected.';
    if (issues.length){ toast(`Warnings: ${issues.length} issue(s) detected.`, { type: 'warning' }); }
  }

  function bindDetailsControls(){
    $('#btn-reload-details').addEventListener('click', async ()=>{ const t = toastLoading('Loading details...'); await loadMatchDetails(detailsVersion); t.close(); });
    $('#btn-validate-groups').addEventListener('click', validateCurrentGroups);
    const releaseBtn = $('#btn-release-groups');
    if (releaseBtn){
      releaseBtn.addEventListener('click', async (e)=>{
        if (unsaved){
          toast('Please save changes before releasing.', { type: 'warning' });
          return;
        }
        const evId = $('#matching-event-select').value;
        await confirmAndRelease(evId, detailsVersion, e.currentTarget);
      });
    }
    $('#btn-save-groups').addEventListener('click', async (e)=>{
      const btn = e.currentTarget; setBtnLoading(btn, 'Saving...');
      const evId = $('#matching-event-select').value;
      let r = await apiFetch(`/matching/${evId}/set_groups`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version: detailsVersion, groups: detailsGroups }) });
      if (r.ok){
        const res = await r.json().catch(()=>({}));
        if (res.status === 'warning'){
          const msgs = [].concat(
            (res.violations||[]).map(v=>{
              const names = (v.pair||[]).map(id=> getTeamLabel(id, detailsVersion));
              return `pair ${names[0]||'—'} ↔ ${names[1]||'—'} ${v.count} times`;
            }),
            (res.phase_issues||[]).map(v=>`[${v.phase}] ${getTeamLabel(v.team_id, detailsVersion)} ${v.issue}`)
          );
          toast(`Warnings (${msgs.length})`, { type: 'warning' });
          if (confirm(`Warnings detected:\n${msgs.join('\n')}\nProceed anyway?`)){
            r = await apiFetch(`/matching/${evId}/set_groups`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version: detailsVersion, groups: detailsGroups, force: true }) });
          } else {
            clearBtnLoading(btn); return;
          }
        }
        if (r.ok){
          unsaved = false;
          toast('Changes saved.', { type: 'success' });
          await loadProposals();
          await loadMatchDetails(detailsVersion);
        } else {
          const t = await r.text(); alert(`Failed to save: ${t}`);
        }
      } else {
        const t = await r.text(); alert(`Failed to save: ${t}`);
      }
      clearBtnLoading(btn);
    });
  }

  async function loadMatchDetails(version){
    const evId = $('#matching-event-select').value;
    const url = version ? `/matching/${evId}/details?version=${version}` : `/matching/${evId}/details`;
    const t = toastLoading('Loading matching details...');
    // show spinner message
    $('#match-details').innerHTML = '<div class="flex items-center gap-2 text-sm"><span class="spinner"></span> Loading details...</div>';
    const res = await apiFetch(url);
    if (!res.ok){ $('#match-details').innerHTML = ''; $('#match-details-msg').textContent = 'No details available.'; t.update('No details.'); t.close(); return; }
    const data = await res.json().catch(()=>null);
    if (!data){ $('#match-details').innerHTML = ''; t.update('Load error.'); t.close(); return; }
    detailsVersion = data.version;
    detailsGroups = data.groups || [];
    teamDetails = data.team_details || {};
    unsaved = false;
    updateTeamNameCache(detailsVersion, teamDetails);
    renderMatchDetailsBoard();
    t.update('Details loaded'); t.close();
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
    const selects = [$('#matching-event-select'), $('#refunds-event-select'), $('#map-event-select')];
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
      const btn = $('#btn-submit-event'); setBtnLoading(btn, editingId ? 'Updating...' : 'Creating...');
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
      clearBtnLoading(btn);
    });
    $('#btn-cancel-edit').addEventListener('click', (e)=>{
      e.preventDefault();
      enterCreateMode();
    });
  }

  function readWeights(){
    const defaults = {
      dist: 1,
      pref: 5,
      allergy: 3,
      desired_host: 10,
      trans: 0.5,
      final_party: 0.5,
      phase_order: 0,
      cap_penalty: 5,
      dup: 1000,
    };
    const get = (id, key)=>{
      const el = document.getElementById(id);
      if (!el) return defaults[key];
      const val = parseFloat(el.value);
      return Number.isFinite(val) ? val : defaults[key];
    };
    return {
      dist: get('w-dist', 'dist'),
      pref: get('w-pref', 'pref'),
      allergy: get('w-allergy', 'allergy'),
      desired_host: get('w-desired-host', 'desired_host'),
      trans: get('w-trans', 'trans'),
      final_party: get('w-final-party', 'final_party'),
      phase_order: get('w-phase-order', 'phase_order'),
      cap_penalty: get('w-cap-penalty', 'cap_penalty'),
      dup: get('w-dup', 'dup'),
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
    $('#btn-start-matching').addEventListener('click', async (e)=>{
      const btn = e.currentTarget; setBtnLoading(btn, 'Starting...');
      const evId = $('#matching-event-select').value;
      const weights = readWeights();
      const algorithms = selectedAlgorithms();
      const t = toastLoading('Starting matching...');
      const res = await apiFetch(`/matching/${evId}/start`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ algorithms, weights }) });
      const msg = $('#matching-msg');
      if (res.ok) { msg.textContent = 'Matching started.'; t.update('Matching started'); }
      else { const tx = await res.text(); msg.textContent = `Failed: ${tx}`; t.update('Matching error'); }
      await loadProposals();
      await loadMatchDetails();
      t.close();
      clearBtnLoading(btn);
    });
    $('#btn-refresh-matches').addEventListener('click', async ()=>{ const t = toastLoading('Refreshing proposals...'); await loadProposals(); await loadMatchDetails(detailsVersion); t.update('Proposals refreshed'); t.close(); });
    const delAllBtn = $('#btn-delete-all-matches');
    if (delAllBtn){
      delAllBtn.addEventListener('click', async (e)=>{
        const btn = e.currentTarget; const evId = $('#matching-event-select').value;
        if (!evId) return;
        if (!confirm('Delete ALL match proposals for this event?')) return;
        setBtnLoading(btn, 'Deleting...');
        const t = toastLoading('Deleting proposals...');
        const r = await apiFetch(`/matching/${evId}/matches`, { method: 'DELETE' });
        if (r.ok){
          $('#matching-msg').textContent = 'All matches deleted.';
          detailsVersion = null; detailsGroups = []; teamDetails = {}; unsaved = false;
          $('#match-details').innerHTML = '';
          await loadProposals();
          t.update('Deleted');
        } else {
          const tx = await r.text(); alert(`Failed to delete: ${tx}`); t.update('Delete error');
        }
        t.close();
        clearBtnLoading(btn);
      });
    }
  }

  async function loadProposals(){
    const evId = $('#matching-event-select').value;
    const res = await apiFetch(`/matching/${evId}/matches`);
    const list = await res.json().catch(()=>[]);
    const box = $('#proposals'); box.innerHTML = '';
    const finalizedRecord = list.find(m=> (m.status||'').toLowerCase() === 'finalized');
    const finalizedVersion = finalizedRecord ? Number(finalizedRecord.version) : null;
    list.forEach(m=>{
      const version = Number(m.version);
      const metrics = m.metrics || {};
      const algorithm = m.algorithm || '';
      const isFinalized = (m.status||'').toLowerCase() === 'finalized';
      const isCurrent = detailsVersion === version;
      const wasEdited = Boolean(m.updated_at && (!m.created_at || m.updated_at !== m.created_at));
      const classes = ['p-3','rounded-xl','border','transition','shadow-sm','proposal-card'];
      if (isFinalized){
        classes.push('border-[#bbf7d0]','bg-[#f0fdf4]');
      } else {
        classes.push('border-[#f0f4f7]','bg-white');
        if (finalizedVersion != null && version !== finalizedVersion){
          classes.push('opacity-70');
        }
      }
      if (isCurrent){ classes.push('ring-2','ring-offset-1','ring-[#2563eb]'); }
      const card = document.createElement('div');
      card.className = classes.join(' ');

      const createdAt = m.created_at ? fmtDate(m.created_at) : null;
      const updatedAt = m.updated_at ? fmtDate(m.updated_at) : null;
      const finalizedAt = m.finalized_at ? fmtDate(m.finalized_at) : null;
      const metaParts = [];
      if (createdAt) metaParts.push(`Created ${createdAt}`);
      if (updatedAt && (!createdAt || updatedAt !== createdAt)) metaParts.push(`Updated ${updatedAt}`);
      if (finalizedAt) metaParts.push(`Released ${finalizedAt}`);
      const badges = [];
      if (isFinalized) badges.push('<span class="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-[#bbf7d0] text-[#065f46]">Released</span>');
      if (wasEdited && !isFinalized) badges.push('<span class="px-2 py-0.5 rounded-full text-[11px] bg-[#fee2e2] text-[#b91c1c]">Edited</span>');

      const travel = (metrics.total_travel_seconds||0).toFixed(0);
      const score = (metrics.aggregate_group_score||0).toFixed(1);
      const releaseDisabled = isFinalized || (unsaved && detailsVersion === version);
      let releaseClasses = isFinalized ? 'bg-[#9ca3af] cursor-not-allowed' : 'bg-[#1b5e20] hover:bg-[#166534]';
      let releaseLabel = isFinalized ? 'Released' : 'Release';
      if (!isFinalized && unsaved && detailsVersion === version){
        releaseClasses = 'bg-[#9ca3af] cursor-not-allowed';
        releaseLabel = 'Save first';
      }
      card.innerHTML = `
        <div class="flex items-center justify-between gap-2 flex-wrap">
          <div class="font-semibold flex items-center gap-2">v${version}${algorithm?` · ${algorithm}`:''}${badges.length ? ` <span class=\"flex gap-1\">${badges.join('')}</span>` : ''}</div>
          <div class="text-sm text-[#4a5568]">Travel: ${travel}s · Score: ${score}</div>
        </div>
        ${metaParts.length ? `<div class="mt-1 text-xs text-[#475569]">${metaParts.join(' · ')}</div>` : ''}
        <div class="mt-2 flex flex-wrap gap-2">
          <button data-view="${version}" class="bg-[#4a5568] text-white rounded-xl px-3 py-1 text-sm">${isCurrent ? 'Viewing' : 'View'}</button>
          <button data-release="${version}" class="${releaseClasses} text-white rounded-xl px-3 py-1 text-sm" ${releaseDisabled ? 'disabled' : ''}>${releaseLabel}</button>
          <button data-issues="${version}" class="bg-[#008080] text-white rounded-xl px-3 py-1 text-sm">View issues</button>
          <button data-delete="${version}" class="bg-[#e53e3e] text-white rounded-xl px-3 py-1 text-sm">Delete</button>
        </div>`;
      box.appendChild(card);
    });
    box.onclick = async (e)=>{
      const viewBtn = e.target.closest('button[data-view]');
      const releaseBtn = e.target.closest('button[data-release]');
      const issuesBtn = e.target.closest('button[data-issues]');
      const deleteBtn = e.target.closest('button[data-delete]');
      const evId = $('#matching-event-select').value;
      if (viewBtn){
        setBtnLoading(viewBtn, 'Opening...');
        const v = Number(viewBtn.getAttribute('data-view'));
        await loadMatchDetails(v);
        clearBtnLoading(viewBtn);
      } else if (releaseBtn){
        const v = Number(releaseBtn.getAttribute('data-release'));
        if (unsaved && detailsVersion === v){
          toast('Save changes before releasing this proposal.', { type: 'warning' });
          return;
        }
        await confirmAndRelease(evId, v, releaseBtn);
      } else if (issuesBtn){
        const v = Number(issuesBtn.getAttribute('data-issues'));
        const card = issuesBtn.closest('.proposal-card');
        const existing = card.querySelector('.issues-panel');
        if (existing) { existing.remove(); return; }
        const t = toastLoading('Analyzing issues...');
        const res = await apiFetch(`/matching/${evId}/issues?version=${v}`);
        const data = await res.json().catch(()=>({ groups:[], issues:[] }));
        const items = data.issues||[];
        const count = items.length;
        if (!count){ t.update('No issues detected'); t.close(); return; }
        await ensureTeamNames(evId, v);
        t.update(`${count} group(s) with issues`); t.close();
        // Build grouped counts
        const counts = {};
        items.forEach(it=> (it.issues||[]).forEach(isu=>{ counts[isu] = (counts[isu]||0)+1; }));
        const badge = (k, v)=>`<span class=\"px-2 py-0.5 rounded-full text-[11px] font-medium ${k==='payment_missing'?'bg-[#fee2e2] text-[#991b1b]':k==='payment_partial'?'bg-[#fef3c7] text-[#92400e]':k==='faulty_team_cancelled'?'bg-[#fecaca] text-[#7f1d1d]':k==='team_incomplete'?'bg-[#e0f2fe] text-[#075985]':'bg-[#e2e8f0] text-[#334155]'}\">${k.replace(/_/g,' ')}: ${v}</span>`;
        const panel = document.createElement('div');
        panel.className = 'issues-panel mt-2 border border-[#fde2e1] bg-[#fff7f7] rounded-xl p-3 text-sm';
        panel.innerHTML = `
          <div class=\"mb-2 flex flex-wrap gap-2\">${Object.entries(counts).map(([k,v])=>badge(k,v)).join('')}</div>
          <div class=\"space-y-1 max-h-60 overflow-auto pr-1\">${items.map(it=>{
            const g = it.group || {}; const tags = (it.issues||[]).map(isu=>`<span class=\"inline-block px-1.5 py-0.5 mr-1 mb-1 rounded text-[11px] ${isu==='payment_missing'?'bg-[#fee2e2] text-[#991b1b]':isu==='payment_partial'?'bg-[#fef3c7] text-[#92400e]':isu==='faulty_team_cancelled'?'bg-[#fecaca] text-[#7f1d1d]':isu==='team_incomplete'?'bg-[#e0f2fe] text-[#075985]':'bg-[#e2e8f0] text-[#334155]'}\">${isu.replace(/_/g,' ')}</span>`).join('');
            const hostName = getTeamLabel(g.host_team_id, v);
            const guestNames = (g.guest_team_ids||[]).map(x=> getTeamLabel(x, v)).join(', ');
            return `<div class=\"py-1 border-b last:border-b-0 border-[#f0d4d3]\"><div class=\"text-xs text-[#475569]\"><span class=\"font-semibold\">${g.phase||''}</span> · host ${hostName} → guests ${guestNames || '—'} </div><div class=\"mt-1\">${tags}</div></div>`;
          }).join('')}</div>`;
        card.appendChild(panel);
      } else if (deleteBtn){
        const v = Number(deleteBtn.getAttribute('data-delete'));
        if (!confirm(`Delete proposal v${v}?`)) return;
        const r = await apiFetch(`/matching/${evId}/matches?version=${v}`, { method: 'DELETE' });
        if (r.ok){
          await loadProposals();
          if (detailsVersion === v){
            await loadMatchDetails();
          }
        } else {
          const t = await r.text(); alert(`Failed to delete: ${t}`);
        }
      }
    }
  }

  // Removed old Manual Adjustments & Issues UI binding; handled dynamically via proposal issues button

  // ---------------- Travel Map logic ----------------
  function ensureMainMap(){
    if (mainMap) return mainMap;
    const el = $('#travel-map'); if (!el) return null;
    mainMap = L.map(el).setView([51.0, 9.0], 6);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap' }).addTo(mainMap);
    return mainMap;
  }
  function clearLayers(layers){ layers.forEach(l=>{ try{ l.remove(); } catch(e){} }); layers.length = 0; }

  function phaseColor(phase){ return phase==='appetizer' ? '#059669' : (phase==='main' ? '#f97316' : '#f59e0b'); }

  function drawLegend(container){
    const el = container || $('#map-legend'); if (!el) return;
    el.innerHTML = '';
    const items = [
      { color: '#059669', label: 'Appetizer' },
      { color: '#f97316', label: 'Main' },
      { color: '#f59e0b', label: 'Dessert' },
      { color: '#7c3aed', label: 'After party' },
    ];
    items.forEach(it=>{
      const row = document.createElement('div'); row.className = 'flex items-center gap-2 text-sm';
      const sw = document.createElement('span'); sw.style.cssText = `display:inline-block;width:12px;height:12px;border-radius:9999px;background:${it.color}`;
      row.appendChild(sw);
      const tx = document.createElement('span'); tx.textContent = it.label; row.appendChild(tx);
      el.appendChild(row);
    });
  }

  function drawPathsOn(map, dataPoints, dataGeom, layers){
    const tp = (dataPoints && dataPoints.team_paths) || {};
    const bounds = dataPoints && dataPoints.bounds;
    const afterParty = dataPoints && dataPoints.after_party;
    const colors = ['#e11d48','#1d4ed8','#059669','#f59e0b','#7c3aed','#f43f5e','#0ea5e9','#10b981','#f97316'];
    let colorIdx = 0;
    for (const [tid, rec] of Object.entries(tp)){
      const pts = (rec.points||[]).filter(p=> typeof p.lat==='number' && typeof p.lon==='number');
      if (pts.length < 1) continue;
      const col = colors[colorIdx++ % colors.length];
      // draw route: use geometry segments if provided
      const geomRec = dataGeom && dataGeom.team_geometries && dataGeom.team_geometries[tid];
      if (geomRec && Array.isArray(geomRec.segments) && geomRec.segments.length){
        for (const seg of geomRec.segments){
          const poly = L.polyline(seg, { color: col, weight: 3, opacity: 0.85 }).addTo(map);
          layers.push(poly);
        }
      } else {
        // fallback straight polyline connecting host points in order
        const latlngs = pts.map(p=> [p.lat, p.lon]);
        if (latlngs.length >= 2){
          const poly = L.polyline(latlngs, { color: col, weight: 3, opacity: 0.8, dashArray: '6 6' }).addTo(map);
          layers.push(poly);
        }
      }
      // per-phase markers
      pts.forEach(p=>{
        const pc = phaseColor(p.phase);
        const m = L.circleMarker([p.lat, p.lon], { radius: 5, color: pc, fillColor: pc, fillOpacity: 0.95, weight: 1 }).addTo(map);
        m.bindTooltip(`${p.phase}`, { permanent: false, direction: 'top', offset: [0,-4] });
        layers.push(m);
      });
    }
    if (afterParty && typeof afterParty.lat==='number' && typeof afterParty.lon==='number'){
      const ap = L.circleMarker([afterParty.lat, afterParty.lon], { radius: 6, color: '#7c3aed', fillColor: '#7c3aed', fillOpacity: 1, weight: 2 }).addTo(map);
      ap.bindTooltip('After party', { permanent: false, direction: 'top', offset: [0,-4] });
      layers.push(ap);
    }
    if (bounds && isFinite(bounds.min_lat) && isFinite(bounds.min_lon) && isFinite(bounds.max_lat) && isFinite(bounds.max_lon)){
      try{ map.fitBounds([[bounds.min_lat, bounds.min_lon], [bounds.max_lat, bounds.max_lon]], { padding: [20,20] }); } catch(e){}
    }
    drawLegend();
  }

  function buildVersionQuery(){
    return (typeof detailsVersion === 'number' && !isNaN(detailsVersion)) ? `version=${encodeURIComponent(detailsVersion)}` : '';
  }

  async function loadTravelMapAll(){
    const map = ensureMainMap(); if (!map) return;
    const evId = $('#map-event-select').value || $('#matching-event-select').value;
    const real = $('#map-real-route').checked;
    const msg = $('#map-msg'); msg.textContent = 'Loading...';
    const t = toastLoading('Loading map...');
    clearLayers(mainLayers);
    const vq = buildVersionQuery();
    const baseUrl = `/matching/${evId}/paths${vq?`?${vq}`:''}`;
    const [pointsRes, geomRes] = await Promise.all([
      apiFetch(baseUrl + `${vq?'&':'?'}fast=1`),
      real ? apiFetch(`/matching/${evId}/paths/geometry${vq?`?${vq}`:''}`) : Promise.resolve({ ok: false })
    ]);
    if (!pointsRes.ok){
      msg.textContent = 'No data.';
      toast(`Map: error ${pointsRes.status}`, { type: 'warning' });
      t.update('Load error'); t.close();
      return;
    }
    const dataPoints = await pointsRes.json().catch(()=>({ team_paths:{}, bounds:null }));
    const dataGeom = real && geomRes.ok ? await geomRes.json().catch(()=>null) : null;
    drawPathsOn(map, dataPoints, dataGeom, mainLayers);
    const has = Object.keys(dataPoints.team_paths||{}).length; msg.textContent = has ? 'Done.' : 'No data.';
    t.update(has ? 'Map ready' : 'No data'); t.close();
  }

  async function loadTravelMapFiltered(){
    const map = ensureMainMap(); if (!map) return;
    const evId = $('#map-event-select').value || $('#matching-event-select').value;
    const real = $('#map-real-route').checked;
    const ids = ($('#map-team-ids').value||'').split(',').map(s=>s.trim()).filter(Boolean).join(',');
    const msg = $('#map-msg'); msg.textContent = 'Loading...';
    const t = toastLoading('Loading selected paths...');
    clearLayers(mainLayers);
    const vq = buildVersionQuery();
    const base = `/matching/${evId}/paths${vq?`?${vq}`:''}${ids?`${vq?'&':'?'}ids=${encodeURIComponent(ids)}`:''}`;
    const [pointsRes, geomRes] = await Promise.all([
      apiFetch(base + `${(vq||ids)?'&':'?'}fast=1`),
      real ? apiFetch(`/matching/${evId}/paths/geometry${vq?`?${vq}`:''}${ids?`${vq?'&':'?'}ids=${encodeURIComponent(ids)}`:''}`) : Promise.resolve({ ok: false })
    ]);
    if (!pointsRes.ok){
      msg.textContent = 'No data.';
      toast(`Map: error ${pointsRes.status}`, { type: 'warning' });
      t.update('Load error'); t.close();
      return;
    }
    const dataPoints = await pointsRes.json().catch(()=>({ team_paths:{}, bounds:null }));
    const dataGeom = real && geomRes.ok ? await geomRes.json().catch(()=>null) : null;
    drawPathsOn(map, dataPoints, dataGeom, mainLayers);
    const has = Object.keys(dataPoints.team_paths||{}).length; msg.textContent = has ? 'Done.' : 'No data.';
    t.update(has ? 'Map ready' : 'No data'); t.close();
  }

  async function openTeamMap(teamId){
    teamMapCurrentId = teamId;
    const modal = $('#team-map-modal'); modal.classList.remove('hidden'); modal.classList.add('flex');
    // disable background scrolling
    try { document.body.style.overflow = 'hidden'; } catch(e){}
    // init map first time
    if (!teamMap){
      const el = $('#team-map');
      teamMap = L.map(el).setView([51.0,9.0], 7);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap' }).addTo(teamMap);
    }
    await refreshTeamMap();
  }

  async function refreshTeamMap(){
    const evId = $('#matching-event-select').value;
    const real = $('#team-map-real').checked;
    const msg = $('#team-map-msg'); msg.textContent = 'Loading...';
    const t = toastLoading('Loading team path...');
    clearLayers(teamLayers);
    const ids = encodeURIComponent(teamMapCurrentId);
    const vq = buildVersionQuery();
    const [pointsRes, geomRes] = await Promise.all([
      apiFetch(`/matching/${evId}/paths${vq?`?${vq}`:''}&fast=1&ids=${ids}`.replace('?&','?')),
      real ? apiFetch(`/matching/${evId}/paths/geometry${vq?`?${vq}`:''}&ids=${ids}`.replace('?&','?')) : Promise.resolve({ ok: false })
    ]);
    if (!pointsRes.ok){
      msg.textContent = 'No data.';
      toast(`Team path: error ${pointsRes.status}`, { type: 'warning' });
      t.update('Load error'); t.close();
      return;
    }
    const dataPoints = await pointsRes.json().catch(()=>({ team_paths:{}, bounds:null }));
    const dataGeom = real && geomRes.ok ? await geomRes.json().catch(()=>null) : null;
    drawPathsOn(teamMap, dataPoints, dataGeom, teamLayers);
    const ok = (dataPoints.team_paths && dataPoints.team_paths[teamMapCurrentId]);
    msg.textContent = ok ? 'Done.' : 'No data.';
    t.update(ok ? 'Path ready' : 'No data'); t.close();
  }

  function bindMaps(){
    $('#btn-map-all').addEventListener('click', loadTravelMapAll);
    $('#btn-map-load').addEventListener('click', async ()=>{
      const ids = ($('#map-team-ids').value||'').trim();
      if (ids) await loadTravelMapFiltered(); else await loadTravelMapAll();
    });
    $('#team-map-close').addEventListener('click', ()=>{ $('#team-map-modal').classList.add('hidden'); $('#team-map-modal').classList.remove('flex'); try{ document.body.style.overflow = ''; } catch(e){} });
    $('#team-map-refresh').addEventListener('click', refreshTeamMap);
  }

  async function bindRefunds(){
    const processBtn = $('#btn-process-refunds');
    $('#btn-load-refunds').addEventListener('click', async ()=>{
      const evId = $('#refunds-event-select').value;
  const res = await apiFetch(`payments/admin/events/${evId}/refunds`);
      const data = await res.json().catch(()=>({ enabled:false, items:[], total_refund_cents:0 }));
      const box = $('#refunds-overview');
      const msg = $('#refunds-msg');
      if (!data.enabled){ box.textContent = 'Refund option disabled for this event.'; processBtn.classList.add('hidden'); msg.textContent=''; return; }
      const hasItems = Array.isArray(data.items) && data.items.length>0;
      if (hasItems){ processBtn.classList.remove('hidden'); } else { processBtn.classList.add('hidden'); }
      const rows = data.items.map(it=>`<tr data-reg="${it.registration_id}"><td class="p-1">${it.user_email||''}</td><td class="p-1">${(it.amount_cents/100).toFixed(2)} €</td><td class="p-1 text-xs">${it.registration_id}</td><td class="p-1"><button class="btn-refund-one bg-[#008080] text-white rounded px-2 py-1 text-xs">Refund</button></td></tr>`).join('');
      box.innerHTML = hasItems ? `
        <div class="font-semibold mb-2">Total refunds: ${(data.total_refund_cents/100).toFixed(2)} €</div>
        <div class="overflow-x-auto mt-2">
          <table class="min-w-full text-sm"><thead><tr class="bg-[#f0f4f7]"><th class="p-1 text-left">User</th><th class="p-1 text-left">Amount</th><th class="p-1 text-left">Registration</th><th class="p-1 text-left">Action</th></tr></thead><tbody>${rows}</tbody></table>
        </div>` : '<div class="text-sm">No refunds due.</div>';
      msg.textContent = hasItems ? '' : 'No pending refunds.';
    });
    if (processBtn){
      processBtn.addEventListener('click', async ()=>{
        const evId = $('#refunds-event-select').value; if (!evId) return;
        const t = toastLoading('Processing refunds...');
  const r = await apiFetch(`/events/${evId}/refunds/process`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
        const data = await r.json().catch(()=>({}));
        if (r.ok){ t.update(`Processed ${data.processed||0}`); toast(`Refunds processed: ${data.processed||0}`, { type: 'success' }); }
        else { t.update('Error'); toast('Refund processing failed', { type: 'error' }); }
        t.close();
        // reload overview
        try { $('#btn-load-refunds').click(); } catch(e){}
      });
    }
    // delegated per-row refund
    document.addEventListener('click', async (e)=>{
      const btn = e.target && e.target.closest && e.target.closest('.btn-refund-one');
      if (!btn) return;
      const tr = btn.closest('tr[data-reg]'); if (!tr) return;
      const regId = tr.getAttribute('data-reg');
      const evId = $('#refunds-event-select').value; if (!evId || !regId) return;
      const t = toastLoading('Refunding...');
      btn.disabled = true; btn.classList.add('opacity-70');
  const r = await apiFetch(`/events/${evId}/refunds/process`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ registration_ids: [regId] }) });
      const data = await r.json().catch(()=>({}));
      if (r.ok){ t.update('Done'); toast('Refund processed', { type: 'success' }); }
      else { t.update('Error'); toast('Refund failed', { type: 'error' }); }
      t.close();
      try { $('#btn-load-refunds').click(); } catch(e){}
    });
  }

  async function init(){
    await ensureCsrf();
    await loadEvents();
    await handleCreate();
    await startMatching();
    await bindRefunds();
    bindWeightInfo();
    bindAdvancedWeightsToggle();
    bindMaps();
    // Do not auto-load matching proposals or details on page load.
    // Users must click "Start Matching" or "Refresh Proposals" to fetch them.
    const placeholder = document.getElementById('match-details-msg');
    if (placeholder) placeholder.textContent = 'Click “Refresh Proposals” or “Start Matching” to load data.';
    drawLegend();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
