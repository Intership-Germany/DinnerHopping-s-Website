# Chat Implementation Documentation

## Overview

The chat functionality has been implemented for the DinnerHopping event system. This allows registered participants to communicate with their group members for each course section (starter, main, dessert, after-party).

## Features Implemented

### Frontend (`frontend/public/js/pages/chat.js`)
- **Parse URL Parameters**: Extracts `event_id` and `group_id` from URL query parameters
- **Group Selection**: Displays a dropdown to switch between multiple chat groups when available
- **Message Display**: Shows chat messages with sender information and proper styling
- **Send Messages**: Allows users to send messages to the current group
- **Auto-refresh**: Polls for new messages every 5 seconds
- **User Identification**: Distinguishes current user's messages from others with different styling

### UI (`frontend/public/chat.html`)
- **Dynamic Content**: Removed hardcoded fake conversation
- **Loading State**: Shows spinner while loading
- **Error Handling**: Displays error messages when chat cannot be loaded
- **Group Selector**: Dropdown to switch between groups (hidden when only one group exists)
- **Message Input**: Form with text input and send button (enabled only when chat is loaded)
- **Responsive Design**: Uses Tailwind CSS for responsive layout

### Backend API (Already Implemented)
The backend already has complete chat functionality at `/chats/` endpoints:
- `POST /chats/groups` - Create a new chat group
- `GET /chats/groups` - List user's chat groups
- `GET /chats/groups/{group_id}` - Get group details
- `GET /chats/groups/{group_id}/messages` - List messages in a group
- `POST /chats/messages` - Send a message to a group

## Usage Flow

1. **From Event Page**: Users click "Open Group Chats" button on the event page
2. **Navigate to Chat**: Redirects to `/chat.html?event_id={EVENT_ID}`
3. **Load Groups**: JavaScript fetches all chat groups for the logged-in user
4. **Filter by Event**: If `event_id` is provided, only shows groups for that event
5. **Select Group**: If multiple groups exist, user can select from dropdown
6. **View Messages**: Messages are loaded and displayed with sender information
7. **Send Messages**: User can type and send messages
8. **Auto-refresh**: New messages appear automatically every 5 seconds

## Technical Details

### Authentication
- Uses `window.dh.apiFetch()` for all API calls (handles CSRF tokens automatically)
- Requires user to be logged in (protected by `auth-guard.js`)
- Backend validates that user is a member of the group and registered for the event

### Message Format
```javascript
{
  id: "message_id",
  group_id: "group_id",
  body: "Message text",
  created_at: "2025-01-01T12:00:00Z",
  sender: {
    email: "user@example.com",
    name: "User Name",
    address_public: "Address"
  }
}
```

### Group Format
```javascript
{
  id: "group_id",
  event_id: "event_id",
  section_ref: "starter", // or "main", "dessert", "after_party"
  participants: [
    {
      email: "user@example.com",
      name: "User Name",
      address_public: "Street, City"
    }
  ],
  created_at: "2025-01-01T12:00:00Z",
  created_by: "creator@example.com"
}
```

## Navigation from Event Page

The event page (`frontend/public/js/pages/event.js`) already has the navigation logic:

```javascript
openChatsBtn.addEventListener('click', () => {
    const target = `/chat.html?event_id=${encodeURIComponent(eventId)}`;
    window.location.href = target;
});
```

## Future Enhancements

Potential improvements that could be added:
- Real-time updates using WebSocket instead of polling
- Read receipts for messages
- Typing indicators
- Message timestamps in a more user-friendly format
- Notification when new messages arrive
- Support for attachments/images
- Emoji picker
- Message search functionality
- Pagination for message history

## Testing

To test the chat functionality:
1. Ensure backend is running with database connection
2. Log in as a registered user for an event
3. Navigate to an event page
4. Click "Open Group Chats" button
5. Verify chat interface loads
6. Send messages and verify they appear
7. Open in another browser/incognito window with different user to test real-time updates

## Error Handling

The implementation handles several error cases:
- No chat groups found (user not registered or no groups created)
- Selected group not found
- Authentication failures
- Network errors
- Backend unavailable

Error messages are displayed in a red banner at the top of the chat interface.
