/**
 * Admin Team Management Page
 * Provides UI for monitoring teams, handling incomplete teams, and releasing event plans
 */
(function () {
  const BASE = window.BACKEND_BASE_URL;
  
  let currentTeams = [];
  let currentFilter = 'all';
  let selectedEventId = null;

  // DOM elements
  const eventSelect = document.getElementById('event-select');
  const btnLoadTeams = document.getElementById('btn-load-teams');
  const btnSendReminders = document.getElementById('btn-send-incomplete-reminders');
  const btnReleasePlans = document.getElementById('btn-release-plans');
  const teamsTable = document.getElementById('teams-tbody');
  const loadingMsg = document.getElementById('loading-msg');
  const statusMsg = document.getElementById('status-msg');
  const statsContainer = document.getElementById('stats-container');
  
  // Stats elements
  const statComplete = document.getElementById('stat-complete');
  const statIncomplete = document.getElementById('stat-incomplete');
  const statFaulty = document.getElementById('stat-faulty');
  const statPending = document.getElementById('stat-pending');

  // Filter buttons
  const filterAll = document.getElementById('filter-all');
  const filterComplete = document.getElementById('filter-complete');
  const filterIncomplete = document.getElementById('filter-incomplete');
  const filterFaulty = document.getElementById('filter-faulty');
  const filterPending = document.getElementById('filter-pending');

  /**
   * Initialize page
   */
  async function init() {
    await loadEvents();
    setupEventListeners();
  }

  /**
   * Load events into dropdown
   */
  async function loadEvents() {
    try {
      const res = await window.dh.apiFetch('/events', { method: 'GET' });
      if (!res.ok) throw new Error('Failed to load events');
      const events = await res.json();
      
      eventSelect.innerHTML = '<option value="">-- All Events --</option>';
      events.forEach(event => {
        const opt = document.createElement('option');
        opt.value = event._id || event.id;
        opt.textContent = `${event.title} (${event.date || 'No date'})`;
        eventSelect.appendChild(opt);
      });
    } catch (error) {
      console.error('Error loading events:', error);
      statusMsg.textContent = 'Failed to load events';
      statusMsg.className = 'text-sm text-red-600';
    }
  }

  /**
   * Setup event listeners
   */
  function setupEventListeners() {
    btnLoadTeams.addEventListener('click', loadTeams);
    btnSendReminders.addEventListener('click', sendIncompleteReminders);
    btnReleasePlans.addEventListener('click', releasePlans);

    filterAll.addEventListener('click', () => applyFilter('all'));
    filterComplete.addEventListener('click', () => applyFilter('complete'));
    filterIncomplete.addEventListener('click', () => applyFilter('incomplete'));
    filterFaulty.addEventListener('click', () => applyFilter('faulty'));
    filterPending.addEventListener('click', () => applyFilter('pending'));
  }

  /**
   * Load teams from API
   */
  async function loadTeams() {
    selectedEventId = eventSelect.value || null;
    
    loadingMsg.classList.remove('hidden');
    statusMsg.textContent = '';
    statsContainer.classList.add('hidden');
    
    try {
      const url = selectedEventId 
        ? `/admin/teams/overview?event_id=${selectedEventId}`
        : '/admin/teams/overview';
      
      const res = await window.dh.apiFetch(url, { method: 'GET' });
      if (!res.ok) throw new Error('Failed to load teams');
      
      const data = await res.json();
      currentTeams = data.teams || [];
      
      // Update stats
      statComplete.textContent = data.complete || 0;
      statIncomplete.textContent = data.incomplete || 0;
      statFaulty.textContent = data.faulty || 0;
      statPending.textContent = data.pending || 0;
      
      statsContainer.classList.remove('hidden');
      
      // Show action buttons if event is selected
      if (selectedEventId) {
        btnSendReminders.classList.remove('hidden');
        btnReleasePlans.classList.remove('hidden');
      } else {
        btnSendReminders.classList.add('hidden');
        btnReleasePlans.classList.add('hidden');
      }
      
      renderTeams();
      
      statusMsg.textContent = `Loaded ${currentTeams.length} team(s)`;
      statusMsg.className = 'text-sm text-green-600';
    } catch (error) {
      console.error('Error loading teams:', error);
      statusMsg.textContent = 'Failed to load teams: ' + error.message;
      statusMsg.className = 'text-sm text-red-600';
      teamsTable.innerHTML = '<tr><td colspan="8" class="p-4 text-center text-red-500">Error loading teams</td></tr>';
    } finally {
      loadingMsg.classList.add('hidden');
    }
  }

  /**
   * Apply filter to teams list
   */
  function applyFilter(filter) {
    currentFilter = filter;
    
    // Update active button
    [filterAll, filterComplete, filterIncomplete, filterFaulty, filterPending].forEach(btn => {
      btn.classList.remove('font-bold');
    });
    
    switch(filter) {
      case 'all': filterAll.classList.add('font-bold'); break;
      case 'complete': filterComplete.classList.add('font-bold'); break;
      case 'incomplete': filterIncomplete.classList.add('font-bold'); break;
      case 'faulty': filterFaulty.classList.add('font-bold'); break;
      case 'pending': filterPending.classList.add('font-bold'); break;
    }
    
    renderTeams();
  }

  /**
   * Render teams table
   */
  function renderTeams() {
    const filtered = currentFilter === 'all' 
      ? currentTeams 
      : currentTeams.filter(t => t.category === currentFilter);
    
    if (filtered.length === 0) {
      teamsTable.innerHTML = '<tr><td colspan="8" class="p-4 text-center text-gray-500">No teams match the current filter</td></tr>';
      return;
    }
    
    teamsTable.innerHTML = filtered.map(team => {
      const categoryBadge = getCategoryBadge(team.category);
      const memberEmails = (team.members || []).map(m => m.email || 'unknown').join(', ');
      const createdDate = team.created_at ? new Date(team.created_at).toLocaleDateString() : 'Unknown';
      
      return `
        <tr class="border-b hover:bg-gray-50">
          <td class="p-2 text-xs font-mono">${team.team_id.substring(0, 8)}...</td>
          <td class="p-2">${team.event_title || 'Unknown Event'}</td>
          <td class="p-2">${team.status || 'unknown'}</td>
          <td class="p-2">${categoryBadge}</td>
          <td class="p-2 text-xs">${memberEmails}</td>
          <td class="p-2">
            <span class="text-green-600">${team.active_registrations || 0}</span> / 
            <span class="text-red-600">${team.cancelled_registrations || 0}</span>
          </td>
          <td class="p-2">${team.course_preference || 'N/A'}</td>
          <td class="p-2 text-xs">${createdDate}</td>
        </tr>
      `;
    }).join('');
  }

  /**
   * Get category badge HTML
   */
  function getCategoryBadge(category) {
    const badges = {
      complete: '<span class="badge badge-complete">Complete</span>',
      incomplete: '<span class="badge badge-incomplete">Incomplete</span>',
      faulty: '<span class="badge badge-faulty">Faulty</span>',
      pending: '<span class="badge badge-pending">Pending</span>'
    };
    return badges[category] || '<span class="badge">Unknown</span>';
  }

  /**
   * Send reminders to incomplete teams
   */
  async function sendIncompleteReminders() {
    if (!selectedEventId) {
      alert('Please select an event first');
      return;
    }
    
    if (!confirm('Send reminder emails to all incomplete teams for this event?')) {
      return;
    }
    
    const originalText = btnSendReminders.textContent;
    btnSendReminders.disabled = true;
    btnSendReminders.innerHTML = '<div class="spinner"></div> Sending...';
    
    try {
      const res = await window.dh.apiFetch('/admin/teams/send-incomplete-reminder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event_id: selectedEventId })
      });
      
      if (!res.ok) throw new Error('Failed to send reminders');
      
      const result = await res.json();
      
      if (window.showToast) {
        window.showToast(
          `Sent ${result.emails_sent} reminder(s) to ${result.incomplete_teams_found} incomplete team(s)`,
          'success'
        );
      } else {
        alert(`Success! Sent ${result.emails_sent} reminders.`);
      }
      
      if (result.errors && result.errors.length > 0) {
        console.error('Some emails failed:', result.errors);
      }
    } catch (error) {
      console.error('Error sending reminders:', error);
      if (window.showToast) {
        window.showToast('Failed to send reminders: ' + error.message, 'error');
      } else {
        alert('Failed to send reminders: ' + error.message);
      }
    } finally {
      btnSendReminders.disabled = false;
      btnSendReminders.textContent = originalText;
    }
  }

  /**
   * Release event plans to all paid participants
   */
  async function releasePlans() {
    if (!selectedEventId) {
      alert('Please select an event first');
      return;
    }
    
    if (!confirm('Release final event plans to all paid participants? This will send email notifications.')) {
      return;
    }
    
    const originalText = btnReleasePlans.textContent;
    btnReleasePlans.disabled = true;
    btnReleasePlans.innerHTML = '<div class="spinner"></div> Releasing...';
    
    try {
      const res = await window.dh.apiFetch(`/admin/events/${selectedEventId}/release-plans`, {
        method: 'POST'
      });
      
      if (!res.ok) throw new Error('Failed to release plans');
      
      const result = await res.json();
      
      if (window.showToast) {
        window.showToast(
          `Released plans to ${result.participants_notified} of ${result.total_paid} paid participant(s)`,
          'success'
        );
      } else {
        alert(`Success! Notified ${result.participants_notified} participants.`);
      }
      
      if (result.errors && result.errors.length > 0) {
        console.error('Some emails failed:', result.errors);
      }
    } catch (error) {
      console.error('Error releasing plans:', error);
      if (window.showToast) {
        window.showToast('Failed to release plans: ' + error.message, 'error');
      } else {
        alert('Failed to release plans: ' + error.message);
      }
    } finally {
      btnReleasePlans.disabled = false;
      btnReleasePlans.textContent = originalText;
    }
  }

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
