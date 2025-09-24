(function(){
  const $ = (sel, root)=> (root||document).querySelector(sel);
  const $$ = (sel, root)=> Array.from((root||document).querySelectorAll(sel));
  const fmtDate = (s)=> s ? new Date(s).toLocaleString() : '';

  async function ensureCsrf(){ try{ await (window.initCsrf && window.initCsrf()); } catch(e){} }

  async function loadEvents(){
    const res = await apiFetch('/events');
    const events = await res.json().catch(()=>[]);
    const tbody = $('#events-tbody');
    tbody.innerHTML = '';
    events.forEach(ev=>{
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td class="p-2 font-semibold">${ev.title||'Untitled'}</td>
        <td class="p-2">${ev.date||''}</td>
        <td class="p-2">${ev.city||''}</td>
        <td class="p-2"><span class="tag tag-${(ev.status||'').toLowerCase()}">${ev.status||''}</span></td>
        <td class="p-2"><span class="tag tag-${(ev.matching_status||'').toLowerCase()}">${ev.matching_status||''}</span></td>
        <td class="p-2">${ev.attendee_count||0}</td>
        <td class="p-2 space-x-2">
          <button data-action="publish" data-id="${ev.id}" class="bg-[#008080] text-white px-3 py-1 rounded-xl font-semibold shadow hover:bg-[#00b3b3] transition">Publish</button>
          <button data-action="edit" data-id="${ev.id}" class="bg-[#ffc241] text-[#172a3a] px-3 py-1 rounded-xl font-semibold shadow hover:bg-[#ffe5d0] transition">Edit</button>
        </td>`;
      tbody.appendChild(tr);
    });
    // event count and selects
    $('#events-count').textContent = `${events.length} events`;
    const selects = [$('#matching-event-select'), $('#issues-event-select'), $('#refunds-event-select')];
    selects.forEach(sel=>{ if (!sel) return; sel.innerHTML = events.map(e=>`<option value="${e.id}">${e.title} (${e.date||''})</option>`).join(''); });
    // bind actions (publish/edit)
    tbody.onclick = async (e)=>{
      const btn = e.target.closest('button'); if (!btn) return;
      const id = btn.getAttribute('data-id'); const action = btn.getAttribute('data-action');
      if (action === 'publish'){
        const r = await apiFetch(`/events/${id}/publish`, { method: 'POST' });
        if (r.ok) { await loadEvents(); }
      } else if (action === 'edit'){
        // quick inline prompts for common fields
        const title = prompt('Title');
        const status = prompt('Status (draft|published|closed|cancelled)');
        const fee = prompt('Fee cents (number)');
        const payload = {};
        if (title) payload.title = title;
        if (status) payload.status = status;
        if (fee) payload.fee_cents = Number(fee)||0;
        if (Object.keys(payload).length){
          const r = await apiFetch(`/events/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
          if (r.ok) await loadEvents();
        }
      }
    }
  }

  async function handleCreate(){
    const f = $('#create-event-form');
    f.addEventListener('submit', async (e)=>{
      e.preventDefault();
      const fd = new FormData(f);
      const payload = {
        title: fd.get('title'),
        date: fd.get('date') || null,
        start_at: fd.get('start_at') || null,
        city: fd.get('city') || null,
        capacity: fd.get('capacity') ? Number(fd.get('capacity')) : null,
        fee_cents: fd.get('fee_cents') ? Number(fd.get('fee_cents')) : 0,
        registration_deadline: fd.get('registration_deadline') || null,
        extra_info: fd.get('extra_info') || null,
        refund_on_cancellation: fd.get('refund_on_cancellation') ? true : false,
        chat_enabled: fd.get('chat_enabled') ? true : false,
        valid_zip_codes: (fd.get('valid_zip_codes')||'').split(',').map(s=>s.trim()).filter(Boolean),
      };
      const addr = fd.get('after_party_address');
      if (addr) payload.after_party_location = { address: addr };
      const res = await apiFetch('/events', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const out = $('#create-event-msg');
      if (res.ok) { out.textContent = 'Event created as draft.'; f.reset(); await loadEvents(); }
      else { out.textContent = 'Failed to create event.'; }
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
    });
    $('#btn-refresh-matches').addEventListener('click', loadProposals);
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
        <div class="flex items-center justify-between">
          <div class="font-semibold">v${m.version} · ${alg}</div>
          <div class="text-sm text-[#4a5568]">Travel: ${(met.total_travel_seconds||0).toFixed(0)}s · Score: ${(met.aggregate_group_score||0).toFixed(1)}</div>
        </div>
        <div class="mt-2 flex gap-2">
          <button data-finalize="${m.version}" class="bg-[#1b5e20] text-white rounded-xl px-3 py-1 text-sm">Release</button>
          <button data-issues="${m.version}" class="bg-[#008080] text-white rounded-xl px-3 py-1 text-sm">View issues</button>
        </div>`;
      box.appendChild(d);
    });
    box.onclick = async (e)=>{
      const f = e.target.closest('button[data-finalize]');
      const i = e.target.closest('button[data-issues]');
      const evId = $('#matching-event-select').value;
      if (f){
        const v = Number(f.getAttribute('data-finalize'));
        const r = await apiFetch(`/matching/${evId}/finalize?version=${v}`, { method: 'POST' });
        if (r.ok) { await loadEvents(); await loadProposals(); }
      } else if (i){
        const v = Number(i.getAttribute('data-issues'));
        $('#issues-event-select').value = evId; $('#issues-version').value = v;
        await loadIssues();
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
      if (r.ok) { await loadEvents(); await loadProposals(); await loadIssues(); }
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
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
