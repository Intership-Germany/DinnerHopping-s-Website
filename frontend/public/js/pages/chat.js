
/**
 * Chat page logic
 * Responsibilities:
 *  - Parse URL parameters (event_id, group_id)
 *  - Fetch user's chat groups for the event
 *  - Load and display messages for selected group
 *  - Implement send message functionality
 *  - Auto-refresh messages with polling
 */

(async () => {
	const qs = new URLSearchParams(window.location.search);
	const eventId = qs.get('event_id');
	const groupId = qs.get('group_id');

	// DOM elements
	const chatTitle = document.getElementById('chatTitle');
	const chatLocation = document.getElementById('chatLocation');
	const messagesContainer = document.getElementById('messagesContainer');
	const messageForm = document.getElementById('messageForm');
	const messageInput = document.getElementById('messageInput');
	const sendButton = document.getElementById('sendButton');
	const groupSelector = document.getElementById('groupSelector');
	const loadingSpinner = document.getElementById('loadingSpinner');
	const errorMessage = document.getElementById('errorMessage');

	// Use namespaced dh.apiFetch if available
	const apiFetch = (window.dh && window.dh.apiFetch) || window.apiFetch || fetch;
	const initCsrf = (window.dh && window.dh.initCsrf) || window.initCsrf || (async () => {});

	let currentGroupId = groupId;
	let currentGroup = null;
	let userGroups = [];
	let pollingInterval = null;
	let currentUserEmail = null;

	// Initialize CSRF
	try {
		await initCsrf();
	} catch (e) {
		console.warn('CSRF init failed:', e);
	}

	// Helper to fetch user profile to get current user email
	async function getCurrentUserEmail() {
		try {
			const resp = await apiFetch('/users/profile', {
				headers: { 'Accept': 'application/json' }
			});
			if (resp.ok) {
				const data = await resp.json();
				return data.email;
			}
		} catch (e) {
			console.warn('Failed to fetch user email:', e);
		}
		return null;
	}

	// Fetch user's chat groups
	async function loadGroups() {
		try {
			const resp = await apiFetch('/chats/groups', {
				headers: { 'Accept': 'application/json' }
			});
			if (!resp.ok) {
				throw new Error(`Failed to load groups: ${resp.status}`);
			}
			const allGroups = await resp.json();
			// Filter by event_id if provided
			if (eventId) {
				userGroups = allGroups.filter(g => g.event_id === eventId);
			} else {
				userGroups = allGroups;
			}
			return userGroups;
		} catch (e) {
			console.error('Error loading groups:', e);
			throw e;
		}
	}

	// Fetch group details
	async function loadGroupDetails(gid) {
		try {
			const resp = await apiFetch(`/chats/groups/${gid}`, {
				headers: { 'Accept': 'application/json' }
			});
			if (!resp.ok) {
				throw new Error(`Failed to load group: ${resp.status}`);
			}
			return await resp.json();
		} catch (e) {
			console.error('Error loading group details:', e);
			throw e;
		}
	}

	// Fetch messages for a group
	async function loadMessages(gid) {
		try {
			const resp = await apiFetch(`/chats/groups/${gid}/messages`, {
				headers: { 'Accept': 'application/json' }
			});
			if (!resp.ok) {
				throw new Error(`Failed to load messages: ${resp.status}`);
			}
			return await resp.json();
		} catch (e) {
			console.error('Error loading messages:', e);
			throw e;
		}
	}

	// Send a message
	async function sendMessage(gid, body) {
		try {
			const resp = await apiFetch('/chats/messages', {
				method: 'POST',
				headers: {
					'Accept': 'application/json',
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					group_id: gid,
					body: body
				})
			});
			if (!resp.ok) {
				throw new Error(`Failed to send message: ${resp.status}`);
			}
			return await resp.json();
		} catch (e) {
			console.error('Error sending message:', e);
			throw e;
		}
	}

	// Render messages in the UI
	function renderMessages(messages) {
		messagesContainer.innerHTML = '';
		messages.forEach(msg => {
			const isCurrentUser = msg.sender?.email === currentUserEmail;
			const msgDiv = document.createElement('div');
			msgDiv.className = `flex items-end gap-2 ${isCurrentUser ? 'justify-end' : ''}`;

			// Avatar
			if (!isCurrentUser) {
				const avatar = document.createElement('img');
				const initial = (msg.sender?.name || msg.sender?.email || '?')[0].toUpperCase();
				avatar.src = `https://placehold.co/32x32/008080/fff?text=${initial}`;
				avatar.alt = msg.sender?.name || msg.sender?.email || 'User';
				avatar.className = 'w-8 h-8 rounded-full';
				msgDiv.appendChild(avatar);
			}

			// Message bubble
			const bubble = document.createElement('div');
			bubble.className = `chat-bubble ${isCurrentUser ? 'bg-[#008080] text-white' : 'bg-white'} rounded-2xl px-4 py-2 shadow text-sm`;
			bubble.textContent = msg.body || '';
			msgDiv.appendChild(bubble);

			// Avatar for current user (on right side)
			if (isCurrentUser) {
				const avatar = document.createElement('img');
				const initial = (msg.sender?.name || msg.sender?.email || '?')[0].toUpperCase();
				avatar.src = `https://placehold.co/32x32/ffc241/fff?text=${initial}`;
				avatar.alt = msg.sender?.name || msg.sender?.email || 'You';
				avatar.className = 'w-8 h-8 rounded-full';
				msgDiv.appendChild(avatar);
			}

			messagesContainer.appendChild(msgDiv);
		});

		// Scroll to bottom
		messagesContainer.parentElement.scrollTop = messagesContainer.parentElement.scrollHeight;
	}

	// Update chat header with group details
	function updateChatHeader(group) {
		if (chatTitle) {
			const sectionName = (group.section_ref || 'Group').replace(/_/g, ' ');
			chatTitle.textContent = `${sectionName} Chat`;
		}
		if (chatLocation && group.participants && group.participants.length > 0) {
			// Try to find host address or just show participant count
			const hostWithAddress = group.participants.find(p => p.address_public);
			if (hostWithAddress) {
				chatLocation.textContent = hostWithAddress.address_public;
			} else {
				chatLocation.textContent = `${group.participants.length} participants`;
			}
		}
	}

	// Populate group selector
	function populateGroupSelector(groups) {
		if (!groupSelector || groups.length <= 1) {
			if (groupSelector) groupSelector.classList.add('hidden');
			return;
		}
		groupSelector.classList.remove('hidden');
		const select = groupSelector.querySelector('select');
		if (!select) return;

		select.innerHTML = '';
		groups.forEach(g => {
			const option = document.createElement('option');
			option.value = g.id;
			const sectionName = (g.section_ref || 'Group').replace(/_/g, ' ');
			option.textContent = sectionName;
			if (g.id === currentGroupId) {
				option.selected = true;
			}
			select.appendChild(option);
		});

		select.addEventListener('change', (e) => {
			const newGroupId = e.target.value;
			// Update URL and reload
			const url = new URL(window.location);
			url.searchParams.set('group_id', newGroupId);
			window.location.href = url.toString();
		});
	}

	// Load and display chat
	async function loadChat() {
		try {
			if (loadingSpinner) loadingSpinner.classList.remove('hidden');
			if (errorMessage) errorMessage.classList.add('hidden');

			// Get current user email
			currentUserEmail = await getCurrentUserEmail();

			// Load groups
			const groups = await loadGroups();

			if (groups.length === 0) {
				throw new Error('No chat groups found. You may not be registered for this event or no groups have been created yet.');
			}

			// Select group
			if (!currentGroupId && groups.length > 0) {
				currentGroupId = groups[0].id;
			}

			const selectedGroup = groups.find(g => g.id === currentGroupId);
			if (!selectedGroup) {
				throw new Error('Selected group not found');
			}

			// Load group details and messages
			currentGroup = await loadGroupDetails(currentGroupId);
			const messages = await loadMessages(currentGroupId);

			// Update UI
			updateChatHeader(currentGroup);
			populateGroupSelector(groups);
			renderMessages(messages);

			// Enable form
			if (messageInput) messageInput.disabled = false;
			if (sendButton) sendButton.disabled = false;

			if (loadingSpinner) loadingSpinner.classList.add('hidden');
		} catch (e) {
			console.error('Error loading chat:', e);
			if (loadingSpinner) loadingSpinner.classList.add('hidden');
			if (errorMessage) {
				errorMessage.textContent = e.message || 'Failed to load chat';
				errorMessage.classList.remove('hidden');
			}
		}
	}

	// Handle message submission
	if (messageForm) {
		messageForm.addEventListener('submit', async (e) => {
			e.preventDefault();
			if (!messageInput || !currentGroupId) return;

			const body = messageInput.value.trim();
			if (!body) return;

			// Disable form while sending
			messageInput.disabled = true;
			sendButton.disabled = true;

			try {
				await sendMessage(currentGroupId, body);
				messageInput.value = '';
				// Reload messages
				const messages = await loadMessages(currentGroupId);
				renderMessages(messages);
			} catch (e) {
				alert('Failed to send message: ' + e.message);
			} finally {
				messageInput.disabled = false;
				sendButton.disabled = false;
				messageInput.focus();
			}
		});
	}

	// Auto-refresh messages every 5 seconds
	function startPolling() {
		pollingInterval = setInterval(async () => {
			if (!currentGroupId) return;
			try {
				const messages = await loadMessages(currentGroupId);
				renderMessages(messages);
			} catch (e) {
				console.error('Polling error:', e);
			}
		}, 5000);
	}

	// Cleanup on page unload
	window.addEventListener('beforeunload', () => {
		if (pollingInterval) {
			clearInterval(pollingInterval);
		}
	});

	// Initialize
	await loadChat();
	startPolling();
})();
