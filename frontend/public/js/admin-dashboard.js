(function(){
  // Provide apiFetch alias for new client namespace
  const apiFetch = (window.dh && window.dh.apiFetch) || window.apiFetch;
  const $ = (sel, root)=> (root||document).querySelector(sel);
  const $$ = (sel, root)=> Array.from((root||document).querySelectorAll(sel));
  const fmtDate = (s)=> s ? new Date(s).toLocaleString() : '';
  const toast = (msg, opts)=> (window.dh && window.dh.toast) ? window.dh.toast(msg, opts||{}) : null;
  const toastLoading = (msg)=> (window.dh && window.dh.toastLoading) ? window.dh.toastLoading(msg) : { update(){}, close(){} };
  function getDialog(){
    return (window.dh && window.dh.dialog) || null;
  }
  function showDialogAlert(message, options){
    const dlg = getDialog();
    if (dlg && typeof dlg.alert === 'function'){
      return dlg.alert(message, Object.assign({ title: 'Notification', tone: 'info' }, options || {}));
    }
    window.alert(message);
    return Promise.resolve();
  }
  function showDialogConfirm(message, options){
    const dlg = getDialog();
    if (dlg && typeof dlg.confirm === 'function'){
      return dlg.confirm(message, Object.assign({ tone: 'warning', confirmLabel: 'Continue', cancelLabel: 'Cancel' }, options || {}));
    }
    return Promise.resolve(window.confirm(message));
  }
  const ESCAPE_LOOKUP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const ESCAPE_REGEX = /[&<>"']/g;
  const escapeHtml = (value)=>{
    if (value === null || value === undefined) return '';
    return String(value).replace(ESCAPE_REGEX, (ch)=> ESCAPE_LOOKUP[ch] || ch);
  };

  // --- Edit mode state ---
  let editingId = null;

  // --- Matching details state ---
  let detailsVersion = null; // number
  let detailsGroups = [];    // [{phase, host_team_id, guest_team_ids, score?, travel_seconds?, host_address_public?}]
  let detailsMetrics = {};   // { total_participant_count, phase_summary: { phase: { assigned_participants, ... } } }
  let teamDetails = {};      // { team_id: {size, team_diet, course_preference, can_host_main, lat, lon} }
  let unsaved = false;
  // --- Map state ---
  let mainMap = null;
  let mainLayers = [];
  let teamMap = null;
  let teamLayers = [];
  let teamMapCurrentId = null;
  const teamNamesCache = {};
  const syntheticDrafts = {
    splits: {}, // originalTeamId -> { originalId, splitIds: [], members: [{ splitId, member, status }] }
    splitMembers: {}, // splitId -> { originalId, memberIndex }
    createStage: [], // ordered array of participant IDs (split IDs or solo team IDs) staged for pairing
    createdPairs: [], // { pairId, componentIds: [], label }
    singles: {}, // solo teamId -> { status: 'available'|'staged'|'paired'|'placed' }
  };
  const syntheticTempTeamIds = new Set();
  let syntheticStylesInjected = false;

  function ensureSyntheticStyles(){
    if (syntheticStylesInjected) return;
    const style = document.createElement('style');
    style.textContent = `
      .synthetic-drop-zone {
        border: 2px dashed #94a3b8;
        border-radius: 12px;
        padding: 16px 14px;
        background: #f8fafc;
        text-align: center;
        font-size: 12px;
        color: #475569;
        transition: border-color 0.2s ease, background 0.2s ease;
      }
      .synthetic-drop-zone.drag-active {
        border-color: #2563eb;
        background: #e0f2fe;
        color: #1d4ed8;
      }
      .synthetic-available-list {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }
      .synthetic-available-item {
        border: 1px dashed #cbd5f5;
        border-radius: 10px;
        padding: 6px;
        background: #fff;
      }
      .synthetic-pending-actions button {
        border: none;
        border-radius: 6px;
        padding: 4px 8px;
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
      }
      .synthetic-pending-actions button[data-synthetic-action="process-create"] {
        background: #10b981;
        color: #ffffff;
      }
      .synthetic-pending-actions button[data-synthetic-action="process-split"] {
        background: #f59e0b;
        color: #ffffff;
      }
      .synthetic-pending-actions button[data-synthetic-action="remove"] {
        background: #e2e8f0;
        color: #475569;
      }
      .synthetic-pending-actions button:disabled {
        opacity: 0.6;
        cursor: default;
      }
      .synthetic-indicator {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 18px;
        height: 18px;
        border-radius: 9999px;
        background: #f97316;
        color: #ffffff;
        font-size: 11px;
        font-weight: 700;
      }
      .synthetic-remove-btn {
        border: none;
        border-radius: 6px;
        padding: 4px 8px;
        font-size: 11px;
        font-weight: 600;
        background: #e2e8f0;
        color: #334155;
        cursor: pointer;
      }
      .synthetic-remove-btn:hover {
        background: #cbd5f5;
      }
      .synthetic-split-entry,
      .synthetic-pair-entry {
        transition: box-shadow 0.2s ease;
      }
      .synthetic-split-entry:hover,
      .synthetic-pair-entry:hover {
        box-shadow: 0 10px 18px rgba(15, 23, 42, 0.08);
      }
      .synthetic-legend {
        font-size: 11px;
        color: #475569;
        background: #f8fafc;
        padding: 10px 14px;
        border-top: 1px solid #e2e8f0;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .synthetic-panel-section-title {
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #1f2937;
      }
      .synthetic-drop-hint {
        margin-top: 6px;
        font-size: 11px;
        color: #64748b;
      }
      .synthetic-create-stage {
        margin-top: 10px;
        padding: 8px;
        border: 1px dashed #cbd5f5;
        border-radius: 10px;
        min-height: 52px;
        background: #ffffff;
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        align-items: flex-start;
      }
      .synthetic-stage-empty {
        font-size: 11px;
        color: #94a3b8;
      }
      .synthetic-stage-item {
        position: relative;
      }
      .synthetic-stage-remove {
        position: absolute;
        top: -6px;
        right: -6px;
        width: 18px;
        height: 18px;
        border-radius: 9999px;
        background: #ef4444;
        color: #ffffff;
        border: none;
        font-size: 11px;
        line-height: 1;
        cursor: pointer;
      }
      .synthetic-stage-remove:hover {
        opacity: 0.85;
      }
      .synthetic-item-status {
        margin-top: 4px;
        font-size: 10px;
        color: #64748b;
      }
      .synthetic-item-disabled {
        opacity: 0.5;
        cursor: default;
      }
    `;
    document.head.appendChild(style);
    syntheticStylesInjected = true;
  }

  function isSyntheticId(teamId){
    if (!teamId) return false;
    return String(teamId).startsWith('pair:') || String(teamId).startsWith('split:');
  }

  function sanitizeForSyntheticId(value){
    return String(value || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+/, '')
      .replace(/-+$/, '') || 'member';
  }

  function recordSyntheticTempId(teamId){
    syntheticTempTeamIds.add(String(teamId));
  }

  function cleanupTemporarySyntheticTeams(){
    syntheticTempTeamIds.forEach((tid)=>{
      delete teamDetails[tid];
    });
    syntheticTempTeamIds.clear();
  }

  function isTeamIdPlaced(teamId){
    const target = String(teamId);
    return detailsGroups.some((group)=>{
      if (String(group.host_team_id) === target) return true;
      return (group.guest_team_ids || []).some((gid)=> String(gid) === target);
    });
  }

  function removeTeamFromGroups(teamId){
    const target = String(teamId);
    let changed = false;
    detailsGroups.forEach((group)=>{
      if (String(group.host_team_id) === target){
        group.host_team_id = null;
        changed = true;
      }
      const before = (group.guest_team_ids || []).length;
      group.guest_team_ids = (group.guest_team_ids || []).filter((gid)=> String(gid) !== target);
      if ((group.guest_team_ids || []).length !== before){
        changed = true;
      }
    });
    return changed;
  }

  function generateSplitId(originalId, member, index){
    const baseEmail = member && member.email ? member.email : `${originalId}-${index+1}`;
    const normalizedOriginal = sanitizeForSyntheticId(originalId);
    const base = sanitizeForSyntheticId(baseEmail);
    let candidate = `split:${normalizedOriginal}:${base}`;
    let attempt = 1;
    while (teamDetails[candidate] || syntheticDrafts.splitMembers[candidate]){
      candidate = `split:${normalizedOriginal}:${base}-${attempt++}`;
    }
    return candidate;
  }

  function buildSplitTeamDetails(splitId, member, source, originalId){
    const clonedMember = member ? { ...member } : { full_name: 'Participant' };
    const payment = source && source.payment ? { ...source.payment } : { status: 'not_applicable' };
    return {
      id: splitId,
      size: 1,
      team_diet: source ? source.team_diet : null,
      course_preference: source ? source.course_preference : null,
      can_host_main: source ? !!source.can_host_main : false,
      payment,
      members: [clonedMember],
      synthetic_parent: originalId,
      synthetic_kind: 'split',
    };
  }

  function ensureSyntheticSplitDraft(teamId){
    const originalId = String(teamId);
    if (syntheticDrafts.splits[originalId]){
      return syntheticDrafts.splits[originalId];
    }
    const source = teamDetails[originalId];
    if (!source || !Array.isArray(source.members) || source.members.length === 0){
      return null;
    }
    const members = [];
    source.members.forEach((member, index)=>{
      const splitId = generateSplitId(originalId, member, index);
      const splitDetails = buildSplitTeamDetails(splitId, member, source, originalId);
      teamDetails[splitId] = splitDetails;
      recordSyntheticTempId(splitId);
      syntheticDrafts.splitMembers[splitId] = { originalId, memberIndex: index };
      members.push({ splitId, member: member ? { ...member } : null, status: 'available' });
    });
    const entry = {
      originalId,
      splitIds: members.map((m)=> m.splitId),
      members,
      label: computeTeamLabel(source, originalId),
    };
    syntheticDrafts.splits[originalId] = entry;
    return entry;
  }

  function setSplitMemberStatus(splitId, status){
    const info = syntheticDrafts.splitMembers[splitId];
    if (!info) return;
    const entry = syntheticDrafts.splits[info.originalId];
    if (!entry) return;
    const target = entry.members.find((m)=> m.splitId === splitId);
    if (target){
      target.status = status;
    }
  }

  function getSplitMemberStatus(splitId){
    const info = syntheticDrafts.splitMembers[splitId];
    if (!info) return 'available';
    const entry = syntheticDrafts.splits[info.originalId];
    if (!entry) return 'available';
    const target = entry.members.find((m)=> m.splitId === splitId);
    return target ? target.status || 'available' : 'available';
  }

  function refreshSplitMemberStates(){
    const stagedSet = new Set(syntheticDrafts.createStage.map(String));
    const pairedSet = new Set();
    syntheticDrafts.createdPairs.forEach((pair)=>{
      (pair.componentIds || []).forEach((sid)=> pairedSet.add(String(sid)));
    });
    const placedSet = new Set();
    detailsGroups.forEach((group)=>{
      if (group.host_team_id != null){
        placedSet.add(String(group.host_team_id));
      }
      (group.guest_team_ids || []).forEach((gid)=> placedSet.add(String(gid)));
    });
    Object.values(syntheticDrafts.splits).forEach((entry)=>{
      entry.members.forEach((memberRec)=>{
        const sid = String(memberRec.splitId);
        if (pairedSet.has(sid)){
          memberRec.status = 'paired';
        } else if (stagedSet.has(sid)){
          memberRec.status = 'staged';
        } else if (placedSet.has(sid)){
          memberRec.status = 'placed';
        } else {
          memberRec.status = 'available';
        }
      });
    });
  }

  function finalizeComponentUsage(componentIds){
    componentIds.forEach((componentId)=>{
      const id = String(componentId);
      if (isManagedSplitId(id)){
        const info = syntheticDrafts.splitMembers[id];
        if (info){
          const entry = syntheticDrafts.splits[info.originalId];
          if (entry){
            entry.members = entry.members.filter((member)=> String(member.splitId) !== id);
            entry.splitIds = entry.splitIds.filter((sid)=> String(sid) !== id);
            if (!entry.members.length){
              delete syntheticDrafts.splits[info.originalId];
            }
          }
          delete syntheticDrafts.splitMembers[id];
        }
        if (syntheticTempTeamIds.has(id)){
          syntheticTempTeamIds.delete(id);
        }
        delete teamDetails[id];
      } else if (syntheticDrafts.singles[id]){
        delete syntheticDrafts.singles[id];
      }
    });
  }

  function rollbackSyntheticPair(pairId, componentIds, message){
    const target = String(pairId);
    syntheticDrafts.createdPairs = syntheticDrafts.createdPairs.filter((pair)=> String(pair.pairId) !== target);
    if (syntheticTempTeamIds.has(target)){
      syntheticTempTeamIds.delete(target);
    }
    delete teamDetails[target];
    componentIds.slice().reverse().forEach((componentId)=>{
      const id = String(componentId);
      if (isManagedSplitId(id)){
        setSplitMemberStatus(id, 'available');
      } else {
        const entry = syntheticDrafts.singles[id] || { status: 'available' };
        entry.status = 'available';
        syntheticDrafts.singles[id] = entry;
      }
      if (!syntheticDrafts.createStage.includes(id)){
        syntheticDrafts.createStage.unshift(id);
      }
    });
    refreshSplitMemberStates();
    renderMatchDetailsBoard();
    updateSyntheticManagementPanel();
    if (message){
      toast(message, { type: 'error' });
    }
  }

  async function autoPersistSyntheticPair(pairId, componentIds){
    const evSelect = $('#matching-event-select');
    const evId = evSelect ? evSelect.value : null;
    if (!evId){
      rollbackSyntheticPair(pairId, componentIds, 'Sélectionnez un événement avant de créer une nouvelle équipe.');
      return;
    }
    const loader = toastLoading('Création automatique de l’équipe...');
    try {
      const res = await apiFetch(`/admin/teams/create-from-synthetic?event_id=${encodeURIComponent(evId)}&synthetic_id=${encodeURIComponent(pairId)}`, { method: 'POST' });
      loader.close();
      if (!res.ok){
        const txt = await res.text().catch(()=> 'Création impossible.');
        console.error('[CREATE TEAM ERROR]', { status: res.status, error: txt, pairId, componentIds });
        // Afficher le code d'erreur HTTP dans le message
        rollbackSyntheticPair(pairId, componentIds, `Création échouée (HTTP ${res.status}): ${txt}`);
        return;
      }
      removeCreatedPairFromDrafts(pairId);
      finalizeComponentUsage(componentIds);
      if (syntheticTempTeamIds.has(pairId)){
        syntheticTempTeamIds.delete(pairId);
      }
      delete teamDetails[pairId];
      
      // NE PAS recharger les détails pour préserver les changements non sauvegardés
      // La nouvelle équipe sera visible lors du prochain rechargement manuel ou après sauvegarde
      toast('Nouvelle équipe créée. Sauvegardez vos changements pour l\'inclure dans le matching.', { type: 'success' });
      
      // Mettre à jour l'UI sans recharger depuis le serveur
      refreshSplitMemberStates();
      renderMatchDetailsBoard();
      updateSyntheticManagementPanel();
    } catch (error){
      loader.close();
      rollbackSyntheticPair(pairId, componentIds, 'Création automatique impossible (réseau).');
    }
  }

  function removeCreatedPairFromDrafts(pairId){
    const target = String(pairId);
    syntheticDrafts.createdPairs = syntheticDrafts.createdPairs.filter((pair)=> String(pair.pairId) !== target);
  }

  function generatePairIdFromComponents(componentIds){
    const emailParts = componentIds.map((sid, idx)=>{
      const det = teamDetails[sid];
      const member = det && Array.isArray(det.members) && det.members[0] ? det.members[0] : null;
      const email = member && member.email ? member.email : `member-${idx+1}`;
      return email; // Utiliser l'email original, pas la version sanitizée
    }).filter(Boolean);
    const base = emailParts.length ? emailParts.join('+') : componentIds.join('-');
    let candidate = `pair:${base}`;
    let attempt = 1;
    // Pour éviter les collisions avec des noms similaires, ajouter un compteur si nécessaire
    while (teamDetails[candidate] || syntheticDrafts.createdPairs.some((pair)=> String(pair.pairId) === candidate)){
      candidate = `pair:${base}-${attempt++}`;
    }
    return candidate;
  }

  function createPairFromStage(){
    if (syntheticDrafts.createStage.length < 2) return null;
    const usedComponents = syntheticDrafts.createStage.splice(0, 2).map(String);
    usedComponents.forEach((componentId)=>{
      if (isManagedSplitId(componentId)){
        setSplitMemberStatus(componentId, 'paired');
      } else {
        const rec = syntheticDrafts.singles[componentId] || { status: 'available' };
        rec.status = 'paired';
        syntheticDrafts.singles[componentId] = rec;
      }
    });
    const pairId = generatePairIdFromComponents(usedComponents);
    const members = usedComponents.flatMap((componentId)=>{
      const det = teamDetails[componentId];
      if (!det || !Array.isArray(det.members)) return [];
      return det.members.map((member)=> ({ ...member }));
    });
    const pairDetails = {
      id: pairId,
      size: members.length || usedComponents.length,
      team_diet: null,
      course_preference: null,
      can_host_main: members.some((m)=> m && m.can_host_main),
      payment: { status: 'not_applicable' },
      members,
      synthetic_kind: 'pair',
      synthetic_from_split_ids: usedComponents.slice(),
    };
    teamDetails[pairId] = pairDetails;
    recordSyntheticTempId(pairId);
    syntheticDrafts.createdPairs.push({
      pairId,
      componentIds: usedComponents.slice(),
      label: computeTeamLabel(pairDetails, pairId),
    });
    refreshSplitMemberStates();
    renderMatchDetailsBoard();
    updateSyntheticManagementPanel();
    autoPersistSyntheticPair(pairId, usedComponents.slice());
    return pairId;
  }

  function isManagedSplitId(splitId){
    return Object.prototype.hasOwnProperty.call(syntheticDrafts.splitMembers, splitId);
  }

  function isManagedPairId(pairId){
    return syntheticDrafts.createdPairs.some((pair)=> String(pair.pairId) === String(pairId));
  }

  function renderSyntheticCreateStage(container){
    if (!container) return;
    container.innerHTML = '';
    if (!syntheticDrafts.createStage.length){
      const empty = document.createElement('div');
      empty.className = 'synthetic-stage-empty';
      empty.textContent = 'Glissez un participant séparé ici pour commencer une nouvelle équipe.';
      container.appendChild(empty);
      return;
    }
    syntheticDrafts.createStage.forEach((splitId)=>{
  const card = renderTeamCard(splitId);
      card.dataset.phase = 'unplaced';
      card.dataset.groupIdx = '-1';
      card.dataset.role = 'unplaced';
      const wrapper = document.createElement('div');
      wrapper.className = 'synthetic-stage-item';
      wrapper.appendChild(card);
      container.appendChild(wrapper);
    });
  }

  function listAvailableSyntheticParticipants(){
    const items = [];
    Object.values(syntheticDrafts.splits).forEach((entry)=>{
      if (!entry || !Array.isArray(entry.members)) return;
      entry.members.forEach((memberRec)=>{
        const status = memberRec && memberRec.status ? memberRec.status : 'available';
        if (status === 'available'){
          items.push(String(memberRec.splitId));
        }
      });
    });
    Object.entries(syntheticDrafts.singles).forEach(([teamId, info])=>{
      // Ne pas lister les équipes originales qui ont été séparées
      if (info && info.originalTeamSplit === true) return;
      
      const status = info && info.status ? info.status : 'available';
      if (status === 'available'){
        items.push(String(teamId));
      }
    });
    return items;
  }

  function renderSyntheticAvailableParticipants(container){
    if (!container) return;
    container.innerHTML = '';
    const availableIds = listAvailableSyntheticParticipants();
    if (!availableIds.length){
      const empty = document.createElement('div');
      empty.className = 'synthetic-stage-empty';
      empty.textContent = 'Aucun participant disponible pour le moment.';
      container.appendChild(empty);
      return;
    }
    availableIds.sort().forEach((tid)=>{
      const card = renderTeamCard(tid);
      card.dataset.phase = 'unplaced';
      card.dataset.groupIdx = '-1';
      card.dataset.role = 'unplaced';
      const wrapper = document.createElement('div');
      wrapper.className = 'synthetic-available-item';
      wrapper.appendChild(card);
      container.appendChild(wrapper);
    });
  }

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

  function toTimeInputValue(v){
    if (!v && v !== 0) return '';
    if (typeof v === 'string'){
      const txt = v.trim();
      if (!txt) return '';
      const simple = txt.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?(?:([+-]\d{2}:?\d{2})|Z)?$/i);
      if (simple){
        const hh = simple[1].padStart(2, '0');
        const mm = simple[2].padStart(2, '0');
        return `${hh}:${mm}`;
      }
      const isoTime = txt.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})?(?:([+-]\d{2}:?\d{2})|Z)?$/i);
      if (isoTime){
        const hhmm = isoTime[2] || '';
        if (hhmm){
          const [hh, mm] = hhmm.split(':');
          return `${hh.padStart(2,'0')}:${mm.padStart(2,'0')}`;
        }
      }
      const normalized = txt.startsWith('T') ? `1970-01-01${txt}` : (txt.includes('T') ? txt : `1970-01-01T${txt}`);
      const parsed = Date.parse(normalized);
      if (!Number.isNaN(parsed)){
        const d = new Date(parsed);
        const hh = String(d.getHours()).padStart(2, '0');
        const mm = String(d.getMinutes()).padStart(2, '0');
        return `${hh}:${mm}`;
      }
      return '';
    }
    if (v instanceof Date){
      const hh = String(v.getHours()).padStart(2, '0');
      const mm = String(v.getMinutes()).padStart(2, '0');
      return `${hh}:${mm}`;
    }
    if (typeof v === 'number' && Number.isFinite(v)){
      const totalMinutes = Math.floor(v / 60);
      const hh = String(Math.floor(totalMinutes / 60)).padStart(2, '0');
      const mm = String(totalMinutes % 60).padStart(2, '0');
      return `${hh}:${mm}`;
    }
    return '';
  }

  function setForm(ev){
    const f = $('#create-event-form');
    const titleInput = f.querySelector('input[name="title"]');
    if (titleInput) titleInput.value = ev.title || '';
    f.city.value = ev.city || '';
    f.date.value = toDateInputValue(ev.date);
  f.start_at.value = toTimeInputValue(ev.start_at);
    f.registration_deadline.value = toDateTimeLocalInputValue(ev.registration_deadline);
    // per-event meal times (HH:MM) — optional
    if (f.appetizer_time) f.appetizer_time.value = ev.appetizer_time || '';
    if (f.main_time) f.main_time.value = ev.main_time || '';
    if (f.dessert_time) f.dessert_time.value = ev.dessert_time || '';
    if (f.payment_deadline) f.payment_deadline.value = toDateTimeLocalInputValue(ev.payment_deadline);
    f.capacity.value = ev.capacity != null ? String(ev.capacity) : '';
    f.fee_cents.value = ev.fee_cents != null ? String(ev.fee_cents) : '';
    f.valid_zip_codes.value = Array.isArray(ev.valid_zip_codes) ? ev.valid_zip_codes.join(', ') : '';
    const afterPartyLocation = ev.after_party_location || ev.location || null;
    const afterPartyAddress = afterPartyLocation && (afterPartyLocation.address_public || afterPartyLocation.address || '');
    f.after_party_address.value = afterPartyAddress || '';
    f.extra_info.value = ev.extra_info || '';
    f.refund_on_cancellation.checked = !!ev.refund_on_cancellation;
    f.chat_enabled.checked = !!ev.chat_enabled;
    // Inform zip autocomplete script that form values have been populated
    document.dispatchEvent(new CustomEvent('dh:event_form_loaded'));
  }

  function enterEditMode(ev){
    if (!ev || ev.id == null){
      toast('Unable to load this event.', { type: 'error' });
      return;
    }
    editingId = ev.id;
    setForm(ev);
    const title = $('#create-form-title');
    if (title){
      title.textContent = ev.title ? `Edit Event – ${ev.title}` : 'Edit Event';
    }
    const submit = $('#btn-submit-event');
    if (submit){
      submit.textContent = 'Update Event';
    }
    const cancel = $('#btn-cancel-edit');
    if (cancel){
      cancel.classList.remove('hidden');
      cancel.disabled = false;
    }
    const msg = $('#create-event-msg');
    if (msg) msg.textContent = 'Editing existing event. Remember to save changes.';
    const form = $('#create-event-form');
    if (form){
      form.dataset.mode = 'edit';
      try {
        form.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } catch (_) {}
    }
  }

  function enterCreateMode(){
    editingId = null;
    const form = $('#create-event-form');
    if (form){
      form.reset();
      delete form.dataset.mode;
    }
    const title = $('#create-form-title');
    if (title) title.textContent = 'Create New Event';
    const submit = $('#btn-submit-event');
    if (submit) submit.textContent = 'Create Event (Draft)';
    const cancel = $('#btn-cancel-edit');
    if (cancel){
      cancel.classList.add('hidden');
      cancel.disabled = false;
    }
    const msg = $('#create-event-msg');
    if (msg) msg.textContent = '';
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
      // If no name available, extract local part of email for more compact display
      const email = (m.email || '').trim();
      if (email) {
        const localPart = email.split('@')[0];
        return localPart || email;
      }
      return '';
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
    const confirmed = await showDialogConfirm(prompt, {
      title: `Release proposal v${version}`,
      confirmLabel: 'Release',
      tone: 'warning',
    });
    if (!confirmed) return false;
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
    // If no name available, extract local part of email for more compact display
    const email = (member.email || '').trim();
    if (email) {
      const localPart = email.split('@')[0];
      return localPart || email;
    }
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
    Object.entries(syntheticDrafts.singles).forEach(([teamId, record])=>{
      const sid = String(teamId);
      if (!record || typeof record !== 'object'){
        syntheticDrafts.singles[teamId] = { status: 'available' };
        return;
      }
      if (pairedSet.has(sid)){
        record.status = 'paired';
      } else if (stagedSet.has(sid)){
        record.status = 'staged';
      } else if (placedSet.has(sid)){
        record.status = 'placed';
      } else {
        record.status = 'available';
      }
    });
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
    // optional meal phase times (stored as strings like "HH:MM")
    const appetizer = (fd.get('appetizer_time') || '').trim();
    if (appetizer) payload.appetizer_time = appetizer;
    const main = (fd.get('main_time') || '').trim();
    if (main) payload.main_time = main;
    const dessert = (fd.get('dessert_time') || '').trim();
    if (dessert) payload.dessert_time = dessert;
    const afterPartyAddress = (fd.get('after_party_address') || '').trim();
    if (afterPartyAddress) {
      payload.after_party_location = { address: afterPartyAddress };
    }
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

    const isSynthetic = isSyntheticId(tid);
    if (isSynthetic){
      const badge = document.createElement('span');
      badge.className = 'synthetic-indicator';
      badge.textContent = '✳';
      badge.title = 'Équipe synthétique (éditable)';
      nameRow.appendChild(badge);
    }

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
  function bindSyntheticTeamButtons(){
    if (bindSyntheticTeamButtons._bound) return;
    document.addEventListener('click', async (ev)=>{
      const btn = ev.target.closest('.team-create-btn');
      if (!btn) return;
      ev.preventDefault();
      const tid = btn.dataset.teamId;
      if (!tid) return;
      const evId = $('#matching-event-select') ? $('#matching-event-select').value : null;
      if (!evId){
        toast('Sélectionnez un événement.', { type: 'warning' });
        return;
      }
      const proceed = await showDialogConfirm(`Créer une équipe persistante à partir de ${tid}?`, { title: 'Créer équipe', confirmLabel: 'Créer', tone: 'warning' });
      if (!proceed) return;
      const t = toastLoading('Création en cours...');
      const res = await apiFetch(`/admin/teams/create-from-synthetic?event_id=${encodeURIComponent(evId)}&synthetic_id=${encodeURIComponent(tid)}`, { method: 'POST' });
      t.close();
      if (res.ok){
        toast('Équipe créée.', { type: 'success' });
        removeCreatedPairFromDrafts(tid);
        updateSyntheticManagementPanel();
        await loadMatchDetails(detailsVersion);
        await loadProposals();
      } else {
        const txt = await res.text().catch(()=> 'Erreur');
        await showDialogAlert(`Échec: ${txt}`, { title: 'Erreur', tone: 'danger' });
      }
    });
    bindSyntheticTeamButtons._bound = true;
  }

  function bindWeightInfo(){
    if (bindWeightInfo._bound) return;
    document.addEventListener('click', (ev)=>{
      const btn = ev.target.closest('.weight-info');
      if (!btn) return;
      ev.preventDefault();
      const info = (btn.dataset && btn.dataset.info) ? btn.dataset.info : 'No description available.';
      showDialogAlert(info, { title: 'Weight option details', tone: 'info' });
    });
    bindWeightInfo._bound = true;
  }

  function bindAlgorithmInfo(){
    if (bindAlgorithmInfo._bound) return;
    document.addEventListener('click', (ev)=>{
      const btn = ev.target.closest('.algo-info');
      if (!btn) return;
      ev.preventDefault();
      ev.stopPropagation();
      const info = btn.dataset && btn.dataset.info ? btn.dataset.info : 'No description available.';
      const title = btn.dataset && btn.dataset.title ? btn.dataset.title : 'Algorithm details';
      showDialogAlert(info, { title, tone: 'info' });
    });
    bindAlgorithmInfo._bound = true;
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

  const ISSUE_METADATA = {
    payment_missing: { label: 'Missing payment', description: 'At least one team assigned to this group has not completed payment.', tone: 'error' },
    payment_partial: { label: 'Partial payment', description: 'A team recorded a partial payment; verify before confirmation.', tone: 'warning' },
    faulty_team_cancelled: { label: 'Cancelled team', description: 'A cancelled team is still assigned in this proposal.', tone: 'error' },
    team_incomplete: { label: 'Incomplete team', description: 'A team has missing participants or required information.', tone: 'warning' },
    uncovered_allergy: { label: 'Uncovered allergy', description: 'Some guest allergies are not covered by the host.', tone: 'error' },
    capacity_mismatch: { label: 'Capacity mismatch', description: 'The assigned host cannot serve the current number of guests.', tone: 'warning' },
    duplicate_pair: { label: 'Duplicate encounter', description: 'Teams meet more than once across phases.', tone: 'warning' },
    diet_conflict: { label: 'Diet conflict', description: 'Host/guest dietary preferences are incompatible.', tone: 'warning' },
    registration_missing: { label: 'Missing registration', description: 'Active registrations are not included in the proposal.', tone: 'error' },
    phase_participation_gap: { label: 'Missing phase participation', description: 'Teams are absent from one or more meal phases.', tone: 'error' },
  };

  const ISSUE_TONE_STYLES = {
    error: { chipBg: '#fee2e2', chipText: '#7f1d1d', chipBorder: '#fecaca', cardBg: '#fff5f5', cardBorder: '#fecaca', accent: '#dc2626' },
    warning: { chipBg: '#fef3c7', chipText: '#92400e', chipBorder: '#fde68a', cardBg: '#fffbeb', cardBorder: '#fde68a', accent: '#f59e0b' },
    info: { chipBg: '#dbeafe', chipText: '#1d4ed8', chipBorder: '#bfdbfe', cardBg: '#eff6ff', cardBorder: '#bfdbfe', accent: '#2563eb' },
    neutral: { chipBg: '#e2e8f0', chipText: '#334155', chipBorder: '#cbd5f5', cardBg: '#f8fafc', cardBorder: '#e2e8f0', accent: '#94a3b8' },
  };

  const ISSUE_TONE_RANK = { neutral: 0, info: 1, warning: 2, error: 3 };

  function resolveIssueMeta(type){
    return ISSUE_METADATA[type] || { label: type.replace(/_/g, ' '), description: 'See detailed logs for more information.', tone: 'info' };
  }

  function toneForIssues(issueTypes){
    let selected = 'neutral';
    let best = -1;
    issueTypes.forEach(type=>{
      const tone = resolveIssueMeta(type).tone || 'neutral';
      const rank = ISSUE_TONE_RANK[tone] != null ? ISSUE_TONE_RANK[tone] : ISSUE_TONE_RANK.neutral;
      if (rank > best){
        best = rank;
        selected = tone;
      }
    });
    return selected;
  }

  function createIssueChip(type, stats){
    const meta = resolveIssueMeta(type);
    const tone = meta.tone || 'neutral';
    const styles = ISSUE_TONE_STYLES[tone] || ISSUE_TONE_STYLES.neutral;
    const chip = document.createElement('span');
    chip.className = 'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold shadow-sm';
    chip.style.background = styles.chipBg;
    chip.style.color = styles.chipText;
    chip.style.border = `1px solid ${styles.chipBorder}`;
    const total = typeof stats === 'number' ? stats : Number((stats && stats.total) || 0);
    const unique = (stats && typeof stats === 'object' && stats.uniqueTeams != null) ? Number(stats.uniqueTeams) : Number.NaN;
    const sameCount = Number.isFinite(unique) && unique > 0 && unique !== total ? ` (${unique} team${unique > 1 ? 's' : ''})` : '';
    chip.textContent = `${meta.label}: ${total}${sameCount}`;
    const teamNames = (stats && typeof stats === 'object' && Array.isArray(stats.teamNames)) ? stats.teamNames : [];
    const tooltipParts = [meta.description];
    if (teamNames.length){
      const display = teamNames.slice(0, 8);
      const remaining = teamNames.length - display.length;
      if (remaining > 0){
        display.push(`…+${remaining}`);
      }
      tooltipParts.push(`Teams: ${display.join(', ')}`);
    }
    chip.title = tooltipParts.filter(Boolean).join('\n');
    return chip;
  }

  function createIssueCard(item, version){
    const group = item.group || {};
    const issueTypes = Array.isArray(item.issues) ? item.issues : [];
    const tone = toneForIssues(issueTypes);
    const styles = ISSUE_TONE_STYLES[tone] || ISSUE_TONE_STYLES.neutral;
    const card = document.createElement('div');
    card.className = 'issue-item rounded-xl border p-3 text-xs space-y-2 shadow-sm';
    card.style.background = styles.cardBg;
    card.style.borderColor = styles.cardBorder;
    card.style.borderLeftWidth = '4px';
    card.style.borderLeftStyle = 'solid';
    card.style.borderLeftColor = styles.accent;

    const phase = document.createElement('div');
    phase.className = 'font-semibold text-[#1f2937] uppercase tracking-wide text-[11px]';
    phase.textContent = group.phase ? group.phase : 'Unknown phase';
    card.appendChild(phase);

    const hostName = getTeamLabel(group.host_team_id, version);
    const hostLine = document.createElement('div');
    hostLine.className = 'text-[#1f2937]';
    hostLine.innerHTML = `<span class="font-medium">Host</span>: ${hostName}`;
    card.appendChild(hostLine);

    const guestNames = (group.guest_team_ids || []).map(id=> getTeamLabel(id, version));
    const guestLine = document.createElement('div');
    guestLine.className = 'text-[#334155]';
    guestLine.innerHTML = `<span class="font-medium">Guests</span>: ${guestNames.length ? guestNames.join(', ') : '—'}`;
    card.appendChild(guestLine);

    if (issueTypes.length){
      const list = document.createElement('ul');
      list.style.paddingLeft = '16px';
      list.style.color = '#334155';
      list.style.marginTop = '4px';
      issueTypes.forEach(type=>{
        const meta = resolveIssueMeta(type);
        const li = document.createElement('li');
        const base = document.createElement('div');
        base.textContent = `${meta.label} – ${meta.description}`;
        li.appendChild(base);
        const actorEntries = (item.actors && item.actors[type]) || [];
        const details = [];
        actorEntries.forEach(entry=>{
          if (entry && Array.isArray(entry.pair)){
            const names = entry.pair.map(id=> getTeamLabel(id, version));
            const count = entry.total ? ` (${entry.total} encounters)` : '';
            details.push(`${names.join(' ↔ ')}${count}`);
            return;
          }
          if (entry && entry.team_id){
            const name = getTeamLabel(entry.team_id, version);
            let label = entry.role === 'host' ? `Host ${name}` : (entry.role === 'guest' ? `Guest ${name}` : name);
            if (Array.isArray(entry.allergies) && entry.allergies.length){
              label += ` – ${entry.allergies.join(', ')}`;
            }
            if (entry.warning){
              label += ` (${String(entry.warning).replace(/_/g, ' ')})`;
            }
            if (Array.isArray(entry.missing_phases) && entry.missing_phases.length){
              label += ` – Missing phases: ${entry.missing_phases.join(', ')}`;
            }
            if (Array.isArray(entry.missing_emails) && entry.missing_emails.length){
              label += ` – Missing participants: ${entry.missing_emails.join(', ')}`;
            }
            if (Array.isArray(entry.missing_unit_ids) && entry.missing_unit_ids.length){
              label += ` – Units: ${entry.missing_unit_ids.join(', ')}`;
            }
            details.push(label);
            return;
          }
        });
        if (details.length){
          const sub = document.createElement('ul');
          sub.className = 'ml-4 list-disc text-[#1f2937]';
          details.forEach(text=>{
            const subLi = document.createElement('li');
            subLi.textContent = text;
            sub.appendChild(subLi);
          });
          li.appendChild(sub);
        }
        list.appendChild(li);
      });
      card.appendChild(list);
    }

    if (Array.isArray(group.warnings) && group.warnings.length){
      const warn = document.createElement('div');
      warn.className = 'text-[#ca8a04]';
      warn.textContent = `Warnings: ${group.warnings.join(', ')}`;
      card.appendChild(warn);
    }

    return card;
  }

  function groupsByPhase(){
    const by = { appetizer: [], main: [], dessert: [] };
    detailsGroups.forEach((g, idx)=>{ by[g.phase] = by[g.phase] || []; by[g.phase].push({ ...g, _idx: idx }); });
    return by;
  }

  function renderMatchDetailsBoard(){
    const box = $('#match-details');
    const msg = $('#match-details-msg');
    if (!box || !msg) return;

    if (!detailsVersion){
      box.innerHTML = '';
      msg.textContent = 'No proposal loaded yet.';
      removeSyntheticManagementPanel();
      ['appetizer','main','dessert'].forEach((phase)=>{
        const panel = document.getElementById(`unplaced-${phase}-panel`);
        if (panel){
          panel.classList.add('hidden');
          panel.classList.remove('visible');
        }
      });
      return;
    }

    refreshSplitMemberStates();

    const phases = ['appetizer','main','dessert'];
    const by = groupsByPhase();

    box.innerHTML = '';
    msg.textContent = unsaved
      ? 'You have unsaved changes. Metrics reflect the current preview and are not saved yet.'
      : '';

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

    const remoteMetrics = (detailsMetrics && typeof detailsMetrics === 'object') ? detailsMetrics : {};
    const localMetrics = calculateLocalMetrics();

    const totalParticipants = Number.isFinite(localMetrics.total_participant_count)
      ? localMetrics.total_participant_count
      : (Number.isFinite(remoteMetrics.total_participant_count) ? Number(remoteMetrics.total_participant_count) : null);
    const assignedParticipants = Number.isFinite(localMetrics.assigned_participant_count)
      ? localMetrics.assigned_participant_count
      : (Number.isFinite(remoteMetrics.assigned_participant_count) ? Number(remoteMetrics.assigned_participant_count) : null);

    const phaseSummaryRemote = remoteMetrics.phase_summary || {};
    const phaseSummaryLocal = localMetrics.phase_summary || {};
    const phaseSummary = phases.reduce((acc, phase)=>{
      acc[phase] = Object.assign({}, phaseSummaryRemote[phase] || {}, phaseSummaryLocal[phase] || {});
      return acc;
    }, {});

    const headline = document.createElement('div');
    headline.className = 'mb-4 flex flex-wrap items-center justify-between gap-3 text-sm text-[#1f2937]';

    const totalsBlock = document.createElement('div');
    totalsBlock.className = 'flex flex-wrap items-center gap-3';
    const totalLabel = document.createElement('div');
    totalLabel.className = 'flex items-baseline gap-2';
    totalLabel.innerHTML = `
      <span class="text-xs uppercase tracking-wide text-[#64748b]">Total participants</span>
      <span class="text-base font-semibold text-[#111827]">${totalParticipants != null ? totalParticipants : '—'}</span>`;
    totalsBlock.appendChild(totalLabel);
    const assignedLabel = document.createElement('div');
    assignedLabel.className = 'text-xs text-[#64748b]';
    assignedLabel.textContent = `Assigned: ${assignedParticipants != null ? assignedParticipants : '—'}`;
    totalsBlock.appendChild(assignedLabel);
    headline.appendChild(totalsBlock);

    if (unsaved){
      const notice = document.createElement('div');
      notice.className = 'text-xs text-[#b45309] font-medium';
      notice.textContent = 'Preview metrics include unsaved adjustments.';
      headline.appendChild(notice);
    }

    box.appendChild(headline);

    const summaryKeys = phases.filter(phase=> Object.prototype.hasOwnProperty.call(phaseSummary, phase));
    if (summaryKeys.length){
      const summaryGrid = document.createElement('div');
      summaryGrid.className = 'mb-4 grid gap-3 md:grid-cols-3';
      summaryKeys.forEach((phase)=>{
        const info = phaseSummary[phase] || {};
        const assignedVal = Number(info.assigned_participants);
        const expectedVal = Number(info.expected_participants);
        const assigned = Number.isFinite(assignedVal) ? assignedVal : null;
        const expected = Number.isFinite(expectedVal) ? expectedVal : null;
        const missingVal = Number(info.missing_participants);
        const missing = Number.isFinite(missingVal)
          ? missingVal
          : (expected != null && assigned != null ? Math.max(expected - assigned, 0) : null);
        const groupsCountVal = Number(info.group_count);
        const groupsCount = Number.isFinite(groupsCountVal) ? groupsCountVal : null;
        const unitsAssignedVal = Number(info.assigned_units);
        const unitsAssigned = Number.isFinite(unitsAssignedVal) ? unitsAssignedVal : null;
        const expectedUnitsVal = Number(info.expected_units);
        const expectedUnits = Number.isFinite(expectedUnitsVal) ? expectedUnitsVal : null;

        const card = document.createElement('div');
        const hasGap = (missing != null && missing > 0) || (expected != null && assigned != null && assigned < expected);
        card.className = hasGap
          ? 'rounded-xl border border-[#fecaca] bg-[#fef2f2] p-3 text-sm text-[#7f1d1d]'
          : 'rounded-xl border border-[#e2e8f0] bg-[#f8fafc] p-3 text-sm text-[#1f2937]';

        const title = document.createElement('div');
        title.className = 'uppercase text-[11px] font-semibold text-[#475569] tracking-wide';
        title.textContent = phase;
        card.appendChild(title);

        const participantsLine = document.createElement('div');
        participantsLine.className = 'mt-1 text-lg font-semibold';
        const assignedLabelText = assigned != null ? assigned : '?';
        const expectedLabelText = expected != null ? expected : '?';
        participantsLine.textContent = `${assignedLabelText}/${expectedLabelText} participants`;
        card.appendChild(participantsLine);

        const unitLine = document.createElement('div');
        unitLine.className = 'text-xs text-[#4a5568]';
        const unitsAssignedLabel = unitsAssigned != null ? unitsAssigned : '?';
        const expectedUnitsLabel = expectedUnits != null ? expectedUnits : '?';
        const groupLabel = groupsCount != null ? groupsCount : '?';
        unitLine.textContent = `Units: ${unitsAssignedLabel}/${expectedUnitsLabel} · Groups: ${groupLabel}`;
        card.appendChild(unitLine);

        if (hasGap){
          const gapLine = document.createElement('div');
          gapLine.className = 'mt-1 text-xs font-medium';
          const missingParticipants = missing != null ? Math.max(missing, 0) : null;
          gapLine.textContent = missingParticipants != null
            ? `${missingParticipants} missing participant${missingParticipants>1?'s':''}`
            : 'Participant coverage incomplete';
          card.appendChild(gapLine);
        }

        summaryGrid.appendChild(card);
      });
      box.appendChild(summaryGrid);
    }

    ensureSyntheticStyles();

    const legend = document.createElement('div');
    legend.className = 'flex flex-wrap gap-4 items-center text-[11px] bg-[#f8fafc] p-2 rounded-lg border border-[#e2e8f0]';
    legend.innerHTML = `
      <div class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-[#dc2626]"></span> unpaid</div>
      <div class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-[#16a34a]"></span> paid</div>
      <div class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-[#9ca3af]"></span> n/a</div>
      <div class="flex items-center gap-2"><span class="synthetic-indicator" style="width:16px;height:16px;font-size:10px;">✳</span> équipe synthétique (éditable)</div>`;
    box.appendChild(legend);

    phases.forEach(phase=>{
      const section = document.createElement('div');
      section.className = 'phase-section';
      section.id = `phase-section-${phase}`;
      section.innerHTML = `<div class="font-semibold mb-2 capitalize">${phase}</div>`;
      const wrap = document.createElement('div'); wrap.className = 'grid grid-cols-1 md:grid-cols-3 gap-3';
      (by[phase]||[]).forEach((g)=>{
        const card = document.createElement('div');
        card.className = 'p-3 rounded-xl border border-[#f0f4f7] bg-[#fcfcfd] group relative';

        const hostZone = document.createElement('div'); hostZone.className = 'host-zone mb-2 p-2 rounded border border-dashed bg-white/40';
        hostZone.dataset.phase = phase; hostZone.dataset.groupIdx = String(g._idx); hostZone.dataset.role = 'host';
        hostZone.innerHTML = '<div class="text-xs text-[#4a5568] mb-1 flex items-center justify-between"><span>Host</span></div>';
        const hostTeamId = g.host_team_id != null ? String(g.host_team_id) : null;
        const hostAllergies = normalizeList(Array.isArray(g.host_allergies) && g.host_allergies.length ? g.host_allergies : hostFallbackAllergies(hostTeamId));
        const hostCard = g.host_team_id ? renderTeamCard(String(g.host_team_id)) : null;
        if (hostCard){
          hostCard.dataset.phase = phase;
          hostCard.dataset.groupIdx = String(g._idx);
          hostCard.dataset.role = 'host';
          hostZone.appendChild(hostCard);
        }

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

        const guestZone = document.createElement('div'); guestZone.className = 'guest-zone p-2 rounded border border-dashed min-h-10 bg-white/40';
        guestZone.dataset.phase = phase; guestZone.dataset.groupIdx = String(g._idx); guestZone.dataset.role = 'guest';
        guestZone.innerHTML = '<div class="text-xs text-[#4a5568] mb-1">Guests</div>';
        (g.guest_team_ids||[]).forEach(tid=>{
          const t = renderTeamCard(String(tid));
          t.dataset.phase = phase;
          t.dataset.groupIdx = String(g._idx);
          t.dataset.role = 'guest';
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
      if (releaseBtn){
        releaseBtn.disabled = true;
        releaseBtn.classList.add('opacity-60');
        releaseBtn.title = 'Save changes before releasing';
      }
    }
    box.appendChild(ctrl);

    bindDnD();
    bindDetailsControls();
    bindTeamMapButtons();
    bindTeamNameButtons();
    bindSyntheticTeamButtons();
    fetchIssuesForDetails();
    updateUnplacedTeamsPanels();
    updateSyntheticManagementPanel();
  }

  function calculateLocalMetrics() {
    // Calculate metrics locally based on current detailsGroups state
    const metrics = {
      total_participant_count: 0,
      phase_summary: {
        appetizer: { 
          assigned_participants: 0, 
          expected_participants: 0, 
          missing_participants: 0,
          assigned_units: 0,
          expected_units: 0,
          group_count: 0
        },
        main: { 
          assigned_participants: 0, 
          expected_participants: 0, 
          missing_participants: 0,
          assigned_units: 0,
          expected_units: 0,
          group_count: 0
        },
        dessert: { 
          assigned_participants: 0, 
          expected_participants: 0, 
          missing_participants: 0,
          assigned_units: 0,
          expected_units: 0,
          group_count: 0
        }
      }
    };
    
    // Count total participants and units from REAL teams only (not synthetic pair:/split:)
    // Synthetic teams are just temporary groupings of solo participants
    // Also exclude original teams that have been split to avoid duplication
    const hiddenSingles = new Set(Object.entries(syntheticDrafts.singles || {}).filter(([, info])=>{
      if (!info || typeof info !== 'object') return false;
      // Cacher les équipes qui sont: 
      // 1. marquées comme non disponibles (status !== 'available')
      // 2. OU marquées comme équipes originales séparées
      return (info.status && info.status !== 'available') || info.originalTeamSplit === true;
    }).map(([teamId])=> String(teamId)));

    const allTeamIds = Object.keys(teamDetails).filter(tid => {
      // Only count real teams (not pair: or split: synthetic teams)
      return !tid.startsWith('pair:') && !tid.startsWith('split:');
    }).filter(tid => !hiddenSingles.has(String(tid)));
    
    let totalParticipants = 0;
    let totalUnits = 0;
    allTeamIds.forEach(tid => {
      const det = teamDetails[tid] || {};
      totalParticipants += (det.size || 1);
      totalUnits += 1; // Each team is 1 unit
    });
    metrics.total_participant_count = totalParticipants;
    
    // Each phase should have totalParticipants and totalUnits as expected
    ['appetizer', 'main', 'dessert'].forEach(phase => {
      metrics.phase_summary[phase].expected_participants = totalParticipants;
      metrics.phase_summary[phase].expected_units = totalUnits;
    });
    
    // Track which teams are placed (globally, not per phase)
    // A team placed in any phase counts as "assigned"
    const allPlacedTeams = new Set();
    
    // Count assigned participants and groups per phase from detailsGroups
    detailsGroups.forEach(g => {
      const phase = g.phase;
      if (!metrics.phase_summary[phase]) return;
      
      // Count this group
      metrics.phase_summary[phase].group_count += 1;
      
      // Count host participants (can be solo or synthetic)
      if (g.host_team_id) {
        const tid = String(g.host_team_id);
        const det = teamDetails[tid] || {};
        const size = det.size || 1;
        metrics.phase_summary[phase].assigned_participants += size;
        allPlacedTeams.add(tid);
      }
      
      // Count guests participants (can be solo or synthetic)
      (g.guest_team_ids || []).forEach(tid => {
        const gtid = String(tid);
        const det = teamDetails[gtid] || {};
        const size = det.size || 1;
        metrics.phase_summary[phase].assigned_participants += size;
        allPlacedTeams.add(gtid);
      });
    });
    
    // Now calculate assigned_units per phase
    // A unit is assigned to a phase if it appears in that phase (as host or guest)
    // IMPORTANT: For synthetic teams (pair:/split:), we need to count the underlying solo teams
    
    // Helper function to extract solo team IDs from synthetic IDs
    function extractSoloIdsFromSynthetic(syntheticId) {
      const soloIds = [];
      if (syntheticId.startsWith('pair:')) {
        // pair:email1@domain.com+email2@domain.com
        const emailsPart = syntheticId.substring(5); // Remove 'pair:'
        const emails = emailsPart.split('+').filter(e => e);
        
        // Try to find matching team IDs by email
        Object.keys(teamDetails).forEach(tid => {
          if (tid.startsWith('pair:') || tid.startsWith('split:')) return;
          
          const det = teamDetails[tid] || {};
          const members = det.members || [];
          
          // Check if any member email matches
          members.forEach(member => {
            const memberEmail = (member.email || '').toLowerCase();
            if (emails.some(e => e.toLowerCase().includes(memberEmail) || memberEmail.includes(e.toLowerCase()))) {
              soloIds.push(tid);
            }
          });
        });
      } else if (syntheticId.startsWith('split:')) {
        // split:email@domain.com
        const email = syntheticId.substring(6); // Remove 'split:'
        
        // Try to find matching team ID by email
        Object.keys(teamDetails).forEach(tid => {
          if (tid.startsWith('pair:') || tid.startsWith('split:')) return;
          
          const det = teamDetails[tid] || {};
          const members = det.members || [];
          
          members.forEach(member => {
            const memberEmail = (member.email || '').toLowerCase();
            if (email.toLowerCase().includes(memberEmail) || memberEmail.includes(email.toLowerCase())) {
              soloIds.push(tid);
            }
          });
        });
      }
      return soloIds;
    }
    
    ['appetizer', 'main', 'dessert'].forEach(phase => {
      const soloTeamsInPhase = new Set();
      
      detailsGroups.forEach(g => {
        if (g.phase !== phase) return;
        
        // Process host
        if (g.host_team_id) {
          const tid = String(g.host_team_id);
          if (tid.startsWith('pair:') || tid.startsWith('split:')) {
            // Extract and count underlying solo teams
            extractSoloIdsFromSynthetic(tid).forEach(soloId => soloTeamsInPhase.add(soloId));
          } else {
            // Regular team
            soloTeamsInPhase.add(tid);
          }
        }
        
        // Process guests
        (g.guest_team_ids || []).forEach(tid => {
          const gtid = String(tid);
          if (gtid.startsWith('pair:') || gtid.startsWith('split:')) {
            // Extract and count underlying solo teams
            extractSoloIdsFromSynthetic(gtid).forEach(soloId => soloTeamsInPhase.add(soloId));
          } else {
            // Regular team
            soloTeamsInPhase.add(gtid);
          }
        });
      });
      
      metrics.phase_summary[phase].assigned_units = soloTeamsInPhase.size;
    });
    
    // Calculate missing participants per phase
    ['appetizer', 'main', 'dessert'].forEach(phase => {
      const summary = metrics.phase_summary[phase];
      summary.missing_participants = Math.max(0, summary.expected_participants - summary.assigned_participants);
    });
    
    // Calculate total assigned participants by counting unique solo teams across ALL phases
    const allPlacedSoloTeams = new Set();
    
    detailsGroups.forEach(g => {
      // Process host
      if (g.host_team_id) {
        const tid = String(g.host_team_id);
        if (tid.startsWith('pair:') || tid.startsWith('split:')) {
          extractSoloIdsFromSynthetic(tid).forEach(soloId => allPlacedSoloTeams.add(soloId));
        } else {
          allPlacedSoloTeams.add(tid);
        }
      }
      
      // Process guests
      (g.guest_team_ids || []).forEach(tid => {
        const gtid = String(tid);
        if (gtid.startsWith('pair:') || gtid.startsWith('split:')) {
          extractSoloIdsFromSynthetic(gtid).forEach(soloId => allPlacedSoloTeams.add(soloId));
        } else {
          allPlacedSoloTeams.add(gtid);
        }
      });
    });
    
    // Count participants from these unique solo teams
    let totalAssignedParticipants = 0;
    allPlacedSoloTeams.forEach(tid => {
      const det = teamDetails[tid] || {};
      totalAssignedParticipants += (det.size || 1);
    });
    
    metrics.assigned_participant_count = totalAssignedParticipants;
    
    return metrics;
  }

  function updateUnplacedTeamsPanels(){
    // Recalculate metrics locally based on current detailsGroups
    const localMetrics = calculateLocalMetrics();
    const phaseSummary = localMetrics.phase_summary || {};
    
    // Collect team IDs that are placed BY PHASE (per phase tracking)
    const placedByPhase = {
      appetizer: new Set(),
      main: new Set(),
      dessert: new Set()
    };
    
    // Also track which solo teams are part of synthetic pairs/splits
    const solosInSyntheticTeams = new Set();
    
    detailsGroups.forEach(g => {
      const phase = g.phase;
      if (!placedByPhase[phase]) return;
      
      if (g.host_team_id) {
        const htid = String(g.host_team_id);
        placedByPhase[phase].add(htid);
        
        // If it's a synthetic ID, extract the solo team IDs
        if (htid.startsWith('pair:') || htid.startsWith('split:')) {
          extractSoloIdsFromSynthetic(htid).forEach(sid => solosInSyntheticTeams.add(sid));
        }
      }
      
      (g.guest_team_ids || []).forEach(tid => {
        const gtid = String(tid);
        placedByPhase[phase].add(gtid);
        
        // If it's a synthetic ID, extract the solo team IDs
        if (gtid.startsWith('pair:') || gtid.startsWith('split:')) {
          extractSoloIdsFromSynthetic(gtid).forEach(sid => solosInSyntheticTeams.add(sid));
        }
      });
    });

    // Helper function to extract solo team IDs from synthetic IDs
    function extractSoloIdsFromSynthetic(syntheticId) {
      const soloIds = [];
      if (syntheticId.startsWith('pair:')) {
        // pair:email1@domain.com+email2@domain.com
        const emailsPart = syntheticId.substring(5); // Remove 'pair:'
        const emails = emailsPart.split('+').filter(e => e);
        
        // Try to find matching team IDs by email
        Object.keys(teamDetails).forEach(tid => {
          if (tid.startsWith('pair:') || tid.startsWith('split:')) return;
          
          const det = teamDetails[tid] || {};
          const members = det.members || [];
          
          // Check if any member email matches
          members.forEach(member => {
            const memberEmail = (member.email || '').toLowerCase();
            if (emails.some(e => e.toLowerCase().includes(memberEmail) || memberEmail.includes(e.toLowerCase()))) {
              soloIds.push(tid);
            }
          });
        });
      } else if (syntheticId.startsWith('split:')) {
        // split:email@domain.com
        const email = syntheticId.substring(6); // Remove 'split:'
        
        // Try to find matching team ID by email
        Object.keys(teamDetails).forEach(tid => {
          if (tid.startsWith('pair:') || tid.startsWith('split:')) return;
          
          const det = teamDetails[tid] || {};
          const members = det.members || [];
          
          members.forEach(member => {
            const memberEmail = (member.email || '').toLowerCase();
            if (email.toLowerCase().includes(memberEmail) || memberEmail.includes(email.toLowerCase())) {
              soloIds.push(tid);
            }
          });
        });
      }
      return soloIds;
    }

    // Consider all units (real teams and synthetic pair:/split: units)
    const allTeamIds = Object.keys(teamDetails);
    
    // For each phase, find teams that are NOT placed in THAT specific phase
    const unplacedByPhase = {
      appetizer: [],
      main: [],
      dessert: []
    };

    ['appetizer', 'main', 'dessert'].forEach(phase => {
      allTeamIds.forEach(tid => {
        const det = teamDetails[tid] || {};
        
        // A team is "unplaced" in this phase if:
        // 1. It's NOT in this phase's placed list
        // 2. It's NOT part of a synthetic pair/split that is placed
        // 3. It's NOT an original team that has been split (to avoid duplicates)
        const isPlacedInThisPhase = placedByPhase[phase].has(tid);
        const isPartOfSyntheticTeam = solosInSyntheticTeams.has(tid);
        
        // Vérifier si c'est une équipe originale qui a été séparée
        const singleInfo = syntheticDrafts.singles[tid];
        const isOriginalTeamSplit = singleInfo && singleInfo.originalTeamSplit === true;
        
        // Always surface missing participants, regardless of original course preference.
        // Mais exclure les équipes originales qui ont été séparées
        if (!isPlacedInThisPhase && !isPartOfSyntheticTeam && !isOriginalTeamSplit) {
          unplacedByPhase[phase].push(tid);
        }
      });
    });

    // Update/create each panel outside of sections (in body)
    ['appetizer', 'main', 'dessert'].forEach(phase => {
      // Try to find existing panel or create new one
      let panel = $(`#unplaced-${phase}-panel`);
      
      if (!panel) {
        panel = document.createElement('div');
        panel.id = `unplaced-${phase}-panel`;
        panel.className = 'floating-panel';
        panel.dataset.phase = phase;
        
        const icons = { appetizer: '🥗', main: '🍖', dessert: '🍰' };
        panel.innerHTML = `
          <div class="floating-panel-header">
            <span>${icons[phase]} ${phase.charAt(0).toUpperCase() + phase.slice(1)} - Unplaced</span>
            <span id="unplaced-${phase}-count" class="bg-white/20 px-2 py-0.5 rounded-full text-xs">0</span>
          </div>
          <div id="unplaced-${phase}-content" class="floating-panel-content">
            <div class="text-xs text-gray-500 text-center py-4">No unplaced teams</div>
          </div>
        `;
        
        document.body.appendChild(panel);
      }
      
      const content = panel.querySelector(`#unplaced-${phase}-content`);
      const count = panel.querySelector(`#unplaced-${phase}-count`);
      
      if (!content || !count) return;
      
      // Check if there are missing participants according to metrics
      const phaseInfo = phaseSummary[phase] || {};
      const missingParticipants = Number(phaseInfo.missing_participants || 0);
      const expectedUnits = Number(phaseInfo.expected_units || 0);
      const assignedUnits = Number(phaseInfo.assigned_units || 0);
      const missingUnits = Math.max(0, expectedUnits - assignedUnits);
      
      let teams = unplacedByPhase[phase];
      
      // Only show panel if there are actually missing participants/units
      if (missingParticipants === 0 && missingUnits === 0) {
        content.innerHTML = '<div class="text-xs text-gray-500 text-center py-4">No missing participants</div>';
        count.textContent = '0';
        panel.classList.add('hidden');
      } else if (teams.length === 0) {
        // There are missing participants but no unplaced teams available
        content.innerHTML = `<div class="text-xs text-gray-500 text-center py-4">${missingParticipants} participant(s) missing<br/>No unplaced teams available</div>`;
        count.textContent = String(missingParticipants);
        panel.classList.add('hidden'); // Hide if no teams to show
      } else {
        // Calculate how many teams we need to show based on missing participants
        // We need to fill exactly the missing participants count
        let participantsToFill = missingParticipants;
        const teamsToShow = [];
        
        for (let i = 0; i < teams.length && participantsToFill > 0; i++) {
          const tid = teams[i];
          const det = teamDetails[tid] || {};
          const teamSize = det.size || 1;
          
          teamsToShow.push(tid);
          participantsToFill -= teamSize;
        }
        
        // If no teams needed or available, hide panel
        if (teamsToShow.length === 0) {
          count.textContent = `${missingParticipants} missing`;
          content.innerHTML = `<div class="text-xs text-gray-500 text-center py-4">${missingParticipants} participant(s) missing<br/>Aucun candidat disponible actuellement</div>`;
          panel.classList.remove('hidden');
        } else {
          // Show only the teams needed to fill missing participants
          count.textContent = `${missingParticipants} missing`;
          content.innerHTML = '';

          teamsToShow.forEach(tid => {
            // Use the existing renderTeamCard function for consistent styling
            const teamCard = renderTeamCard(tid);

            // Add unplaced-specific data attributes for drag and drop
            teamCard.dataset.phase = phase;
            teamCard.dataset.groupIdx = '-1';
            teamCard.dataset.role = 'unplaced';

            content.appendChild(teamCard);
          });

          const remainingParticipants = Math.max(participantsToFill, 0);
          if (remainingParticipants > 0) {
            const note = document.createElement('div');
            note.className = 'mt-2 text-[11px] text-[#b91c1c] text-center';
            note.textContent = `${remainingParticipants} participant(s) supplémentaires restent à placer`;
            content.appendChild(note);
          }

          panel.classList.remove('hidden');
        }
      }
    });
    
    // Update panel positions based on scroll
    updateFloatingPanelPositions();

    // Refresh synthetic management panel alongside unplaced data
    updateSyntheticManagementPanel();
  }

  function updateFloatingPanelPositions() {
    const phases = ['appetizer', 'main', 'dessert'];
    const headerHeight = 120; // Approximate header + nav height
    const topOffset = 20; // Top margin when sticky
    
    phases.forEach(phase => {
      const panel = $(`#unplaced-${phase}-panel`);
      const section = $(`#phase-section-${phase}`);
      
      if (!panel || !section || panel.classList.contains('hidden')) {
        if (panel) panel.classList.remove('visible');
        return;
      }
      
      const sectionRect = section.getBoundingClientRect();
      const panelHeight = panel.offsetHeight;
      const viewportHeight = window.innerHeight;
      
      // Check if section is in viewport
      const sectionTop = sectionRect.top;
      const sectionBottom = sectionRect.bottom;
      const isInView = sectionBottom > headerHeight && sectionTop < viewportHeight;
      
      if (isInView) {
        panel.classList.add('visible');
        
        // Calculate panel position
        let panelTop;
        
        if (sectionTop > headerHeight) {
          // Section is below fold, panel should be at section top
          panelTop = sectionTop;
        } else if (sectionBottom < headerHeight + panelHeight + topOffset) {
          // Section is scrolling out of view, panel should stick to section bottom
          panelTop = sectionBottom - panelHeight;
        } else {
          // Section is in view, panel should be sticky at top
          panelTop = headerHeight + topOffset;
        }
        
        panel.style.top = `${Math.max(headerHeight, panelTop)}px`;
      } else {
        panel.classList.remove('visible');
      }
    });
  }

  // Setup scroll listener for floating panels
  let scrollTimeout;
  function setupFloatingPanelScrollListener() {
    window.addEventListener('scroll', () => {
      if (scrollTimeout) clearTimeout(scrollTimeout);
      scrollTimeout = setTimeout(updateFloatingPanelPositions, 10);
    }, { passive: true });
    
    window.addEventListener('resize', () => {
      if (scrollTimeout) clearTimeout(scrollTimeout);
      scrollTimeout = setTimeout(updateFloatingPanelPositions, 10);
    }, { passive: true });
  }

  function removeSyntheticManagementPanel(){
    const panel = document.getElementById('synthetic-management-panel');
    if (panel){
      panel.remove();
    }
    syntheticDrafts.splits = {};
    syntheticDrafts.splitMembers = {};
    syntheticDrafts.createStage = [];
    syntheticDrafts.createdPairs = [];
    syntheticDrafts.singles = {};
    cleanupTemporarySyntheticTeams();
  }

  function renderSyntheticMemberList(det){
    const members = Array.isArray(det.members) ? det.members : [];
    if (!members.length) return null;
    const list = document.createElement('ul');
    list.className = 'text-xs text-[#4a5568] list-disc pl-4 space-y-1';
    members.forEach(member => {
      const item = document.createElement('li');
      item.textContent = formatMemberName(member) || (member.email || 'Participant');
      list.appendChild(item);
    });
    return list;
  }

  function syntheticPanelZoneFromEvent(ev){
    if (!ev || !ev.target || typeof ev.target.closest !== 'function') return null;
    return ev.target.closest('.synthetic-drop-zone');
  }

  function handleSyntheticPanelDragOver(ev){
    const zone = syntheticPanelZoneFromEvent(ev);
    if (!zone) return;
    ev.preventDefault();
    zone.classList.add('drag-active');
    try { ev.dataTransfer.dropEffect = 'move'; } catch (_) {}
  }

  function handleSyntheticPanelDragLeave(ev){
    const zone = syntheticPanelZoneFromEvent(ev);
    if (!zone) return;
    zone.classList.remove('drag-active');
  }

  function handleSyntheticPanelDrop(ev){
    const zone = syntheticPanelZoneFromEvent(ev);
    if (!zone) return;
    ev.preventDefault();
    zone.classList.remove('drag-active');
    const action = zone.dataset.action;
    if (!action) return;
    const raw = (ev.dataTransfer && ev.dataTransfer.getData) ? ev.dataTransfer.getData('text/plain') : '';
    const tid = raw ? String(raw).trim() : '';
    if (!tid) return;

    if (action === 'split'){
      const entry = ensureSyntheticSplitDraft(tid);
      if (!entry){
        toast("Impossible de préparer cette équipe (participants manquants).", { type: 'warning' });
        return;
      }
      // IMPORTANT: Retire l'équipe originale de tous les groupes pour éviter les doublons
      const removed = removeTeamFromGroups(tid);
      if (removed){
        unsaved = true;
      }
      
      // Marque l'équipe originale comme "processed" pour ne plus l'afficher dans les panneaux unplaced
      // On crée une entrée dans singles avec status 'paired' pour la cacher
      syntheticDrafts.singles[tid] = { status: 'paired', originalTeamSplit: true };
      
      refreshSplitMemberStates();
      renderMatchDetailsBoard();
      updateSyntheticManagementPanel();
      toast('Participants disponibles pour une nouvelle combinaison.', { type: 'success' });
      return;
    }

    if (action === 'create'){
      let stageId = tid;
      if (isManagedSplitId(stageId)){
        const status = getSplitMemberStatus(stageId);
        if (status === 'paired'){
          toast('Ce participant est déjà utilisé dans une nouvelle équipe.', { type: 'info' });
          return;
        }
        if (syntheticDrafts.createStage.includes(stageId)){
          toast('Participant déjà présent dans la zone de création.', { type: 'info' });
          return;
        }
        if (status === 'placed'){
          const removed = removeTeamFromGroups(stageId);
          if (removed){
            unsaved = true;
            renderMatchDetailsBoard();
            refreshSplitMemberStates();
          }
        }
        syntheticDrafts.createStage.push(stageId);
        setSplitMemberStatus(stageId, 'staged');
      } else {
        const source = teamDetails[stageId];
        if (!source){
          toast('Impossible de trouver ce participant.', { type: 'warning' });
          return;
        }
        const memberCount = Array.isArray(source.members) ? source.members.length : (source.size || 0) || 0;
        if (memberCount > 1){
          toast('Séparez d’abord cette équipe pour récupérer des participants individuels.', { type: 'info' });
          return;
        }
        const singleStatus = (syntheticDrafts.singles[stageId] && syntheticDrafts.singles[stageId].status) || 'available';
        if (singleStatus === 'paired'){
          toast('Ce participant est déjà utilisé dans une nouvelle équipe.', { type: 'info' });
          return;
        }
        if (syntheticDrafts.createStage.includes(stageId)){
          toast('Participant déjà présent dans la zone de création.', { type: 'info' });
          return;
        }
        if (isTeamIdPlaced(stageId)){
          const removed = removeTeamFromGroups(stageId);
          if (removed){
            unsaved = true;
            renderMatchDetailsBoard();
            refreshSplitMemberStates();
          }
        }
        syntheticDrafts.singles[stageId] = { status: 'staged' };
        syntheticDrafts.createStage.push(stageId);
      }

      updateSyntheticManagementPanel();
      toast('Participant ajouté à la zone de création.', { type: 'success' });
      if (syntheticDrafts.createStage.length >= 2){
        createPairFromStage();
      }
      return;
    }
  }

  function updateSyntheticManagementPanel(){
    if (!detailsVersion){
      removeSyntheticManagementPanel();
      return;
    }
    ensureSyntheticStyles();
    refreshSplitMemberStates();

    const syntheticIds = Object.keys(teamDetails || {}).filter(id => id.startsWith('pair:') || id.startsWith('split:'));
    const pairIds = syntheticIds.filter(id => id.startsWith('pair:')).sort();
    const splitIds = syntheticIds.filter(id => id.startsWith('split:')).sort();
    const singleDraftIds = Object.keys(syntheticDrafts.singles || {});

    let panel = document.getElementById('synthetic-management-panel');
    if (!panel){
      panel = document.createElement('div');
      panel.id = 'synthetic-management-panel';
      panel.className = 'floating-panel synthetic-tools-panel';
      document.body.appendChild(panel);
    }
    panel.classList.remove('hidden');
    panel.classList.add('visible');
    panel.style.top = '120px';
    panel.style.left = '20px';
    panel.style.right = 'auto';
    panel.style.width = '300px';
    panel.style.maxHeight = '75vh';
    panel.style.overflowY = 'auto';

    const header = document.createElement('div');
    header.className = 'floating-panel-header';
    header.innerHTML = '<span>Gestion des équipes synthétiques</span>';

    const content = document.createElement('div');
    content.className = 'floating-panel-content space-y-4';

    const dropZones = document.createElement('div');
    dropZones.className = 'space-y-3';

    const splitZone = document.createElement('div');
    splitZone.className = 'synthetic-drop-zone';
    splitZone.dataset.action = 'split';
    splitZone.innerHTML = `
      <div class="synthetic-panel-section-title">Séparation</div>
      <div class="synthetic-drop-hint">Glissez ici une équipe de plusieurs personnes pour préparer une séparation instantanée.</div>
    `;
    ['dragenter', 'dragover'].forEach(evt => splitZone.addEventListener(evt, handleSyntheticPanelDragOver));
    splitZone.addEventListener('dragleave', handleSyntheticPanelDragLeave);
    splitZone.addEventListener('drop', handleSyntheticPanelDrop);
    dropZones.appendChild(splitZone);

    const createZone = document.createElement('div');
    createZone.className = 'synthetic-drop-zone';
    createZone.dataset.action = 'create';
    createZone.innerHTML = `
      <div class="synthetic-panel-section-title">Création</div>
      <div class="synthetic-drop-hint">Glissez ici un participant individuel (séparé ou déjà seul) pour former une nouvelle équipe.</div>
      <div class="synthetic-create-stage"></div>
    `;
    ['dragenter', 'dragover'].forEach(evt => createZone.addEventListener(evt, handleSyntheticPanelDragOver));
    createZone.addEventListener('dragleave', handleSyntheticPanelDragLeave);
    createZone.addEventListener('drop', handleSyntheticPanelDrop);
    const stageContainer = createZone.querySelector('.synthetic-create-stage');
    renderSyntheticCreateStage(stageContainer);
    dropZones.appendChild(createZone);

    content.appendChild(dropZones);

    const availableSection = document.createElement('div');
    availableSection.className = 'space-y-2';
    availableSection.innerHTML = `<div class="synthetic-panel-section-title">Participants disponibles</div>`;
    const availableList = document.createElement('div');
    availableList.className = 'synthetic-available-list';
    renderSyntheticAvailableParticipants(availableList);
    availableSection.appendChild(availableList);
    content.appendChild(availableSection);

    const availability = document.createElement('div');
    availability.className = 'text-[11px] text-[#64748b] bg-[#f8fafc] rounded-lg border border-[#e2e8f0] p-3';
    const availableParticipantCount = listAvailableSyntheticParticipants().length;
    availability.innerHTML = `
      <div class="synthetic-panel-section-title">Disponibles</div>
      <div>Équipes synthétiques : ${pairIds.length}</div>
      <div>Participants séparés : ${splitIds.length}</div>
      <div>Participants solos suivis : ${singleDraftIds.length}</div>
      <div>Participants disponibles : ${availableParticipantCount}</div>
      <div>En préparation (zone de création) : ${syntheticDrafts.createStage.length}</div>
    `;
    content.appendChild(availability);

    panel.innerHTML = '';
    panel.appendChild(header);
    panel.appendChild(content);
  }

  const participantsModule = (function(){
    const state = {
      eventId: null,
      rows: [],
      summary: { total: 0, by_payment_status: {}, by_registration_status: {} },
      loading: false,
      visible: true,
      sortKey: 'last_name',
      sortDir: 'asc',
      search: '',
    };
    let initialized = false;
    const selectors = {
      section: '#participants-section',
      select: '#participants-event-select',
      search: '#participants-search',
      refresh: '#participants-refresh',
      toggle: '#participants-toggle',
      loading: '#participants-loading',
      wrapper: '#participants-table-wrapper',
      tbody: '#participants-tbody',
      empty: '#participants-empty',
      count: '#participants-count',
      summary: '#participants-summary',
      headers: '#participants-section th.sortable',
    };
    const PAYMENT_LABELS = {
      paid: 'Payé',
      pending: 'En attente',
      pending_payment: 'En attente',
      covered_by_team: "Payé par l'équipe",
      failed: 'Échec',
      not_applicable: 'N/A',
      unpaid: 'Non payé',
      unknown: 'Inconnu',
    };
    const PAYMENT_BADGES = {
      paid: 'bg-[#bbf7d0] text-[#166534]',
      pending: 'bg-[#fef3c7] text-[#92400e]',
      pending_payment: 'bg-[#fef3c7] text-[#92400e]',
      covered_by_team: 'bg-[#dbeafe] text-[#1d4ed8]',
      failed: 'bg-[#fee2e2] text-[#b91c1c]',
      not_applicable: 'bg-[#e2e8f0] text-[#334155]',
      unpaid: 'bg-[#e2e8f0] text-[#334155]',
      unknown: 'bg-[#e2e8f0] text-[#334155]',
    };
    const GENDER_LABELS = {
      female: 'Femme',
      male: 'Homme',
      non_binary: 'Non binaire',
      diverse: 'Divers',
      other: 'Autre',
      prefer_not_to_say: 'Non précisé',
    };
    const TEAM_ROLE_LABELS = {
      creator: 'Capitaine',
      partner: 'Partenaire',
    };
    const REGISTRATION_LABELS = {
      confirmed: 'Confirmé',
      pending: 'En attente',
      pending_payment: 'En attente de paiement',
      invited: 'Invité',
      paid: 'Payé',
      refunded: 'Remboursé',
      cancelled_by_user: 'Annulé (participant)',
      cancelled_admin: 'Annulé (admin)',
      expired: 'Expiré',
      draft: 'Brouillon',
    };

    function init(){
      if (initialized) return;
      const select = $(selectors.select);
      if (select){
        select.addEventListener('change', (event)=>{
          state.eventId = event.target.value || null;
          fetchAndRender(true);
        });
      }
      const searchInput = $(selectors.search);
      if (searchInput){
        searchInput.addEventListener('input', (event)=>{
          state.search = (event.target.value || '').trim();
          render();
        });
      }
      const refreshBtn = $(selectors.refresh);
      if (refreshBtn){
        refreshBtn.addEventListener('click', (event)=>{
          event.preventDefault();
          fetchAndRender(true);
        });
      }
      const toggleBtn = $(selectors.toggle);
      if (toggleBtn){
        toggleBtn.addEventListener('click', (event)=>{
          event.preventDefault();
          state.visible = !state.visible;
          toggleBtn.textContent = state.visible ? 'Masquer' : 'Afficher';
          render();
        });
      }
      const section = $(selectors.section);
      if (section){
        const head = section.querySelector('thead');
        if (head){
          head.addEventListener('click', (event)=>{
            const th = event.target.closest('th.sortable');
            if (!th) return;
            const key = th.dataset.sort;
            if (!key) return;
            if (state.sortKey === key){
              state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
            } else {
              state.sortKey = key;
              state.sortDir = 'asc';
            }
            render();
          });
        }
      }
      initialized = true;
    }

    function setLoading(active){
      const el = $(selectors.loading);
      if (!el) return;
      if (active){
        el.classList.remove('hidden');
      } else {
        el.classList.add('hidden');
      }
    }

    function sortValue(row, key){
      const value = row[key];
      if (key === 'updated_display' || key === 'updated_at' || key === 'created_at'){
        return value ? new Date(value).getTime() : null;
      }
      if (typeof value === 'string'){
        return value.toLowerCase();
      }
      if (value === null || value === undefined){
        return null;
      }
      return value;
    }

    function applyFilters(){
      const rows = state.rows.slice();
      const needle = state.search ? state.search.toLowerCase() : '';
      let filtered = rows;
      if (needle){
        filtered = rows.filter((row)=> row.search_blob.includes(needle));
      }
      filtered.sort((a, b)=>{
        const va = sortValue(a, state.sortKey);
        const vb = sortValue(b, state.sortKey);
        if (va === vb) return 0;
        if (va === null || va === undefined) return state.sortDir === 'asc' ? -1 : 1;
        if (vb === null || vb === undefined) return state.sortDir === 'asc' ? 1 : -1;
        if (va < vb) return state.sortDir === 'asc' ? -1 : 1;
        if (va > vb) return state.sortDir === 'asc' ? 1 : -1;
        return 0;
      });
      return filtered;
    }

    function formatPaymentStatus(status){
      if (!status) return PAYMENT_LABELS.unknown;
      return PAYMENT_LABELS[status] || status;
    }

    function paymentBadgeClass(status){
      if (!status) return PAYMENT_BADGES.unknown;
      return PAYMENT_BADGES[status] || PAYMENT_BADGES.unknown;
    }

    function formatGender(value){
      if (!value) return '';
      return GENDER_LABELS[value] || value;
    }

    function formatRegistrationStatus(status){
      if (!status) return '';
      return REGISTRATION_LABELS[status] || status;
    }

    function formatTeamRole(row){
      if (!row.team_id){
        return 'Solo';
      }
      if (!row.team_role){
        return '';
      }
      return TEAM_ROLE_LABELS[row.team_role] || row.team_role;
    }

    function updateSortHeaders(){
      const headers = document.querySelectorAll(selectors.headers);
      headers.forEach((th)=>{
        const key = th.dataset.sort;
        if (!key){
          th.removeAttribute('aria-sort');
          return;
        }
        if (key === state.sortKey){
          th.setAttribute('aria-sort', state.sortDir === 'asc' ? 'ascending' : 'descending');
        } else {
          th.setAttribute('aria-sort', 'none');
        }
      });
    }

    function updateCount(filteredLength, el){
      if (!el) return;
      const total = state.summary.total || state.rows.length;
      if (!total){
        el.textContent = '0 participant';
        return;
      }
      if (filteredLength === total){
        el.textContent = total === 1 ? '1 participant' : `${total} participants`;
      } else {
        el.textContent = `${filteredLength} / ${total} participants`;
      }
    }

    function updateSummary(el){
      if (!el) return;
      const entries = Object.entries(state.summary.by_payment_status || {});
      if (!entries.length){
        el.textContent = '';
        return;
      }
      const parts = entries.map(([status, count])=> `${formatPaymentStatus(status)} (${count})`);
      el.textContent = `Paiements : ${parts.join(' · ')}`;
    }

    function renderRow(row){
      const lastName = escapeHtml(row.last_name || '');
      const firstName = escapeHtml(row.first_name || '');
      const email = escapeHtml(row.email || '');
      const gender = escapeHtml(formatGender(row.gender));
      const registration = escapeHtml(formatRegistrationStatus(row.registration_status));
      const paymentLabel = escapeHtml(formatPaymentStatus(row.payment_status));
      const paymentClass = paymentBadgeClass(row.payment_status);
      const teamName = row.team_name ? escapeHtml(row.team_name) : (row.team_id ? '' : 'Solo');
      const teamRole = escapeHtml(formatTeamRole(row));
      const updatedRaw = row.updated_display || row.updated_at || row.created_at;
      const updated = updatedRaw ? escapeHtml(fmtDate(updatedRaw)) : '';
      return `\
<tr class="border-b border-[#f0f4f7] last:border-b-0">\
  <td class="p-2">${lastName}</td>\
  <td class="p-2">${firstName}</td>\
  <td class="p-2 font-medium text-[#1d4ed8]">${email}</td>\
  <td class="p-2">${gender}</td>\
  <td class="p-2">${registration}</td>\
  <td class="p-2"><span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold ${paymentClass}">${paymentLabel}</span></td>\
  <td class="p-2">${teamName || '—'}</td>\
  <td class="p-2">${teamRole}</td>\
  <td class="p-2">${updated}</td>\
</tr>`;
    }

    function render(){
      const wrapper = $(selectors.wrapper);
      const tbody = $(selectors.tbody);
      const empty = $(selectors.empty);
      const countEl = $(selectors.count);
      const summaryEl = $(selectors.summary);
      if (!tbody) return;
      const filtered = applyFilters();
      if (state.visible){
        wrapper && wrapper.classList.remove('hidden');
        summaryEl && summaryEl.classList.remove('hidden');
      } else {
        wrapper && wrapper.classList.add('hidden');
        summaryEl && summaryEl.classList.add('hidden');
      }
      if (!filtered.length){
        tbody.innerHTML = '';
        empty && empty.classList.remove('hidden');
      } else {
        empty && empty.classList.add('hidden');
        tbody.innerHTML = filtered.map(renderRow).join('');
      }
      updateCount(filtered.length, countEl);
      updateSummary(summaryEl);
      updateSortHeaders();
    }

    async function fetchAndRender(force){
      if (!state.eventId){
        state.rows = [];
        state.summary = { total: 0, by_payment_status: {}, by_registration_status: {} };
        render();
        return;
      }
      if (state.loading && !force){
        return;
      }
      state.loading = true;
      setLoading(true);
      try {
        const res = await apiFetch(`/admin/events/${state.eventId}/participants`);
        if (!res.ok){
          const text = await res.text().catch(()=> 'Erreur');
          throw new Error(text || 'Erreur de chargement');
        }
        const data = await res.json().catch(()=> ({ participants: [], summary: { total: 0, by_payment_status: {}, by_registration_status: {} } }));
        const participants = Array.isArray(data.participants) ? data.participants : [];
        state.rows = participants.map((p)=>{
          const blob = [p.full_name, p.email, p.team_name, p.registration_status, p.payment_status]
            .filter(Boolean)
            .join(' ')
            .toLowerCase();
          return {
            ...p,
            updated_display: p.payment_updated_at || p.updated_at || p.created_at,
            search_blob: blob,
          };
        });
        state.summary = data.summary || { total: state.rows.length, by_payment_status: {}, by_registration_status: {} };
        if (!state.summary.total){
          state.summary.total = state.rows.length;
        }
        render();
      } catch (error){
        console.error('participants.fetch', error);
        toast('Impossible de charger les participants.', { type: 'error' });
      } finally {
        state.loading = false;
        setLoading(false);
      }
    }

    async function onEventsRefreshed(events){
      init();
      const hasEvents = Array.isArray(events) && events.length > 0;
      const select = $(selectors.select);
      const searchInput = $(selectors.search);
      const toggleBtn = $(selectors.toggle);
      const refreshBtn = $(selectors.refresh);

      if (select){
        if (hasEvents){
          const options = events.map((ev)=>{
            const value = escapeHtml(ev.id);
            const labelText = `${ev.title || 'Évènement'}${ev.date ? ` (${ev.date})` : ''}`;
            const label = escapeHtml(labelText);
            return `<option value="${value}">${label}</option>`;
          }).join('');
          select.innerHTML = options;
          if (state.eventId && events.some((ev)=> ev.id === state.eventId)){
            select.value = state.eventId;
          } else {
            select.value = events[0].id;
            state.eventId = events[0].id;
          }
          select.disabled = false;
        } else {
          select.innerHTML = '<option value="">Aucun évènement</option>';
          select.disabled = true;
          select.value = '';
          state.eventId = null;
        }
      }

      if (searchInput){
        searchInput.disabled = !hasEvents;
        if (!hasEvents){
          searchInput.value = '';
          state.search = '';
        }
      }

      if (toggleBtn){
        toggleBtn.disabled = !hasEvents;
        toggleBtn.textContent = state.visible ? 'Masquer' : 'Afficher';
      }

      if (refreshBtn){
        refreshBtn.disabled = !hasEvents;
      }

      if (hasEvents){
        await fetchAndRender(true);
      } else {
        state.rows = [];
        state.summary = { total: 0, by_payment_status: {}, by_registration_status: {} };
        render();
      }
    }

    return {
      init,
      onEventsRefreshed,
      fetch: fetchAndRender,
    };
  })();

  async function fetchIssuesForDetails(){
    try {
      if (!detailsVersion) return;
      const evId = $('#matching-event-select').value; if (!evId) return;
      const res = await apiFetch(`/matching/${evId}/issues?version=${detailsVersion}`);
      if (!res.ok) return;
      const data = await res.json().catch(()=>null); if (!data) return;
      const groups = data.issues || [];
      let missing = 0, partial = 0, cancelled = 0, incomplete = 0, duplicates = 0, allergies = 0, registrationMissing = 0, phaseGaps = 0;
      groups.forEach(g=>{
        const counts = g.issue_counts || {};
        missing += Number(counts.payment_missing || 0);
        partial += Number(counts.payment_partial || 0);
        cancelled += Number(counts.faulty_team_cancelled || 0);
        incomplete += Number(counts.team_incomplete || 0);
        duplicates += Number(counts.duplicate_pair || 0);
        allergies += Number(counts.uncovered_allergy || 0);
        registrationMissing += Number(counts.registration_missing || 0);
        phaseGaps += Number(counts.phase_participation_gap || 0);
      });
      const el = $('#details-issues');
      const parts = [];
      if (missing) parts.push(`${missing} missing payment`);
      if (partial) parts.push(`${partial} partial payment`);
      if (cancelled) parts.push(`${cancelled} cancelled team`);
      if (incomplete) parts.push(`${incomplete} incomplete team`);
      if (duplicates) parts.push(`${duplicates} duplicate encounter`);
      if (allergies) parts.push(`${allergies} uncovered allergy`);
      if (registrationMissing) parts.push(`${registrationMissing} missing registration${registrationMissing>1?'s':''}`);
      if (phaseGaps) parts.push(`${phaseGaps} phase participation gap${phaseGaps>1?'s':''}`);
      el.textContent = parts.length ? parts.join(' · ') : 'No outstanding issues.';
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
      if (data.metrics && typeof data.metrics === 'object'){
        detailsMetrics = data.metrics;
      }
      renderMatchDetailsBoard();
    } catch (e) {}
  }

  function bindDnD(){
    const root = $('#match-details');
    const dragData = { teamId: null, fromPhase: null, fromGroupIdx: null, role: null };
    if (!root.dataset.dndBound){
      // dragstart on cards (in match details)
      root.addEventListener('dragstart', (e)=>{
        const card = e.target.closest('.team-card'); if (!card || !root.contains(card)) return;
        dragData.teamId = card.dataset.teamId;
        dragData.fromPhase = card.dataset.phase; dragData.fromGroupIdx = Number(card.dataset.groupIdx);
        dragData.role = card.dataset.role;
        try { e.dataTransfer.setData('text/plain', dragData.teamId || ''); } catch(_) {}
        e.dataTransfer.effectAllowed = 'move';
      }, true);
      
      // dragstart on cards in floating panels (global listener)
      document.body.addEventListener('dragstart', (e)=>{
        const card = e.target.closest('.team-card');
        const isInFloatingPanel = card && card.closest('.floating-panel-content');
        if (!isInFloatingPanel) return;
        
        dragData.teamId = card.dataset.teamId;
        dragData.fromPhase = card.dataset.phase; 
        dragData.fromGroupIdx = Number(card.dataset.groupIdx);
        dragData.role = card.dataset.role;
        try { e.dataTransfer.setData('text/plain', dragData.teamId || ''); } catch(_) {}
        e.dataTransfer.effectAllowed = 'move';
      }, true);
      
      // dragend anywhere within root
      root.addEventListener('dragend', ()=>{
        dragData.teamId = null; dragData.fromPhase = null; dragData.fromGroupIdx = null; dragData.role = null;
      }, true);
      
      // dragend on floating panels
      document.body.addEventListener('dragend', (e)=>{
        const card = e.target.closest('.team-card');
        const isInFloatingPanel = card && card.closest('.floating-panel-content');
        if (!isInFloatingPanel) return;
        dragData.teamId = null; dragData.fromPhase = null; dragData.fromGroupIdx = null; dragData.role = null;
      }, true);
      // delegated dragover on zones
      root.addEventListener('dragover', (ev)=>{
        const zone = ev.target && (ev.target.closest && ev.target.closest('.host-zone, .guest-zone'));
        if (!zone || !root.contains(zone)) return;
        ev.preventDefault(); ev.stopPropagation();
        try{ ev.dataTransfer.dropEffect = 'move'; } catch(_){}
      }, true);
      // delegated dragover on floating panels (to allow dropping teams back)
      root.addEventListener('dragover', (ev)=>{
        const panel = ev.target && (ev.target.closest && ev.target.closest('.floating-panel-content'));
        if (!panel || !root.contains(panel)) return;
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
        
        // Check if dragging from unplaced panel
        const fromUnplaced = dragData.role === 'unplaced' || dragData.fromGroupIdx === -1;
        
        if (toRole === 'host'){
          const sameGroup = (dragData.fromGroupIdx === toIdx) && (dragData.fromPhase === toPhase);
          const toG = detailsGroups[toIdx];
          
          if (fromUnplaced) {
            // Dragging from unplaced panel to host zone
            const prevHost = toG.host_team_id ? String(toG.host_team_id) : null;
            toG.host_team_id = dragData.teamId;
            if (prevHost){ 
              toG.guest_team_ids = toG.guest_team_ids || [];
              if (!toG.guest_team_ids.some(t=> String(t)===prevHost)) toG.guest_team_ids.push(prevHost); 
            }
            changed = true;
          } else if (dragData.role === 'guest'){
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
          const toG = detailsGroups[toIdx];
          
          if (fromUnplaced) {
            // Dragging from unplaced panel to guest zone
            if (String(toG.host_team_id) !== dragData.teamId){
              toG.guest_team_ids = toG.guest_team_ids || [];
              if (!toG.guest_team_ids.some(t=> String(t)===dragData.teamId)) toG.guest_team_ids.push(dragData.teamId);
              changed = true;
            }
          } else {
            const fromG = detailsGroups[dragData.fromGroupIdx];
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
      // delegated drop on floating panels (to remove teams from matching)
      root.addEventListener('drop', async (ev)=>{
        const panelContent = ev.target && (ev.target.closest && ev.target.closest('.floating-panel-content'));
        if (!panelContent) return;
        ev.preventDefault(); ev.stopPropagation();
        
        if (!dragData.teamId) return;
        
        // Only allow removing teams that are currently placed (not already unplaced)
        if (dragData.role === 'unplaced') {
          toast('Team is already unplaced.', { type: 'info' });
          return;
        }
        
        // Remove team from its current group
        if (dragData.fromGroupIdx >= 0 && dragData.fromGroupIdx < detailsGroups.length) {
          const fromG = detailsGroups[dragData.fromGroupIdx];
          let removed = false;
          
          if (dragData.role === 'host') {
            if (String(fromG.host_team_id) === dragData.teamId) {
              fromG.host_team_id = null;
              removed = true;
            }
          } else if (dragData.role === 'guest') {
            const beforeLen = (fromG.guest_team_ids || []).length;
            fromG.guest_team_ids = (fromG.guest_team_ids || []).filter(t => String(t) !== dragData.teamId);
            removed = fromG.guest_team_ids.length < beforeLen;
          }
          
          if (removed) {
            unsaved = true;
            // Clear drag context before any DOM changes
            dragData.teamId = null; dragData.fromPhase = null; dragData.fromGroupIdx = null; dragData.role = null;
            // Defer UI update
            const doUpdate = async ()=>{
              renderMatchDetailsBoard();
              try { await validateCurrentGroups(); } catch(_) {}
              try { await previewCurrentGroups(); } catch(_) {}
              toast('Team removed from matching (unsaved).', { type: 'info' });
            };
            if (typeof requestAnimationFrame === 'function') requestAnimationFrame(()=>{ doUpdate(); }); else setTimeout(()=>{ doUpdate(); }, 0);
          }
        }
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
          const proceed = await showDialogConfirm(`Warnings detected:\n${msgs.join('\n')}\nProceed anyway?`, {
            title: 'Warnings detected',
            confirmLabel: 'Save anyway',
            tone: 'warning',
          });
          if (proceed){
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
          const t = await r.text();
          await showDialogAlert(`Failed to save: ${t}`, { tone: 'danger', title: 'Save groups failed' });
        }
      } else {
        const t = await r.text();
        await showDialogAlert(`Failed to save: ${t}`, { tone: 'danger', title: 'Save groups failed' });
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
    
    // Préserver les équipes synthétiques locales (split: et pair:) AVANT de faire la requête
    // pour capturer l'état actuel (après les éventuelles suppressions)
    const syntheticTeamsBeforeLoad = {};
    Object.entries(teamDetails || {}).forEach(([tid, details]) => {
      if (tid.startsWith('split:') || tid.startsWith('pair:')) {
        syntheticTeamsBeforeLoad[tid] = details;
      }
    });
    
    const res = await apiFetch(url);
  if (!res.ok){ detailsMetrics = {}; $('#match-details').innerHTML = ''; $('#match-details-msg').textContent = 'No details available.'; t.update('No details.'); t.close(); return; }
  const data = await res.json().catch(()=>null);
  if (!data){ detailsMetrics = {}; $('#match-details').innerHTML = ''; t.update('Load error.'); t.close(); return; }
    detailsVersion = data.version;
    detailsGroups = data.groups || [];
  detailsMetrics = data.metrics || {};
    
    // Charger les nouvelles données du serveur
    teamDetails = data.team_details || {};
    
    // Restaurer UNIQUEMENT les équipes synthétiques qui existaient avant le chargement
    Object.entries(syntheticTeamsBeforeLoad).forEach(([tid, details]) => {
      teamDetails[tid] = details;
    });
    
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
    await participantsModule.onEventsRefreshed(events);
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
        else {
          const t = await r.text();
          await showDialogAlert(`Failed to set status: ${t}`, { tone: 'danger', title: 'Update status failed' });
        }
      } else if (action === 'edit'){
  const r = await apiFetch(`/events/${id}?anonymise=false`);
        if (!r.ok) return;
        const ev = await r.json().catch(()=>null);
        if (!ev) return;
        ev.id = id;
        enterEditMode(ev);
      } else if (action === 'delete'){
        const title = btn.getAttribute('data-title') || id;
        const confirmed = await showDialogConfirm(`Delete event "${title}"? This will also remove related registrations, matches, plans, etc.`, {
          title: 'Delete event',
          confirmLabel: 'Delete',
          tone: 'danger',
          destructive: true,
        });
        if (!confirmed) return;
        const r = await apiFetch(`/events/${id}`, { method: 'DELETE' });
        if (r.ok) { await loadEvents(); }
        else {
          const t = await r.text();
          await showDialogAlert(`Failed to delete: ${t}`, { tone: 'danger', title: 'Delete event failed' });
        }
      }
    }
    await resumeMatchingProgressIfNeeded();
  }

  async function handleCreate(){
    const f = $('#create-event-form');
    if (!f) return;
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
    const cancelBtn = $('#btn-cancel-edit');
    if (cancelBtn){
      cancelBtn.addEventListener('click', (e)=>{
        e.preventDefault();
        enterCreateMode();
      });
    }
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

  const matchingProgressEls = {
    container: document.getElementById('matching-progress'),
    label: document.getElementById('matching-progress-label'),
    value: document.getElementById('matching-progress-value'),
    bar: document.getElementById('matching-progress-bar'),
    meta: document.getElementById('matching-progress-meta'),
  };
  const matchingProgressState = { current: 0, target: 0, rafId: null };
  let matchingJobPoll = null;

  function showMatchingProgressContainer(){
    const el = matchingProgressEls.container;
    if (!el) return;
    el.classList.remove('hidden');
    requestAnimationFrame(()=>{ el.classList.remove('opacity-0'); });
  }

  function hideMatchingProgressContainer(){
    const el = matchingProgressEls.container;
    if (!el) return;
    el.classList.add('opacity-0');
    setTimeout(()=>{
      if (!el.classList.contains('opacity-0')) return;
      el.classList.add('hidden');
      flashMatchingMessage('');
    }, 250);
  }

  function applyMatchingProgress(value){
    const pct = Math.max(0, Math.min(1, value));
    if (matchingProgressEls.bar) matchingProgressEls.bar.style.width = `${(pct * 100).toFixed(1)}%`;
    if (matchingProgressEls.value) matchingProgressEls.value.textContent = `${Math.round(pct * 100)}%`;
  }

  function resetMatchingProgress(){
    if (matchingProgressState.rafId){
      cancelAnimationFrame(matchingProgressState.rafId);
      matchingProgressState.rafId = null;
    }
    matchingProgressState.current = 0;
    matchingProgressState.target = 0;
    applyMatchingProgress(0);
    if (matchingProgressEls.meta) matchingProgressEls.meta.textContent = '';
  }

  function animateMatchingProgress(target){
    matchingProgressState.target = Math.max(0, Math.min(1, target));
    if (matchingProgressState.rafId) return;
    const step = ()=>{
      const diff = matchingProgressState.target - matchingProgressState.current;
      if (Math.abs(diff) < 0.001){
        matchingProgressState.current = matchingProgressState.target;
        applyMatchingProgress(matchingProgressState.current);
        matchingProgressState.rafId = null;
        return;
      }
      matchingProgressState.current += diff * 0.18;
      applyMatchingProgress(matchingProgressState.current);
      matchingProgressState.rafId = requestAnimationFrame(step);
    };
    matchingProgressState.rafId = requestAnimationFrame(step);
  }

  function translateJobMessage(message){
    if (!message) return '';
    const map = [
      { pattern: /En attente de démarrage/i, text: 'Waiting to start' },
      { pattern: /Initialisation/i, text: 'Initializing...' },
      { pattern: /Chargement des données/i, text: 'Loading data...' },
      { pattern: /Traitement des résultats/i, text: 'Processing results...' },
      { pattern: /Terminé/i, text: 'Completed' },
      { pattern: /Échec du matching/i, text: 'Matching failed' },
      { pattern: /Annulé/i, text: 'Cancelled' },
    ];
    for (const entry of map){
      if (entry.pattern.test(message)) return entry.text;
    }
  const startMatch = message.match(/Démarrage de l'algorithme\s+(.+)/i);
  if (startMatch) return `Starting ${startMatch[1].trim()} algorithm`;
  const doneMatch = message.match(/Algorithme\s+(.+) terminé/i);
  if (doneMatch) return `Algorithm ${doneMatch[1].trim()} finished`;
    if (message.includes(' - ')){
      const [prefix, suffix] = message.split(' - ', 2);
      if (prefix && suffix) return `${prefix.trim()}: ${suffix.trim()}`;
    }
    return message;
  }

  function flashMatchingMessage(text){
    const el = document.getElementById('matching-msg');
    if (!el) return;
    if (typeof text === 'string') el.textContent = text;
    el.classList.remove('matching-flash');
    void el.offsetWidth;
    el.classList.add('matching-flash');
  }

  function updateMatchingProgressUI(job){
    if (!job) return;
    const status = (job.status || '').toLowerCase();
    const progress = typeof job.progress === 'number' ? job.progress : 0;
    const labelEl = matchingProgressEls.label;
    if (labelEl){
      switch(status){
        case 'queued': labelEl.textContent = 'Waiting to start'; break;
        case 'running': labelEl.textContent = 'Matching in progress'; break;
        case 'completed': labelEl.textContent = 'Completed'; break;
        case 'failed': labelEl.textContent = 'Failed'; break;
        case 'cancelled': labelEl.textContent = 'Cancelled'; break;
        default: labelEl.textContent = 'Matching status'; break;
      }
    }
    if (matchingProgressEls.meta){
      const metaParts = [];
      const message = translateJobMessage(job.message || '');
      if (message) metaParts.push(message);
      if (Array.isArray(job.algorithms) && job.algorithms.length){
        metaParts.push(`Algorithms: ${job.algorithms.join(', ')}`);
      }
      matchingProgressEls.meta.textContent = metaParts.join(' • ');
    }
    showMatchingProgressContainer();
    const minProgress = status === 'queued' ? Math.max(progress, 0.05) : progress;
    animateMatchingProgress(minProgress);
  }

  function extractProposalVersions(job){
    if (!job || !Array.isArray(job.proposals)) return [];
    return job.proposals
      .map(entry => Number(entry && entry.version))
      .filter(num => Number.isFinite(num));
  }

  function stopMatchingJobPolling(){
    if (!matchingJobPoll) return;
    if (matchingJobPoll.timer) clearTimeout(matchingJobPoll.timer);
    matchingJobPoll = null;
  }

  async function handleMatchingJobCompletion(eventId, job){
    const status = (job.status || '').toLowerCase();
    const versions = extractProposalVersions(job);
    if (status === 'completed'){
      await loadProposals({ highlightVersions: versions });
      flashMatchingMessage('Matching completed. Latest proposals highlighted.');
      toast('Matching completed successfully.', { type: 'success' });
    } else if (status === 'failed'){
      await loadProposals();
      flashMatchingMessage('Matching failed. Please review the logs.');
      toast('Matching failed. Please review the logs.', { type: 'error' });
    } else if (status === 'cancelled'){
      flashMatchingMessage('Matching was cancelled.');
      toast('Matching was cancelled.', { type: 'warning' });
    }
    setTimeout(()=>{ hideMatchingProgressContainer(); resetMatchingProgress(); }, 1200);
  }

  async function pollMatchingJob(){
    const ctx = matchingJobPoll;
    if (!ctx) return;
    try{
      const res = await apiFetch(`/matching/${ctx.eventId}/jobs/${ctx.jobId}`);
      if (!matchingJobPoll || matchingJobPoll !== ctx) return;
      if (!res.ok) throw new Error(`Polling failed with status ${res.status}`);
      const job = await res.json().catch(()=>null);
      if (!job) throw new Error('Invalid job payload');
      updateMatchingProgressUI(job);
      const status = (job.status || '').toLowerCase();
      if (status === 'completed' || status === 'failed' || status === 'cancelled'){
        stopMatchingJobPolling();
        await handleMatchingJobCompletion(ctx.eventId, job);
      } else {
        ctx.timer = setTimeout(pollMatchingJob, 2000);
      }
    } catch(err){
      if (!matchingJobPoll || matchingJobPoll !== ctx) return;
      console.error('Matching job polling failed', err);
      ctx.timer = setTimeout(pollMatchingJob, 4000);
    }
  }

  function beginMatchingJobTracking(eventId, job){
    if (!job || !job.id) return;
    stopMatchingJobPolling();
    flashMatchingMessage('Matching is in progress. Progress is updating below.');
    updateMatchingProgressUI(job);
    matchingJobPoll = { eventId, jobId: job.id, timer: null };
    pollMatchingJob();
  }

  async function resumeMatchingProgressIfNeeded(){
    stopMatchingJobPolling();
    const select = document.getElementById('matching-event-select');
    const eventId = select ? select.value : '';
    if (!eventId){
      hideMatchingProgressContainer();
      resetMatchingProgress();
      return;
    }
    try{
      const res = await apiFetch(`/matching/${eventId}/jobs?limit=3`);
      if (!res.ok){
        hideMatchingProgressContainer();
        resetMatchingProgress();
        return;
      }
      const jobs = await res.json().catch(()=>[]);
      const active = Array.isArray(jobs) ? jobs.find(job=>{
        const status = (job.status || '').toLowerCase();
        return status === 'running' || status === 'queued';
      }) : null;
      if (active){
        beginMatchingJobTracking(eventId, active);
      } else {
        hideMatchingProgressContainer();
        resetMatchingProgress();
      }
    } catch(err){
      console.error('Failed to resume matching progress', err);
    }
  }

  async function startMatching(){
    $('#btn-start-matching').addEventListener('click', async (e)=>{
      const btn = e.currentTarget;
      const evId = $('#matching-event-select').value;
      if (!evId){
        toast('Please select an event first.', { type: 'warning' });
        return;
      }
      const algorithms = selectedAlgorithms();
      if (!algorithms.length){
        toast('Select at least one matching algorithm.', { type: 'warning' });
        return;
      }
      const weights = readWeights();
      setBtnLoading(btn, 'Starting...');
  const loader = toastLoading('Starting matching...');
      try {
        const res = await apiFetch(`/matching/${evId}/start`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ algorithms, weights }),
        });
        if (res.ok){
          const data = await res.json().catch(()=>null);
          if (data){
            if (data.status === 'already_running'){
              flashMatchingMessage('A matching job is already running for this event.');
              toast('A matching job is already running.', { type: 'info' });
            } else {
              flashMatchingMessage('Matching started. We will notify you when it finishes.');
              toast('Matching started successfully.', { type: 'success' });
            }
            if (data.job && data.job.id){
              beginMatchingJobTracking(evId, data.job);
            }
          } else {
            flashMatchingMessage('Matching started. We will notify you when it finishes.');
            toast('Matching started successfully.', { type: 'success' });
          }
          loader.update('Matching started');
          await loadProposals();
        } else {
          const errText = await res.text().catch(()=> 'Failed to start matching');
          flashMatchingMessage(`Failed to start: ${errText}`);
          loader.update('Matching error');
        }
      } catch(err){
        console.error('Failed to start matching', err);
        flashMatchingMessage(`Failed to start: ${err.message || err}`);
        loader.update('Matching error');
      } finally {
        loader.close();
        clearBtnLoading(btn);
      }
    });
    // Refresh proposals only — do not automatically reload match details to avoid
    // fetching heavy detail payloads when the admin just wants to refresh proposals.
    $('#btn-refresh-matches').addEventListener('click', async ()=>{
      const t = toastLoading('Refreshing proposals...');
      await loadProposals();
      t.update('Proposals refreshed');
      t.close();
    });
    const delAllBtn = $('#btn-delete-all-matches');
    if (delAllBtn){
      delAllBtn.addEventListener('click', async (e)=>{
        const btn = e.currentTarget; const evId = $('#matching-event-select').value;
        if (!evId) return;
        const confirmed = await showDialogConfirm('Delete ALL match proposals for this event?', {
          title: 'Delete all proposals',
          confirmLabel: 'Delete all',
          tone: 'danger',
          destructive: true,
        });
        if (!confirmed) return;
        setBtnLoading(btn, 'Deleting...');
        const t = toastLoading('Deleting proposals...');
        const r = await apiFetch(`/matching/${evId}/matches`, { method: 'DELETE' });
        if (r.ok){
          $('#matching-msg').textContent = 'All matches deleted.';
          detailsVersion = null; detailsGroups = []; teamDetails = {}; unsaved = false;
          $('#match-details').innerHTML = '';
          // Remove floating panels when matches are deleted
          ['appetizer', 'main', 'dessert'].forEach(phase => {
            const panel = $(`#unplaced-${phase}-panel`);
            if (panel) panel.remove();
          });
          await loadProposals();
          t.update('Deleted');
        } else {
          const tx = await r.text();
          await showDialogAlert(`Failed to delete: ${tx}`, { tone: 'danger', title: 'Delete proposals failed' });
          t.update('Delete error');
        }
        t.close();
        clearBtnLoading(btn);
      });
    }
    const eventSelect = document.getElementById('matching-event-select');
    if (eventSelect){
      eventSelect.addEventListener('change', resumeMatchingProgressIfNeeded);
    }
  }

  async function loadProposals(options){
    const opts = options || {};
    const highlightVersions = new Set();
    if (Array.isArray(opts.highlightVersions)){
      opts.highlightVersions.forEach(val=>{
        const num = Number(val);
        if (Number.isFinite(num)) highlightVersions.add(num);
      });
    }
    const highlightDuration = typeof opts.highlightDuration === 'number' ? Math.max(0, opts.highlightDuration) : 5000;
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

    const travel = (metrics.total_travel_seconds || 0).toFixed(0);
    const score = (metrics.aggregate_group_score || 0).toFixed(1);
    const totalParticipants = Number(metrics.total_participant_count || 0);
    const assignedParticipants = Number(metrics.assigned_participant_count || 0);
    const missingParticipants = Math.max(0, totalParticipants - assignedParticipants);
    const participantsLabel = totalParticipants ? `${assignedParticipants}/${totalParticipants}${missingParticipants ? ` (missing ${missingParticipants})` : ''}` : '—';
      // If there is an already finalized version, only that version's button should be actionable
      const releaseAlreadyExists = finalizedVersion != null;
      const releaseDisabled = (!isFinalized && releaseAlreadyExists && version !== finalizedVersion) || (unsaved && detailsVersion === version);
      let releaseClasses = (isFinalized ? 'bg-[#e53e3e] hover:bg-[#c53030]' : 'bg-[#1b5e20] hover:bg-[#166534]');
      // Label: finalized version shows 'Delete release' (actionable), otherwise show 'Release' or 'Save first'
      let releaseLabel = isFinalized ? 'Delete release' : 'Release';
      if (!isFinalized && unsaved && detailsVersion === version){
        releaseClasses = 'bg-[#9ca3af] cursor-not-allowed';
        releaseLabel = 'Save first';
      }
      card.innerHTML = `
        <div class="flex items-center justify-between gap-2 flex-wrap">
          <div class="font-semibold flex items-center gap-2">v${version}${algorithm?` · ${algorithm}`:''}${badges.length ? ` <span class=\"flex gap-1\">${badges.join('')}</span>` : ''}</div>
          <div class="text-sm text-[#4a5568]">Travel: ${travel}s · Score: ${score} · Participants: ${participantsLabel}</div>
        </div>
        ${metaParts.length ? `<div class="mt-1 text-xs text-[#475569]">${metaParts.join(' · ')}</div>` : ''}
        <div class="mt-2 flex flex-wrap gap-2">
          <button data-view="${version}" class="bg-[#4a5568] text-white rounded-xl px-3 py-1 text-sm">${isCurrent ? 'Viewing' : 'View'}</button>
          <button data-release="${version}" data-finalized="${isFinalized ? '1' : '0'}" class="${releaseClasses} text-white rounded-xl px-3 py-1 text-sm" ${releaseDisabled ? 'disabled' : ''}>${releaseLabel}</button>
          <button data-issues="${version}" class="bg-[#008080] text-white rounded-xl px-3 py-1 text-sm">View issues</button>
          <button data-delete="${version}" class="bg-[#e53e3e] text-white rounded-xl px-3 py-1 text-sm">Delete</button>
        </div>`;
      if (highlightVersions.has(version)){
        card.classList.add('matching-highlight');
        if (highlightDuration > 0){
          setTimeout(()=>{ card.classList.remove('matching-highlight'); }, highlightDuration);
        }
      }
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
        const finalizedAttr = releaseBtn.getAttribute('data-finalized');
        // If this is a finalized version, offer a less-destructive "unrelease" action
        if (finalizedAttr === '1'){
          const confirmed = await showDialogConfirm(`Unrelease proposal v${v}? This will remove generated plans but keep the proposal.`, {
            title: `Unrelease proposal v${v}`,
            confirmLabel: 'Unrelease',
            tone: 'danger',
            destructive: true,
          });
          if (!confirmed) return;
          setBtnLoading(releaseBtn, 'Unreleasing...');
          const t = toastLoading('Unreleasing...');
          try {
            const r = await apiFetch(`/matching/${evId}/unrelease?version=${v}`, { method: 'POST' });
            if (r.ok){
              t.update('Unreleased');
              await loadProposals();
              if (detailsVersion === v) await loadMatchDetails(v);
            } else {
              const tx = await r.text().catch(()=> 'Unrelease failed');
              await showDialogAlert(`Failed to unrelease: ${tx}`, { tone: 'danger', title: 'Unrelease failed' });
            }
          } catch (err){
            await showDialogAlert(`Failed to unrelease: ${err?.message || err}`, { tone: 'danger', title: 'Unrelease failed' });
          } finally {
            t.close();
            clearBtnLoading(releaseBtn);
          }
        } else {
          if (unsaved && detailsVersion === v){
            toast('Save changes before releasing this proposal.', { type: 'warning' });
            return;
          }
          await confirmAndRelease(evId, v, releaseBtn);
        }
      } else if (issuesBtn){
        const v = Number(issuesBtn.getAttribute('data-issues'));
        
        // Warn if viewing issues for current version with unsaved changes
        if (unsaved && v === detailsVersion) {
          const proceed = await showDialogConfirm(
            'You have unsaved changes. The issues shown will be based on the last saved state, not your current modifications.\n\nSave your changes first for accurate issue detection.',
            {
              title: 'Unsaved changes',
              confirmLabel: 'View issues anyway',
              cancelLabel: 'Cancel',
              tone: 'warning'
            }
          );
          if (!proceed) return;
        }
        
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
        const summaryByIssue = {};
        const ensureSummaryEntry = (issue)=>{
          if (!summaryByIssue[issue]){
            summaryByIssue[issue] = { total: 0, teamIds: new Set(), teamNames: new Set() };
          }
          return summaryByIssue[issue];
        };
        items.forEach(it=>{
          const perIssueCounts = it.issue_counts || {};
          Object.entries(perIssueCounts).forEach(([issue, total])=>{
            const entry = ensureSummaryEntry(issue);
            entry.total += Number(total) || 0;
          });
          const actorMap = it.actors || {};
          Object.entries(actorMap).forEach(([issue, actors])=>{
            const entry = ensureSummaryEntry(issue);
            (actors||[]).forEach(actor=>{
              if (actor && actor.team_id){
                const tid = String(actor.team_id);
                entry.teamIds.add(tid);
                entry.teamNames.add(getTeamLabel(tid, v));
              }
              if (actor && Array.isArray(actor.pair)){
                actor.pair.forEach(tid=>{
                  const id = String(tid);
                  entry.teamIds.add(id);
                  entry.teamNames.add(getTeamLabel(id, v));
                });
              }
            });
          });
          if (!Object.keys(perIssueCounts).length && Array.isArray(it.issues)){
            it.issues.forEach(issue=>{
              const entry = ensureSummaryEntry(issue);
              entry.total += 1;
            });
          }
        });

        const panel = document.createElement('div');
        panel.className = 'issues-panel mt-3 rounded-xl border border-[#e2e8f0] bg-white p-3 text-sm shadow-sm space-y-3';

        const header = document.createElement('div');
        header.className = 'flex items-center justify-between gap-2 flex-wrap';
        header.innerHTML = `<span class="font-semibold text-[#111827]">Issues overview</span><span class="text-xs text-[#6b7280]">Proposal v${v}</span>`;
        panel.appendChild(header);

        const summary = document.createElement('div');
        summary.className = 'flex flex-wrap gap-2';
        Object.entries(summaryByIssue)
          .map(([issue, entry])=>{
            const teamCount = entry.teamIds ? entry.teamIds.size : 0;
            const names = entry.teamNames ? Array.from(entry.teamNames).filter(Boolean) : [];
            const total = entry.total || teamCount;
            return [issue, { total, uniqueTeams: teamCount || null, teamNames: names }];
          })
          .filter(([, info])=> info.total > 0)
          .sort((a,b)=> (b[1].total - a[1].total))
          .forEach(([issue, info])=>{
            summary.appendChild(createIssueChip(issue, info));
          });
        if (!summary.children.length){
          const empty = document.createElement('span');
          empty.className = 'text-xs text-[#64748b]';
          empty.textContent = 'No grouped issues reported.';
          summary.appendChild(empty);
        }
        panel.appendChild(summary);

        const list = document.createElement('div');
        list.className = 'space-y-2 max-h-60 overflow-auto pr-1';
        items.forEach(item=>{
          list.appendChild(createIssueCard(item, v));
        });
        panel.appendChild(list);

        card.appendChild(panel);
      } else if (deleteBtn){
        const v = Number(deleteBtn.getAttribute('data-delete'));
        const confirmed = await showDialogConfirm(`Delete proposal v${v}?`, {
          title: 'Delete proposal',
          confirmLabel: 'Delete',
          tone: 'danger',
          destructive: true,
        });
        if (!confirmed) return;
        const r = await apiFetch(`/matching/${evId}/matches?version=${v}`, { method: 'DELETE' });
        if (r.ok){
          await loadProposals();
          if (detailsVersion === v){
            await loadMatchDetails();
          }
        } else {
          const t = await r.text();
          await showDialogAlert(`Failed to delete: ${t}`, { tone: 'danger', title: 'Delete proposal failed' });
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

  function phaseColor(phase){
    switch (phase){
      case 'appetizer': return '#059669';
      case 'main': return '#f97316';
      case 'dessert': return '#f59e0b';
      case 'after_party': return '#7c3aed';
      default: return '#6b7280';
    }
  }

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

  async function safeRun(step, label){
    try {
      await step();
    } catch (err){
      console.error(`Admin dashboard init issue in ${label}:`, err);
    }
  }

  async function init(){
    await safeRun(ensureCsrf, 'ensureCsrf');
    await safeRun(loadEvents, 'loadEvents');
    await safeRun(handleCreate, 'handleCreate');
    await safeRun(startMatching, 'startMatching');
    await safeRun(bindRefunds, 'bindRefunds');
    bindWeightInfo();
    bindAlgorithmInfo();
    bindAdvancedWeightsToggle();
    bindMaps();
    setupFloatingPanelScrollListener();
    // Do not auto-load matching proposals or details on page load.
    // Users must click "Start Matching" or "Refresh Proposals" to fetch them.
    const placeholder = document.getElementById('match-details-msg');
    if (placeholder) placeholder.textContent = 'Click “Refresh Proposals” or “Start Matching” to load data.';
    drawLegend();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
