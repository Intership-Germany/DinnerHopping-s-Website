/**
 * Team Invitation Page
 * Handles team invitation accept/decline workflow
 */
(async function () {
  const BASE = window.BACKEND_BASE_URL;
  const apiFetch = (window.dh && window.dh.apiFetch) || fetch;

  const loadingSpinner = document.getElementById('loadingSpinner');
  const invitationContent = document.getElementById('invitationContent');
  const errorContent = document.getElementById('errorContent');
  const errorMessage = document.getElementById('errorMessage');
  const teamDetails = document.getElementById('teamDetails');
  const actionButtons = document.getElementById('actionButtons');
  const statusMessage = document.getElementById('statusMessage');

  // Parse URL parameters
  const params = new URLSearchParams(window.location.search);
  const teamId = params.get('team_id');
  const action = params.get('action');

  function showError(message) {
    loadingSpinner.classList.add('hidden');
    invitationContent.classList.add('hidden');
    errorContent.classList.remove('hidden');
    errorMessage.textContent = message;
  }

  function showStatus(message, type = 'info') {
    statusMessage.textContent = message;
    statusMessage.classList.remove('hidden', 'bg-blue-50', 'text-blue-700', 'bg-green-50', 'text-green-700', 'bg-red-50', 'text-red-700');
    
    if (type === 'success') {
      statusMessage.classList.add('bg-green-50', 'text-green-700');
    } else if (type === 'error') {
      statusMessage.classList.add('bg-red-50', 'text-red-700');
    } else {
      statusMessage.classList.add('bg-blue-50', 'text-blue-700');
    }
  }

  async function loadTeamDetails() {
    try {
      const res = await apiFetch(`${BASE}/registrations/teams/${teamId}`, {
        method: 'GET',
        credentials: 'include',
      });

      if (!res.ok) {
        throw new Error('Failed to load team details');
      }

      const team = await res.json();
      return team;
    } catch (error) {
      throw new Error('Unable to load team details. The invitation may be invalid or expired.');
    }
  }

  async function handleDecline() {
    if (!confirm('Are you sure you want to decline this team invitation? The team creator will be notified.')) {
      return;
    }

    try {
      actionButtons.querySelectorAll('button').forEach(btn => btn.disabled = true);
      
      const res = await apiFetch(`${BASE}/registrations/teams/${teamId}/decline`, {
        method: 'POST',
        credentials: 'include',
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Failed to decline invitation');
      }

      showStatus('You have successfully declined the team invitation. The team creator has been notified.', 'success');
      
      // Hide action buttons after successful decline
      actionButtons.classList.add('hidden');
      
      // Redirect to home after 3 seconds
      setTimeout(() => {
        window.location.href = '/home.html';
      }, 3000);
    } catch (error) {
      showStatus(error.message, 'error');
      actionButtons.querySelectorAll('button').forEach(btn => btn.disabled = false);
    }
  }

  function renderTeamDetails(team) {
    const eventTitle = team.event_title || 'Upcoming Event';
    const creatorEmail = team.created_by_email || 'Unknown';
    const eventDate = team.event_date || 'TBD';

    teamDetails.innerHTML = `
      <div class="border-l-4 border-emerald-500 pl-4">
        <h3 class="font-semibold text-lg text-gray-900">${eventTitle}</h3>
        <p class="text-gray-600 mt-1">Date: ${eventDate}</p>
        <p class="text-gray-600 mt-1">Team Creator: ${creatorEmail}</p>
      </div>
      
      <div class="bg-blue-50 border border-blue-200 rounded-lg p-4">
        <p class="text-blue-900">
          <strong>Note:</strong> You have been invited to join this team. An invitation email with an accept link has been sent to your address.
          To accept, follow the link in that email. If you cannot find the email, check your spam folder or contact the creator. If you decline, the team creator will be notified and can find a replacement partner.
        </p>
      </div>
    `;

    // Only show decline button (accept is automatic)
    actionButtons.innerHTML = `
      <button 
        id="declineBtn" 
        class="flex-1 px-6 py-3 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
      >
        Decline Invitation
      </button>
      <a 
        href="/home.html" 
        class="flex-1 px-6 py-3 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition-colors text-center"
      >
        Go to Home
      </a>
    `;

    document.getElementById('declineBtn').addEventListener('click', handleDecline);
  }

  // Main initialization
  async function init() {
    if (!teamId) {
      showError('No team ID provided in the URL.');
      return;
    }

    try {
      // If action is decline, go straight to declining
      if (action === 'decline') {
        const team = await loadTeamDetails();
        loadingSpinner.classList.add('hidden');
        invitationContent.classList.remove('hidden');
        renderTeamDetails(team);
        
        // Auto-trigger decline flow
        showStatus('Click "Decline Invitation" below to confirm you cannot participate.', 'info');
      } else {
        // Just show the invitation details
        const team = await loadTeamDetails();
        loadingSpinner.classList.add('hidden');
        invitationContent.classList.remove('hidden');
        renderTeamDetails(team);
      }
    } catch (error) {
      showError(error.message);
    }
  }

  // Start the app
  init();
})();
