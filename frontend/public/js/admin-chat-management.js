// Admin Chat Management
(async function () {
  const el = (sel) => document.querySelector(sel);
  const evSelect = el('#event-select');
  const btnRefresh = el('#btn-refresh-events');
  const groupsList = el('#groups-list');
  const btnCreate = el('#btn-create-chat');
  const btnClear = el('#btn-clear-chat');
  const btnDelete = el('#btn-delete-chat');
  const manualGroupId = el('#manual-group-id');
  const postGroupId = el('#post-group-id');
  const postMessage = el('#post-message');
  const btnPost = el('#btn-post-message');
  const postStatus = el('#post-status');

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
      return dlg.confirm(message, Object.assign({ tone: 'warning', confirmLabel: 'Confirmer', cancelLabel: 'Annuler' }, options || {}));
    }
    return Promise.resolve(window.confirm(message));
  }

  async function apiGet(path) {
    const r = await window.dh.apiGet(path);
    if (!r.res.ok) throw new Error('api error');
    return r.data;
  }

  async function loadEvents() {
    evSelect.innerHTML = '';
    try {
      const { res, data } = await window.dh.apiGet('/events/');
      if (!res.ok) return;
      const events = data;
      events.forEach(ev => {
        const opt = document.createElement('option');
        opt.value = ev._id || ev.id || ev.id_str || ev.id;
        opt.textContent = `${ev.title || ev.name} — ${ev.city || ''} — ${ev.date || ''}`;
        evSelect.appendChild(opt);
      });
    } catch (err) {
      console.error(err);
    }
  }

  async function loadGroupsForSelectedEvent() {
    groupsList.innerHTML = '';
    const eventId = evSelect.value;
    if (!eventId) return;
    try {
      // use admin endpoint to list chat groups by event — fallback to /chats/groups?event_id=...
      const { res, data } = await window.dh.apiGet(`/admin/chats?event_id=${encodeURIComponent(eventId)}`);
      if (!res.ok) return;
      const groups = data;
      if (!groups || groups.length === 0) {
        groupsList.textContent = 'No groups';
        return;
      }
      groups.forEach(g => {
        const row = document.createElement('div');
        row.className = 'p-2 bg-white rounded shadow-sm flex justify-between items-start';
        const left = document.createElement('div');
        left.innerHTML = `<div class="font-semibold">ID: ${g._id || g.id}</div><div class="text-sm text-gray-600">section: ${g.section_ref} — participants: ${ (g.participant_emails || []).join(', ') }</div>`;
        const right = document.createElement('div');
        right.className = 'flex gap-2';
        const btnView = document.createElement('button');
        btnView.textContent = 'View';
        btnView.className = 'px-2 py-1 bg-[#4a5568] text-white rounded';
        btnView.addEventListener('click', () => viewGroupMessages(g._id || g.id, g));
        const btnDel = document.createElement('button');
        btnDel.textContent = 'Delete';
        btnDel.className = 'px-2 py-1 bg-red-600 text-white rounded';
        btnDel.addEventListener('click', () => deleteGroup(g._id || g.id));
        right.appendChild(btnView);
        right.appendChild(btnDel);
        row.appendChild(left);
        row.appendChild(right);
        groupsList.appendChild(row);
      });
    } catch (err) {
      console.error(err);
    }
  }

  async function viewGroupMessages(groupId, group) {
    try {
      const { res: r1, data: g } = await window.dh.apiGet(`/admin/chats/groups/${encodeURIComponent(groupId)}`);
      if (!r1.ok) return showDialogAlert('Failed to fetch group', { tone: 'danger', title: 'Chat group' });
      const { res, data: msgs } = await window.dh.apiGet(`/admin/chats/groups/${encodeURIComponent(groupId)}/messages`);
      let out = `Group ${groupId}\nSection: ${g.section_ref || group.section_ref}\nParticipants: ${(g.participant_emails || group.participant_emails || []).join(', ')}\n\nMessages:\n`;
      if (res.ok && Array.isArray(msgs)) {
        msgs.forEach(m => { out += `${m.created_at || ''} — ${m.sender_email || ''}: ${m.body}\n`; });
      } else {
        out += 'No messages';
      }
      await showDialogAlert(out, { title: `Group ${groupId}`, tone: 'info' });
    } catch (err) {
      console.error(err);
      await showDialogAlert('Error fetching messages', { tone: 'danger', title: 'Chat group' });
    }
  }

  async function createChatForEvent() {
    const eventId = evSelect.value;
    if (!eventId) { await showDialogAlert('Select an event first.', { tone: 'warning', title: 'Action required' }); return; }
    try {
      // call admin endpoint to seed chat groups for that event
      const { res, data } = await window.dh.apiPost(`/admin/chats/seed?event_id=${encodeURIComponent(eventId)}`, {});
      if (!res.ok) {
        await showDialogAlert('Failed to create chats', { tone: 'danger', title: 'Chat groups' });
        return;
      }
      await showDialogAlert('Chats created successfully.', { tone: 'success', title: 'Chat groups' });
      loadGroupsForSelectedEvent();
    } catch (err) {
      console.error(err);
    }
  }

  async function clearChatsForEvent() {
    const eventId = evSelect.value;
    if (!eventId) { await showDialogAlert('Select an event first.', { tone: 'warning', title: 'Action required' }); return; }
    const confirmClear = await showDialogConfirm('Clear (delete) all chat groups for this event?', {
      title: 'Clear chats',
      confirmLabel: 'Delete all',
      tone: 'danger',
      destructive: true,
    });
    if (!confirmClear) return;
    try {
      const { res, data } = await window.dh.apiPost(`/admin/chats/clear?event_id=${encodeURIComponent(eventId)}`, {});
      if (!res.ok) return showDialogAlert('Failed to clear chats', { tone: 'danger', title: 'Chat groups' });
      await showDialogAlert('Chats cleared for the selected event.', { tone: 'success', title: 'Chat groups' });
      loadGroupsForSelectedEvent();
    } catch (err) {
      console.error(err);
    }
  }

  async function deleteGroup(groupId) {
    if (!groupId) groupId = manualGroupId.value;
    if (!groupId) { await showDialogAlert('No group id provided.', { tone: 'warning', title: 'Chat groups' }); return; }
    const confirmDelete = await showDialogConfirm('Delete group ' + groupId + '?', {
      title: 'Delete group',
      confirmLabel: 'Delete',
      tone: 'danger',
      destructive: true,
    });
    if (!confirmDelete) return;
    try {
      const { res } = await window.dh.apiDelete(`/admin/chats/groups/${encodeURIComponent(groupId)}`);
      if (!res.ok) return showDialogAlert('Failed to delete group.', { tone: 'danger', title: 'Chat groups' });
      await showDialogAlert('Group deleted successfully.', { tone: 'success', title: 'Chat groups' });
      loadGroupsForSelectedEvent();
    } catch (err) {
      console.error(err);
    }
  }

  async function postMessageToGroup() {
    const gid = postGroupId.value;
    const body = postMessage.value;
    if (!gid || !body) { await showDialogAlert('Group id and message body are required.', { tone: 'warning', title: 'Chat groups' }); return; }
    try {
      const { res } = await window.dh.apiPost(`/admin/chats/groups/${encodeURIComponent(gid)}/messages`, { body });
      if (!res.ok) return postStatus.textContent = 'Failed to post';
      postStatus.textContent = 'Posted as admin';
      postGroupId.value = '';
      postMessage.value = '';
    } catch (err) {
      console.error(err);
      postStatus.textContent = 'Error';
    }
  }

  btnRefresh.addEventListener('click', loadEvents);
  evSelect.addEventListener('change', loadGroupsForSelectedEvent);
  btnCreate.addEventListener('click', createChatForEvent);
  btnClear.addEventListener('click', clearChatsForEvent);
  btnDelete.addEventListener('click', () => deleteGroup());
  btnPost.addEventListener('click', postMessageToGroup);

  // Admin menu toggling on dashboard (used across admin pages) — graceful if not present
  const adminMenuBtn = document.getElementById('admin-menu-btn');
  if (adminMenuBtn) {
    adminMenuBtn.addEventListener('click', () => {
      const m = document.getElementById('admin-menu');
      if (!m) return;
      m.classList.toggle('hidden');
    });
  }

  // initial load
  await loadEvents();
})();
