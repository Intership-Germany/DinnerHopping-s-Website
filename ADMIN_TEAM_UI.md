# Admin Team Management Dashboard - UI Overview

## Page Structure

```
┌─────────────────────────────────────────────────────────────┐
│                     DinnerHopping Header                     │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│  Admin Navigation Bar                                        │
│  [Dashboard] [Email Templates] [Chat] [Team Management]     │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│  🎯 Team Management                                          │
│  Monitor and manage team registrations, handle incomplete   │
│  teams, and track team status.                              │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│  📊 Teams Overview                                           │
│  ┌──────────────┬────────────────────────────────────┐     │
│  │ Select Event │ [Event Dropdown ▼]                 │     │
│  └──────────────┴────────────────────────────────────┘     │
│  [Load Teams] [Send Reminders] [Release Plans]             │
│                                                              │
│  ┌─────────┬─────────┬─────────┬─────────┐                │
│  │   35    │    5    │    2    │    0    │                │
│  │Complete │Incomp.. │ Faulty  │ Pending │                │
│  └─────────┴─────────┴─────────┴─────────┘                │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│  📋 Teams List                                               │
│  [All] [Complete] [Incomplete] [Faulty] [Pending]          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ID      │ Event  │ Status  │ Category │ Members    │  │
│  ├──────────────────────────────────────────────────────┤  │
│  │ 507f... │ Summer │ pending │ Complete │ 2 / 0      │  │
│  │ 61bc... │ Summer │ incomp. │ Incomp.  │ 1 / 1      │  │
│  │ 72ed... │ Winter │ cancld  │ Faulty   │ 0 / 2      │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Features

### 1. Event Selection
- Dropdown to filter teams by specific event
- "All Events" option to view across all events
- Load button to refresh data

### 2. Quick Actions
Three primary action buttons (visible when event selected):

**Send Incomplete Team Reminders**
- Sends bulk emails to team creators with incomplete teams
- Prompts them to find replacement partners
- Shows success/error feedback

**Release Event Plans**
- Sends final schedule to all paid participants
- Includes link to view their personalized plan
- Confirms number of notifications sent

### 3. Statistics Dashboard
Four color-coded cards showing:
- 🟢 Complete Teams: Both members paid and active
- 🟠 Incomplete Teams: One member cancelled, needs replacement
- 🔴 Faulty Teams: Both members cancelled after payment
- 🔵 Pending Teams: Awaiting payment or confirmation

### 4. Team List Table
Displays all teams with columns:
- **Team ID**: First 8 characters of MongoDB ObjectId
- **Event**: Event title
- **Status**: Team status (pending, incomplete, cancelled)
- **Category**: Computed category (complete/incomplete/faulty/pending)
- **Members**: Team member emails
- **Active/Cancelled**: Count of active vs cancelled registrations
- **Course**: Cooking course preference
- **Created**: Registration date

### 5. Filtering
Quick filter buttons to show only:
- All teams
- Complete teams only
- Incomplete teams only
- Faulty teams only
- Pending teams only

## Color Scheme

The UI uses the DinnerHopping brand colors:
- Primary: `#f46f47` (coral/orange)
- Accent: `#008080` (teal)
- Background: `#f0f4f7` (light blue-gray)
- Text: `#172a3a` (dark blue)

Status badges use semantic colors:
- Complete: Green (#e8f5e9 background, #1b5e20 text)
- Incomplete: Orange (#fff3e0 background, #e65100 text)
- Faulty: Red (#ffebee background, #c62828 text)
- Pending: Blue (#e3f2fd background, #1565c0 text)

## User Interactions

### Load Teams Flow
1. Admin selects an event from dropdown (or "All Events")
2. Clicks "Load Teams" button
3. Loading spinner appears
4. Statistics cards update with counts
5. Table populates with team data
6. Action buttons become visible (if event selected)

### Send Reminders Flow
1. Admin clicks "Send Incomplete Team Reminders"
2. Confirmation dialog appears
3. Button shows loading spinner
4. Request sent to `/admin/teams/send-incomplete-reminder`
5. Success toast shows number of emails sent
6. Button returns to normal state

### Release Plans Flow
1. Admin clicks "Release Event Plans"
2. Confirmation dialog appears
3. Button shows loading spinner
4. Request sent to `/admin/events/{event_id}/release-plans`
5. Success toast shows number of participants notified
6. Button returns to normal state

### Filter Teams Flow
1. Admin clicks a filter button (e.g., "Incomplete")
2. Button becomes bold to indicate active filter
3. Table updates to show only matching teams
4. Empty state message if no teams match

## Responsive Design

The dashboard is fully responsive:
- Desktop (>768px): Multi-column layouts, full table
- Tablet (768px): Stacked sections, scrollable table
- Mobile (<768px): Single column, horizontal scroll for table

## Error Handling

The UI handles several error states:
- Network errors: "Failed to load teams" message
- Empty states: "No teams match the current filter"
- API errors: Toast notifications with error details
- Authentication errors: Redirects to login (via auth-guard.js)

## Navigation

The admin dashboard is integrated into the site navigation:
- Header includes "Team Management" link
- Admin sub-navigation bar links to:
  - Dashboard (event management)
  - Email Templates
  - Chat Management
  - Team Management (current page)

## Data Refresh

Data is loaded on-demand:
- Initial page load: No data shown
- After "Load Teams": Data cached in memory
- Filters: Applied client-side (no API calls)
- Refresh: Click "Load Teams" again

## Accessibility

The interface includes:
- Semantic HTML5 elements
- ARIA labels for interactive elements
- Keyboard navigation support
- High contrast color ratios
- Clear focus indicators

## Performance

The dashboard is optimized for:
- Client-side filtering (no backend calls)
- Lazy loading of team details
- Minimal re-renders
- Efficient table rendering

## Future Enhancements

Potential improvements:
- Real-time updates via WebSocket
- Export to CSV/Excel
- Advanced filtering (dietary, location, course)
- Pagination for large team lists
- Team detail modal with full information
- Bulk actions on selected teams
