# Manual Payment and Invitation Email Features

## Overview
This document describes the implementation of two new features:
1. Invitation email sending when inviting friends during event registration
2. Manual payment method for users who cannot use PayPal or Stripe

## 1. Invitation Email Feature

### What Changed
When users register for an event and invite friends via the `invited_emails` field, the system now:
- Creates invitation records in the database with `event_id` stored
- Sends invitation emails to each invited friend
- Returns a list of successfully sent invitations in the response

### Backend Changes
**File**: `backend/app/routers/events.py`
- Added `send_email` import
- Modified invitation creation loop to:
  - Store `event_id` in invitation document
  - Send email to invited friend with invitation link
  - Include event title in email body and template variables

### Email Content
The invitation email includes:
- Event title
- Invitation acceptance link
- Instructions for new users without accounts
- Template variables for customization via email templates

### API Response
The `/events/{event_id}/register` endpoint now returns:
```json
{
  "status": "registered",
  "registration_ids": ["..."],
  "invitations_sent": ["friend1@example.com", "friend2@example.com"],
  "payment_link": "..."
}
```

## 2. Manual Payment Method

### What Changed
Added "other (contact us)" as a payment provider option for users who cannot use online payment methods. This allows:
- Users to request manual payment approval
- Admins to receive email notifications
- Admins to approve/reject payments from dashboard
- Registration to be finalized upon admin approval

### Backend Changes

#### Payment Provider Enum
**File**: `backend/app/routers/payments.py`
- Added `other = 'other'` to `PaymentProvider` enum

#### Provider List Endpoint
**File**: `backend/app/routers/payments.py`
- Modified `/payments/providers` to always include 'other' in the list

#### Payment Creation
**File**: `backend/app/routers/payments.py`
- Added handler for `provider='other'` in `/payments/create` endpoint
- Creates payment with status `"waiting_manual_approval"`
- Sends email notification to all admin users
- Returns `next_action` with type `"manual_approval"` and user-facing message

#### Admin Endpoints
**File**: `backend/app/routers/payments.py`

New endpoints for admin users only:

1. **List Manual Payments**: `GET /payments/admin/manual-payments`
   - Query parameter: `status` (optional, defaults to listing all)
   - Returns list of payments with user and event details

2. **Approve Payment**: `POST /payments/admin/manual-payments/{payment_id}/approve`
   - Updates payment status to `"succeeded"`
   - Finalizes registration (sets status to `"paid"`)
   - Records approval metadata

3. **Reject Payment**: `POST /payments/admin/manual-payments/{payment_id}/reject`
   - Updates payment status to `"failed"`
   - Records rejection metadata

### Frontend Changes

#### Payment Provider Selection
**File**: `frontend/public/js/pages/event.js`
- Updated `fetchProviders()` to include 'other' in allowed providers
- Updated `buildProviderChooser()` to display "Other (Contact Us)" label
- Modified payment flow to:
  - Handle `next_action.type === 'manual_approval'`
  - Show alert message to user
  - Reload page to reflect updated status

#### Admin Dashboard
**Files**: 
- `frontend/public/admin-dashboard.html`
- `frontend/public/js/admin-dashboard.js`

Added new section "Manual Payment Approvals" with:
- "Load Pending Payments" button to fetch manual payments
- List of pending payments with details (event, user, amount, date)
- "Approve" and "Reject" buttons for each payment
- Real-time updates (removes approved/rejected payments from list)
- Toast notifications for success/error feedback

### Email Notifications

#### To Admins (on manual payment request)
Subject: `Manual Payment Pending: {event_title}`

Content includes:
- Event title
- User email
- Payment amount and currency
- Payment ID
- Request to review in admin dashboard

### Database Schema

#### Payments Collection
Manual payments have:
```javascript
{
  provider: "other",
  status: "waiting_manual_approval", // or "succeeded"/"failed"
  meta: {
    user_email: "user@example.com",
    event_id: "...",
    approved_by: "admin",  // added on approval
    approved_at: "...",    // added on approval
    rejected_by: "admin",  // added on rejection
    rejected_at: "..."     // added on rejection
  }
}
```

#### Invitations Collection
Invitations now include:
```javascript
{
  registration_id: ObjectId("..."),
  event_id: ObjectId("..."),  // NEW
  invited_email: "friend@example.com",
  token_hash: "...",
  status: "pending",
  created_at: ISODate("..."),
  expires_at: ISODate("...")
}
```

## Testing

### Test Files
1. **`backend/tests/test_manual_payments.py`**
   - `test_manual_payment_flow`: Complete flow from creation to approval
   - `test_manual_payment_rejection`: Test payment rejection
   - `test_other_provider_in_list`: Verify 'other' is in providers list

2. **`backend/tests/test_invitation_emails.py`**
   - `test_invitation_email_created`: Single invitation with event_id
   - `test_multiple_invitations`: Multiple invitations at once
   - `test_registration_without_invitations`: Backward compatibility

### Running Tests
```bash
cd backend
python -m pytest tests/test_manual_payments.py tests/test_invitation_emails.py -v
```

All tests pass successfully.

## Usage

### For Users
1. **Inviting Friends**:
   - Register for an event
   - Add friend emails in the invitation field
   - Friends receive invitation emails automatically

2. **Manual Payment**:
   - Select "Other (Contact Us)" as payment method
   - Receive confirmation that request was submitted
   - Wait for admin approval
   - Receive confirmation email when approved

### For Admins
1. **Review Manual Payments**:
   - Navigate to Admin Dashboard
   - Scroll to "Manual Payment Approvals" section
   - Click "Load Pending Payments"
   - Review payment details

2. **Approve Payment**:
   - Click "Approve" button for the payment
   - User's registration is automatically finalized
   - Payment is marked as succeeded

3. **Reject Payment**:
   - Click "Reject" button for the payment
   - Payment is marked as failed
   - User can create new payment if needed

## Security Considerations
- Only admin users can access manual payment management endpoints
- Email notifications do not expose sensitive payment details
- Admin actions are logged in payment metadata
- All endpoints require authentication

## Future Enhancements
Potential improvements:
- Email template customization for manual payment notifications
- Automatic reminders for pending manual payments
- Payment status page for users to track their manual payments
- Support for partial payments or payment plans
- Integration with additional payment providers
