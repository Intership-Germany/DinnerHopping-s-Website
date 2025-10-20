/**
 * Admin User Management Page
 * Consolidates team oversight, participant insights, and refund tooling.
 */
(function () {
  const apiFetch = (window.dh && window.dh.apiFetch) || window.apiFetch;
  if (typeof apiFetch !== 'function') {
    console.error('admin-user-management: apiFetch is not available.');
    return;
  }

  const baseToast = (window.dh && window.dh.toast) || null;
  const baseToastLoading = (window.dh && window.dh.toastLoading) || null;
  const showToast = (message, type = 'info') => {
    if (typeof window.showToast === 'function') {
      window.showToast(message, type);
    } else if (typeof baseToast === 'function') {
      baseToast(message, { type });
    } else if (type === 'error') {
      console.error(message);
    } else {
      console.log(message);
    }
  };
  const showLoadingToast = (message) => {
    if (typeof baseToastLoading === 'function') {
      return baseToastLoading(message);
    }
    showToast(message);
    return { update() {}, close() {} };
  };
  function getDialog(){
    return (window.dh && window.dh.dialog) || null;
  }
  function showDialogAlert(message, options){
    const dlg = getDialog();
    if (dlg && typeof dlg.alert === 'function'){
      return dlg.alert(message, Object.assign({ title: 'Information', tone: 'info' }, options || {}));
    }
    window.alert(message);
    return Promise.resolve();
  }
  function showDialogConfirm(message, options){
    const dlg = getDialog();
    if (dlg && typeof dlg.confirm === 'function'){
      return dlg.confirm(message, Object.assign({ tone: 'warning', confirmLabel: 'Continuer', cancelLabel: 'Annuler' }, options || {}));
    }
    return Promise.resolve(window.confirm(message));
  }
  const $ = (selector, root) => (root || document).querySelector(selector);
  const ESCAPE_LOOKUP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const ESCAPE_REGEX = /[&<>"']/g;
  const escapeHtml = (value) => {
    if (value === null || value === undefined) return '';
    return String(value).replace(ESCAPE_REGEX, (ch) => ESCAPE_LOOKUP[ch] || ch);
  };
  const fmtDate = (value) => {
    if (!value) return '';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleString();
  };

  let currentTeams = [];
  let currentFilter = 'all';
  let selectedEventId = null;
  let cachedEvents = [];

  const eventSelect = document.getElementById('event-select');
  const btnLoadTeams = document.getElementById('btn-load-teams');
  const btnSendReminders = document.getElementById('btn-send-incomplete-reminders');
  const btnReleasePlans = document.getElementById('btn-release-plans');
  const teamsTable = document.getElementById('teams-tbody');
  const loadingMsg = document.getElementById('loading-msg');
  const statusMsg = document.getElementById('status-msg');
  const statsContainer = document.getElementById('stats-container');
  const statComplete = document.getElementById('stat-complete');
  const statIncomplete = document.getElementById('stat-incomplete');
  const statFaulty = document.getElementById('stat-faulty');
  const statPending = document.getElementById('stat-pending');
  const filterButtons = {
    all: document.getElementById('filter-all'),
    complete: document.getElementById('filter-complete'),
    incomplete: document.getElementById('filter-incomplete'),
    faulty: document.getElementById('filter-faulty'),
    pending: document.getElementById('filter-pending'),
  };

  function bindTeamControls() {
    if (btnLoadTeams) btnLoadTeams.addEventListener('click', loadTeams);
    if (btnSendReminders) btnSendReminders.addEventListener('click', sendIncompleteReminders);
    if (btnReleasePlans) btnReleasePlans.addEventListener('click', releasePlans);
    Object.entries(filterButtons).forEach(([key, btn]) => {
      if (!btn) return;
      btn.addEventListener('click', () => applyFilter(key));
    });
  }

  async function loadEvents() {
    try {
      const res = await apiFetch('/events/', { method: 'GET' });
      if (!res.ok) throw new Error(`Failed to load events (${res.status})`);
      const events = await res.json();
      if (!Array.isArray(events)) throw new Error('Unexpected events payload');
      cachedEvents = events.map((event) => ({
        id: event._id || event.id,
        title: event.title,
        date: event.date,
      })).filter((event) => Boolean(event.id));
      populateTeamEventSelect();
      participantsModule.setEvents(cachedEvents);
      refundsModule.setEvents(cachedEvents);
      statusMsg.textContent = cachedEvents.length ? 'Events loaded. Select filters to continue.' : 'No events available yet.';
      statusMsg.className = 'text-sm text-gray-600';
    } catch (error) {
      console.error('Error loading events:', error);
      if (eventSelect) {
        eventSelect.innerHTML = '<option value="">-- All Events --</option>';
      }
      participantsModule.setEvents([]);
      refundsModule.setEvents([]);
      statusMsg.textContent = 'Failed to load events. Please retry later.';
      statusMsg.className = 'text-sm text-red-600';
    }
  }

  function populateTeamEventSelect() {
    if (!eventSelect) return;
    eventSelect.innerHTML = '<option value="">-- All Events --</option>';
    cachedEvents.forEach((event) => {
      const option = document.createElement('option');
      option.value = event.id;
      option.textContent = `${event.title || 'Event'} ${event.date ? `(${event.date})` : ''}`.trim();
      eventSelect.appendChild(option);
    });
  }

  async function loadTeams() {
    if (!eventSelect || !teamsTable) return;
    selectedEventId = eventSelect.value || null;
    if (loadingMsg) loadingMsg.classList.remove('hidden');
    if (statusMsg) {
      statusMsg.textContent = '';
      statusMsg.className = 'text-sm text-gray-600';
    }
    if (statsContainer) statsContainer.classList.add('hidden');

    try {
      const url = selectedEventId ? `/admin/teams/overview?event_id=${encodeURIComponent(selectedEventId)}` : '/admin/teams/overview';
      const res = await apiFetch(url, { method: 'GET' });
      if (!res.ok) throw new Error(`Failed to load teams (${res.status})`);
      const data = await res.json();
      currentTeams = Array.isArray(data.teams) ? data.teams : [];
      updateTeamStats(data);
      renderTeams();
      if (statusMsg) {
        statusMsg.textContent = `Loaded ${currentTeams.length} team(s)`;
        statusMsg.className = 'text-sm text-green-600';
      }
      toggleTeamActions(Boolean(selectedEventId));
    } catch (error) {
      console.error('Error loading teams:', error);
      if (statusMsg) {
        statusMsg.textContent = `Failed to load teams: ${error.message}`;
        statusMsg.className = 'text-sm text-red-600';
      }
      if (teamsTable) {
        teamsTable.innerHTML = '<tr><td colspan="8" class="p-4 text-center text-red-500">Error loading teams</td></tr>';
      }
    } finally {
      if (loadingMsg) loadingMsg.classList.add('hidden');
    }
  }

  function updateTeamStats(data) {
    if (!statsContainer) return;
    statsContainer.classList.remove('hidden');
    if (statComplete) statComplete.textContent = data.complete || 0;
    if (statIncomplete) statIncomplete.textContent = data.incomplete || 0;
    if (statFaulty) statFaulty.textContent = data.faulty || 0;
    if (statPending) statPending.textContent = data.pending || 0;
  }

  function toggleTeamActions(visible) {
    const method = visible ? 'remove' : 'add';
    if (btnSendReminders) btnSendReminders.classList[method]('hidden');
    if (btnReleasePlans) btnReleasePlans.classList[method]('hidden');
  }

  function applyFilter(filter) {
    currentFilter = filter;
    Object.entries(filterButtons).forEach(([key, btn]) => {
      if (!btn) return;
      if (key === filter) {
        btn.classList.add('font-bold');
      } else {
        btn.classList.remove('font-bold');
      }
    });
    renderTeams();
  }

  function renderTeams() {
    if (!teamsTable) return;
    const filtered = currentFilter === 'all'
      ? currentTeams
      : currentTeams.filter((team) => team.category === currentFilter);

    if (!filtered.length) {
      teamsTable.innerHTML = '<tr><td colspan="8" class="p-4 text-center text-gray-500">No teams match the current filter</td></tr>';
      return;
    }

    teamsTable.innerHTML = filtered.map((team) => {
      const memberEmails = (team.members || []).map((member) => member.email || 'unknown').join(', ');
      const createdDate = team.created_at ? new Date(team.created_at).toLocaleDateString() : 'Unknown';
      return `
        <tr class="border-b hover:bg-gray-50">
          <td class="p-2 text-xs font-mono">${escapeHtml(String(team.team_id || '').slice(0, 8))}${team.team_id && String(team.team_id).length > 8 ? '...' : ''}</td>
          <td class="p-2">${escapeHtml(team.event_title || 'Unknown Event')}</td>
          <td class="p-2">${escapeHtml(team.status || 'unknown')}</td>
          <td class="p-2">${renderTeamBadge(team.category)}</td>
          <td class="p-2 text-xs">${escapeHtml(memberEmails)}</td>
          <td class="p-2"><span class="text-green-600">${team.active_registrations || 0}</span> / <span class="text-red-600">${team.cancelled_registrations || 0}</span></td>
          <td class="p-2">${escapeHtml(team.course_preference || 'N/A')}</td>
          <td class="p-2 text-xs">${escapeHtml(createdDate)}</td>
        </tr>
      `;
    }).join('');
  }

  function renderTeamBadge(category) {
    const badges = {
      complete: '<span class="badge badge-complete">Complete</span>',
      incomplete: '<span class="badge badge-incomplete">Incomplete</span>',
      faulty: '<span class="badge badge-faulty">Faulty</span>',
      pending: '<span class="badge badge-pending">Pending</span>',
    };
    return badges[category] || '<span class="badge">Unknown</span>';
  }

  async function sendIncompleteReminders() {
    if (!selectedEventId) {
      await showDialogAlert('Please select an event first.', { tone: 'warning', title: 'Action required' });
      return;
    }
    const confirmed = await showDialogConfirm('Send reminder emails to all incomplete teams for this event?', {
      title: 'Send reminders',
      confirmLabel: 'Send emails',
    });
    if (!confirmed) return;
    if (!btnSendReminders) return;

    const originalText = btnSendReminders.textContent;
    btnSendReminders.disabled = true;
    btnSendReminders.innerHTML = '<div class="spinner"></div> Sending...';
    try {
      const res = await apiFetch('/admin/teams/send-incomplete-reminder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event_id: selectedEventId }),
      });
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      const result = await res.json();
      const emailsSent = result.emails_sent || 0;
      const teamCount = result.incomplete_teams_found || 0;
      showToast(`Sent ${emailsSent} reminder(s) to ${teamCount} incomplete team(s).`, 'success');
      if (Array.isArray(result.errors) && result.errors.length) {
        console.warn('Reminder errors:', result.errors);
      }
    } catch (error) {
      console.error('Error sending reminders:', error);
      showToast(`Failed to send reminders: ${error.message}`, 'error');
    } finally {
      btnSendReminders.disabled = false;
      btnSendReminders.textContent = originalText;
    }
  }

  async function releasePlans() {
    if (!selectedEventId) {
      await showDialogAlert('Please select an event first.', { tone: 'warning', title: 'Action required' });
      return;
    }
    const confirmed = await showDialogConfirm('Release final event plans to all paid participants? This will send email notifications.', {
      title: 'Release event plans',
      confirmLabel: 'Release plans',
    });
    if (!confirmed) return;
    if (!btnReleasePlans) return;

    const originalText = btnReleasePlans.textContent;
    btnReleasePlans.disabled = true;
    btnReleasePlans.innerHTML = '<div class="spinner"></div> Releasing...';
    try {
      const res = await apiFetch(`/admin/events/${encodeURIComponent(selectedEventId)}/release-plans`, { method: 'POST' });
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      const result = await res.json();
      const notified = result.participants_notified || 0;
      const total = result.total_paid || 0;
      showToast(`Released plans to ${notified} of ${total} paid participant(s).`, 'success');
      if (Array.isArray(result.errors) && result.errors.length) {
        console.warn('Release plan errors:', result.errors);
      }
    } catch (error) {
      console.error('Error releasing plans:', error);
      showToast(`Failed to release plans: ${error.message}`, 'error');
    } finally {
      btnReleasePlans.disabled = false;
      btnReleasePlans.textContent = originalText;
    }
  }

  const participantsModule = (() => {
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
      paid: 'Paid',
      pending: 'Pending',
      pending_payment: 'Pending',
      covered_by_team: 'Paid by team',
      failed: 'Failed',
      not_applicable: 'N/A',
      unpaid: 'Unpaid',
      unknown: 'Unknown',
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
      female: 'Female',
      male: 'Male',
      non_binary: 'Non-binary',
      diverse: 'Diverse',
      other: 'Other',
      prefer_not_to_say: 'Prefer not to say',
    };
    const TEAM_ROLE_LABELS = {
      creator: 'Captain',
      partner: 'Partner',
    };
    const REGISTRATION_LABELS = {
      confirmed: 'Confirmed',
      pending: 'Pending',
      pending_payment: 'Awaiting payment',
      invited: 'Invited',
      paid: 'Paid',
      refunded: 'Refunded',
      cancelled_by_user: 'Cancelled (participant)',
      cancelled_admin: 'Cancelled (admin)',
      expired: 'Expired',
      draft: 'Draft',
    };

    function init() {
      if (initialized) return;
      const select = $(selectors.select);
      if (select) {
        select.addEventListener('change', (event) => {
          state.eventId = event.target.value || null;
          fetchAndRender(true);
        });
      }
      const searchInput = $(selectors.search);
      if (searchInput) {
        searchInput.addEventListener('input', (event) => {
          state.search = (event.target.value || '').trim();
          render();
        });
      }
      const refreshBtn = $(selectors.refresh);
      if (refreshBtn) {
        refreshBtn.addEventListener('click', (event) => {
          event.preventDefault();
          fetchAndRender(true);
        });
      }
      const toggleBtn = $(selectors.toggle);
      if (toggleBtn) {
        toggleBtn.addEventListener('click', (event) => {
          event.preventDefault();
          state.visible = !state.visible;
          toggleBtn.textContent = state.visible ? 'Hide' : 'Show';
          render();
        });
      }
      const section = $(selectors.section);
      if (section) {
        const head = section.querySelector('thead');
        if (head) {
          head.addEventListener('click', (event) => {
            const th = event.target.closest('th.sortable');
            if (!th) return;
            const key = th.dataset.sort;
            if (!key) return;
            if (state.sortKey === key) {
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

    function setEvents(events) {
      init();
      const select = $(selectors.select);
      const searchInput = $(selectors.search);
      if (!select) return;
      if (!Array.isArray(events) || !events.length) {
        select.innerHTML = '<option value="">No events</option>';
        select.disabled = true;
        state.eventId = null;
        state.rows = [];
        state.summary = { total: 0, by_payment_status: {}, by_registration_status: {} };
        if (searchInput) {
          searchInput.value = '';
          searchInput.disabled = true;
        }
        render();
        return;
      }
      select.disabled = false;
      select.innerHTML = events.map((event) => {
        const labelText = `${event.title || 'Event'}${event.date ? ` (${event.date})` : ''}`;
        return `<option value="${escapeHtml(event.id)}">${escapeHtml(labelText)}</option>`;
      }).join('');
      if (!state.eventId || !events.some((event) => event.id === state.eventId)) {
        state.eventId = events[0].id;
      }
      select.value = state.eventId;
      if (searchInput) {
        searchInput.disabled = !state.eventId;
      }
      fetchAndRender(true);
    }

    function setLoading(active) {
      const el = $(selectors.loading);
      if (!el) return;
      el.classList.toggle('hidden', !active);
    }

    function applyFilters() {
      const rows = state.rows.slice();
      const needle = state.search ? state.search.toLowerCase() : '';
      let filtered = rows;
      if (needle) {
        filtered = rows.filter((row) => row.search_blob.includes(needle));
      }
      filtered.sort((a, b) => {
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

    function sortValue(row, key) {
      const value = row[key];
      if (key === 'updated_display' || key === 'updated_at' || key === 'created_at') {
        return value ? new Date(value).getTime() : null;
      }
      if (typeof value === 'string') return value.toLowerCase();
      if (value === null || value === undefined) return null;
      return value;
    }

    function formatPaymentStatus(status) {
      if (!status) return PAYMENT_LABELS.unknown;
      return PAYMENT_LABELS[status] || status;
    }

    function paymentBadgeClass(status) {
      if (!status) return PAYMENT_BADGES.unknown;
      return PAYMENT_BADGES[status] || PAYMENT_BADGES.unknown;
    }

    function formatGender(value) {
      if (!value) return '';
      return GENDER_LABELS[value] || value;
    }

    function formatRegistrationStatus(status) {
      if (!status) return '';
      return REGISTRATION_LABELS[status] || status;
    }

    function formatTeamRole(row) {
      if (!row.team_id) return 'Solo';
      if (!row.team_role) return '';
      return TEAM_ROLE_LABELS[row.team_role] || row.team_role;
    }

    function updateSortHeaders() {
      const headers = document.querySelectorAll(selectors.headers);
      headers.forEach((th) => {
        const key = th.dataset.sort;
        if (!key) {
          th.removeAttribute('aria-sort');
          return;
        }
        if (key === state.sortKey) {
          th.setAttribute('aria-sort', state.sortDir === 'asc' ? 'ascending' : 'descending');
        } else {
          th.setAttribute('aria-sort', 'none');
        }
      });
    }

    function updateCount(filteredLength, el) {
      if (!el) return;
      const total = state.summary.total || state.rows.length;
      if (!total) {
        el.textContent = '0 participant';
        return;
      }
      if (filteredLength === total) {
        el.textContent = total === 1 ? '1 participant' : `${total} participants`;
      } else {
        el.textContent = `${filteredLength} / ${total} participants`;
      }
    }

    function updateSummary(el) {
      if (!el) return;
      const entries = Object.entries(state.summary.by_payment_status || {});
      if (!entries.length) {
        el.textContent = '';
        return;
      }
      const parts = entries.map(([status, count]) => `${formatPaymentStatus(status)} (${count})`);
      el.textContent = `Payments: ${parts.join(' · ')}`;
    }

    function renderRow(row) {
      const lastName = escapeHtml(row.last_name || '');
      const firstName = escapeHtml(row.first_name || '');
      const email = escapeHtml(row.email || '');
      const gender = escapeHtml(formatGender(row.gender));
      const registration = escapeHtml(formatRegistrationStatus(row.registration_status));
      const paymentLabel = escapeHtml(formatPaymentStatus(row.payment_status));
      const paymentClass = paymentBadgeClass(row.payment_status);
      const teamName = row.team_name ? escapeHtml(row.team_name) : (row.team_id ? '' : 'Solo');
      const teamRole = escapeHtml(formatTeamRole(row));
      const updatedRaw = row.updated_display || row.payment_updated_at || row.updated_at || row.created_at;
      const updated = updatedRaw ? escapeHtml(fmtDate(updatedRaw)) : '';
      return `
        <tr class="border-b border-[#f0f4f7] last:border-b-0">
          <td class="p-2">${lastName}</td>
          <td class="p-2">${firstName}</td>
          <td class="p-2 font-medium text-[#1d4ed8]">${email}</td>
          <td class="p-2">${gender}</td>
          <td class="p-2">${registration}</td>
          <td class="p-2"><span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold ${paymentClass}">${paymentLabel}</span></td>
          <td class="p-2">${teamName || '—'}</td>
          <td class="p-2">${teamRole}</td>
          <td class="p-2">${updated}</td>
        </tr>
      `;
    }

    function render() {
      const wrapper = $(selectors.wrapper);
      const tbody = $(selectors.tbody);
      const empty = $(selectors.empty);
      const countEl = $(selectors.count);
      const summaryEl = $(selectors.summary);
      if (!tbody) return;
      const filtered = applyFilters();
      if (state.visible) {
        if (wrapper) wrapper.classList.remove('hidden');
        if (summaryEl) summaryEl.classList.remove('hidden');
      } else {
        if (wrapper) wrapper.classList.add('hidden');
        if (summaryEl) summaryEl.classList.add('hidden');
      }
      if (!filtered.length) {
        tbody.innerHTML = '';
        if (empty) empty.classList.remove('hidden');
      } else {
        if (empty) empty.classList.add('hidden');
        tbody.innerHTML = filtered.map(renderRow).join('');
      }
      updateCount(filtered.length, countEl);
      updateSummary(summaryEl);
      updateSortHeaders();
    }

    async function fetchAndRender(force) {
      if (!state.eventId) {
        state.rows = [];
        state.summary = { total: 0, by_payment_status: {}, by_registration_status: {} };
        render();
        return;
      }
      if (state.loading && !force) return;
      state.loading = true;
      setLoading(true);
      try {
        const res = await apiFetch(`/admin/events/${encodeURIComponent(state.eventId)}/participants`);
        if (!res.ok) {
          const text = await res.text().catch(() => 'Error');
          throw new Error(text || 'Unable to load participants');
        }
        const data = await res.json().catch(() => ({ participants: [], summary: { total: 0, by_payment_status: {}, by_registration_status: {} } }));
        const participants = Array.isArray(data.participants) ? data.participants : [];
        state.rows = participants.map((participant) => {
          const blob = [participant.full_name, participant.email, participant.team_name, participant.registration_status, participant.payment_status]
            .filter(Boolean)
            .join(' ')
            .toLowerCase();
          return {
            ...participant,
            updated_display: participant.payment_updated_at || participant.updated_at || participant.created_at,
            search_blob: blob,
          };
        });
        state.summary = data.summary || { total: state.rows.length, by_payment_status: {}, by_registration_status: {} };
        if (!state.summary.total) state.summary.total = state.rows.length;
        render();
      } catch (error) {
        console.error('participants.fetch', error);
        showToast('Unable to load participants.', 'error');
      } finally {
        state.loading = false;
        setLoading(false);
      }
    }

    return { init, setEvents };
  })();

  const refundsModule = (() => {
    const state = { eventId: null };
    let initialized = false;
    const selectors = {
      select: '#refunds-event-select',
      load: '#btn-load-refunds',
      process: '#btn-process-refunds',
      overview: '#refunds-overview',
      msg: '#refunds-msg',
    };
    let refundClickHandler = null;

    function init() {
      if (initialized) return;
      const loadBtn = $(selectors.load);
      const processBtn = $(selectors.process);
      const select = $(selectors.select);
      if (loadBtn) loadBtn.addEventListener('click', loadOverview);
      if (processBtn) processBtn.addEventListener('click', processAll);
      if (select) {
        select.addEventListener('change', () => {
          state.eventId = select.value || null;
          if (state.eventId) {
            loadOverview();
          } else {
            resetOverview('Select an event to view refunds.');
          }
        });
      }
      refundClickHandler = async (event) => {
        const btn = event.target && event.target.closest('.btn-refund-one');
        if (!btn) return;
        event.preventDefault();
        const row = btn.closest('tr[data-reg]');
        if (!row) return;
        const registrationId = row.getAttribute('data-reg');
        if (!registrationId) return;
        if (!state.eventId) return;
        btn.disabled = true;
        btn.classList.add('opacity-70');
        const t = showLoadingToast('Processing refund...');
        try {
          const res = await apiFetch(`/events/${encodeURIComponent(state.eventId)}/refunds/process`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ registration_ids: [registrationId] }),
          });
          if (!res.ok) throw new Error(`Request failed (${res.status})`);
          t.update('Done');
          showToast('Refund processed', 'success');
        } catch (error) {
          console.error('Refund single error:', error);
          t.update('Error');
          showToast('Refund failed', 'error');
        } finally {
          t.close();
          btn.disabled = false;
          btn.classList.remove('opacity-70');
          loadOverview();
        }
      };
      document.addEventListener('click', refundClickHandler);
      initialized = true;
    }

    function setEvents(events) {
      init();
      const select = $(selectors.select);
      if (!select) return;
      if (!Array.isArray(events) || !events.length) {
        select.innerHTML = '<option value="">No events</option>';
        select.disabled = true;
        state.eventId = null;
        resetOverview('No refunds to display.');
        return;
      }
      select.disabled = false;
      select.innerHTML = events.map((event) => {
        const labelText = `${event.title || 'Event'}${event.date ? ` (${event.date})` : ''}`;
        return `<option value="${escapeHtml(event.id)}">${escapeHtml(labelText)}</option>`;
      }).join('');
      if (!state.eventId || !events.some((event) => event.id === state.eventId)) {
        state.eventId = events[0].id;
      }
      select.value = state.eventId;
      loadOverview();
    }

    async function loadOverview() {
      const select = $(selectors.select);
      const overview = $(selectors.overview);
      const msg = $(selectors.msg);
      const processBtn = $(selectors.process);
      state.eventId = select && select.value ? select.value : state.eventId;
      if (!state.eventId) {
        resetOverview('Select an event to view refunds.');
        return;
      }
      if (msg) msg.textContent = 'Loading refunds...';
      if (overview) overview.innerHTML = '';
      const t = showLoadingToast('Loading refunds...');
      try {
        const res = await apiFetch(`payments/admin/events/${encodeURIComponent(state.eventId)}/refunds`);
        if (!res.ok) throw new Error(`Request failed (${res.status})`);
        const data = await res.json().catch(() => ({ enabled: false, items: [], total_refund_cents: 0 }));
        if (!data.enabled) {
          resetOverview('Refunds are disabled for this event.');
          if (processBtn) processBtn.classList.add('hidden');
          return;
        }
        const hasItems = Array.isArray(data.items) && data.items.length > 0;
        if (processBtn) processBtn.classList.toggle('hidden', !hasItems);
        if (overview) {
          if (!hasItems) {
            overview.innerHTML = '<div class="text-sm">No refunds due.</div>';
          } else {
            const rows = data.items.map((item) => `
              <tr data-reg="${escapeHtml(item.registration_id)}">
                <td class="p-1">${escapeHtml(item.user_email || '')}</td>
                <td class="p-1">${((item.amount_cents || 0) / 100).toFixed(2)} €</td>
                <td class="p-1 text-xs">${escapeHtml(item.registration_id || '')}</td>
                <td class="p-1"><button class="btn-refund-one bg-[#008080] text-white rounded px-2 py-1 text-xs">Refund</button></td>
              </tr>`).join('');
            overview.innerHTML = `
              <div class="font-semibold mb-2">Total refunds: ${((data.total_refund_cents || 0) / 100).toFixed(2)} €</div>
              <div class="overflow-x-auto mt-2">
                <table class="min-w-full text-sm">
                  <thead>
                    <tr class="bg-[#f0f4f7]">
                      <th class="p-1 text-left">User</th>
                      <th class="p-1 text-left">Amount</th>
                      <th class="p-1 text-left">Registration</th>
                      <th class="p-1 text-left">Action</th>
                    </tr>
                  </thead>
                  <tbody>${rows}</tbody>
                </table>
              </div>`;
          }
        }
        if (msg) msg.textContent = hasItems ? '' : 'No pending refunds.';
      } catch (error) {
        console.error('Error loading refunds:', error);
        resetOverview('Failed to load refunds.');
        showToast('Failed to load refunds.', 'error');
      } finally {
        t.close();
      }
    }

    async function processAll() {
      if (!state.eventId) return;
      const processBtn = $(selectors.process);
      const t = showLoadingToast('Processing refunds...');
      try {
        const res = await apiFetch(`/events/${encodeURIComponent(state.eventId)}/refunds/process`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(`Request failed (${res.status})`);
        t.update(`Processed ${data.processed || 0}`);
        showToast(`Refunds processed: ${data.processed || 0}`, 'success');
      } catch (error) {
        console.error('Error processing refunds:', error);
        t.update('Error');
        showToast('Refund processing failed.', 'error');
      } finally {
        t.close();
        if (processBtn) processBtn.disabled = false;
        loadOverview();
      }
    }

    function resetOverview(message) {
      const overview = $(selectors.overview);
      const msg = $(selectors.msg);
      const processBtn = $(selectors.process);
      if (overview) overview.innerHTML = '';
      if (msg) msg.textContent = message || '';
      if (processBtn) processBtn.classList.add('hidden');
    }

    return { init, setEvents };
  })();

  function init() {
    bindTeamControls();
    participantsModule.init();
    refundsModule.init();
    loadEvents();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
