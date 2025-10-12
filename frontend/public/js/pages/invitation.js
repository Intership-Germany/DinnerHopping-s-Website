(async function(){
  const BASE = window.BACKEND_BASE_URL;
  const apiFetch = (window.dh && window.dh.apiFetch) || fetch;

  const loading = document.getElementById('loading');
  const content = document.getElementById('content');
  const error = document.getElementById('error');
  const details = document.getElementById('invitationDetails');
  const actions = document.getElementById('actions');
  const message = document.getElementById('message');

  const params = new URLSearchParams(window.location.search);
  const token = params.get('token');
  if(!token){
    loading.classList.add('hidden'); error.classList.remove('hidden'); return;
  }

  function showMsg(txt, type='info'){
    message.textContent = txt; message.classList.remove('hidden','bg-blue-50','text-blue-700','bg-green-50','text-green-700','bg-red-50','text-red-700');
    if(type==='success') message.classList.add('bg-green-50','text-green-700');
    else if(type==='error') message.classList.add('bg-red-50','text-red-700');
    else message.classList.add('bg-blue-50','text-blue-700');
  }

  try{
    const res = await apiFetch(`${BASE}/invitations/${token}`);
    if(!res.ok){ throw new Error('Invitation not found'); }
    const inv = await res.json();
    loading.classList.add('hidden'); content.classList.remove('hidden');

    const eventTitle = inv.event_id ? (inv.event_title || inv.event_id) : 'Event';
    document.getElementById('title').textContent = `Invitation: ${eventTitle}`;
    document.getElementById('subtitle').textContent = `Invitation for ${inv.invited_email || ''}`;

    // Render basic details
    details.innerHTML = `\
      <div class="border-l-4 border-emerald-500 pl-4">\
        <h3 class="font-semibold text-lg text-gray-900">${eventTitle}</h3>\
        <p class="text-gray-600 mt-1">Invitation status: ${inv.status}</p>\
      </div>\
    `;

    // Actions: Accept and Decline
    actions.innerHTML = `\
      <button id="acceptBtn" class="px-6 py-3 bg-emerald-600 text-white rounded-lg">Accept invitation</button>\
      <button id="declineBtn" class="px-6 py-3 bg-red-600 text-white rounded-lg">Decline</button>\
      <a href="/home.html" class="px-6 py-3 bg-gray-200 text-gray-700 rounded-lg">Go to Home</a>\
    `;

    document.getElementById('acceptBtn').addEventListener('click', async ()=>{
      try{
        // If user not logged in, backend will require name/password via POST accept; the UI can offer a modal — keep simple: attempt POST without body first (if auth cookie present it will work)
        const r = await apiFetch(`${BASE}/invitations/${token}/accept`, { method: 'POST', credentials: 'include' });
        if(!r.ok){ const d = await r.json().catch(()=>({})); throw new Error(d.detail||'Failed to accept'); }
        const d = await r.json();
        showMsg('Invitation accepted. '+(d.message || ''),'success');
        actions.classList.add('hidden');
      }catch(e){
        // If backend requires account creation, show instructions
        showMsg('Could not accept automatically. If you do not have an account you will need to create one first. Please check your email for a set-password link or register/login and then retry.', 'error');
      }
    });

    document.getElementById('declineBtn').addEventListener('click', async ()=>{
      if(!confirm('Are you sure you want to decline this invitation?')) return;
      try{
        // Use registrations/teams decline endpoint if team_id present, otherwise use invitation revoke
        if(inv.team_id){
          const r = await apiFetch(`${BASE}/registrations/teams/${inv.team_id}/decline`, { method: 'POST', credentials: 'include' });
          if(!r.ok){ const d = await r.json().catch(()=>({})); throw new Error(d.detail||'Failed to decline'); }
        }else{
          // revoke via invitations/<id>/revoke
          const r = await apiFetch(`${BASE}/invitations/${inv.id}/revoke`, { method: 'POST', credentials: 'include' });
          if(!r.ok){ const d = await r.json().catch(()=>({})); throw new Error(d.detail||'Failed to revoke'); }
        }
        showMsg('Invitation declined/removed. The creator will be notified.', 'success');
        actions.classList.add('hidden');
      }catch(e){ showMsg(e.message||'Failed to decline','error'); }
    });

  }catch(err){ loading.classList.add('hidden'); error.classList.remove('hidden'); }
})();
