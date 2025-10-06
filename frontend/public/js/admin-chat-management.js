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

  async function apiGet(path) {
    const r = await window.dh.apiGet(path);
    if (!r.res.ok) throw new Error('api error');
    return r.data;
  }

  async function loadEvents() {
    evSelect.innerHTML = '';
    try {
      const { res, data } = await window.dh.apiGet('/admin/events');
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
      if (!r1.ok) return alert('Failed to fetch group');
      const { res, data: msgs } = await window.dh.apiGet(`/admin/chats/groups/${encodeURIComponent(groupId)}/messages`);
      let out = `Group ${groupId}\nSection: ${g.section_ref || group.section_ref}\nParticipants: ${(g.participant_emails || group.participant_emails || []).join(', ')}\n\nMessages:\n`;
      if (res.ok && Array.isArray(msgs)) {
        msgs.forEach(m => { out += `${m.created_at || ''} — ${m.sender_email || ''}: ${m.body}\n`; });
      } else {
        out += 'No messages';
      }
      alert(out);
    } catch (err) {
      console.error(err);
      alert('Error fetching messages');
    }
  }

  async function createChatForEvent() {
    const eventId = evSelect.value;
    if (!eventId) return alert('Select event');
    try {
      // call admin endpoint to seed chat groups for that event
      const { res, data } = await window.dh.apiPost(`/admin/chats/seed?event_id=${encodeURIComponent(eventId)}`, {});
      if (!res.ok) {
        alert('Failed to create chats');
        return;
      }
      alert('Created chats');
      loadGroupsForSelectedEvent();
    } catch (err) {
      console.error(err);
    }
  }

  async function clearChatsForEvent() {
    const eventId = evSelect.value;
    if (!eventId) return alert('Select event');
    if (!confirm('Clear (delete) all chat groups for this event?')) return;
    try {
      const { res, data } = await window.dh.apiPost(`/admin/chats/clear?event_id=${encodeURIComponent(eventId)}`, {});
      if (!res.ok) return alert('Failed to clear chats');
      alert('Cleared chats');
      loadGroupsForSelectedEvent();
    } catch (err) {
      console.error(err);
    }
  }

  async function deleteGroup(groupId) {
    if (!groupId) groupId = manualGroupId.value;
    if (!groupId) return alert('No group id');
    if (!confirm('Delete group ' + groupId + '?')) return;
    try {
      const { res } = await window.dh.apiDelete(`/admin/chats/groups/${encodeURIComponent(groupId)}`);
      if (!res.ok) return alert('Failed to delete');
      alert('Deleted');
      loadGroupsForSelectedEvent();
    } catch (err) {
      console.error(err);
    }
  }

  async function postMessageToGroup() {
    const gid = postGroupId.value;
    const body = postMessage.value;
    if (!gid || !body) return alert('group id and body required');
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
