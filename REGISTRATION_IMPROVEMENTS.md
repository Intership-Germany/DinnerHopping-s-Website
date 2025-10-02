# Registration & Payment Workflow Improvements

This document describes the improvements made to prevent simultaneous registrations and strengthen the registration-payment workflow.

## Overview

This implementation addresses recurring issues around user registrations and payments by:

1. **Preventing Simultaneous Registrations**: Users can only have one active registration at a time
2. **Strengthening Payment Validation**: Server-side amount validation prevents mismatches
3. **Adding Audit Logging**: Complete audit trail for compliance and debugging
4. **Improving User Communication**: Lifecycle notifications for key events
5. **Normalizing Status Management**: Clear FSM with proper state transitions

## Key Features

### Single Active Registration Rule (Option A)

Users cannot register for a second event while having an active registration for another event. 

**Active statuses**: Any status except `cancelled_by_user` or `cancelled_admin`

**Behavior**:
- ✅ User can update their registration for the same event
- ✅ Cancelled registrations don't block new registrations
- ❌ User blocked from registering for Event B while registered for Event A

**Error Response (409 Conflict)**:
```json
{
  "message": "You already have an active registration for Event X...",
  "existing_registration": {
    "registration_id": "...",
    "event_id": "...",
    "event_title": "Event X",
    "status": "pending_payment"
  }
}
```

### Payment Amount Validation

All payment creation requests are validated server-side:

```python
expected_amount = event.fee_cents * team_size
```

Mismatches are rejected with HTTP 400.

### Registration Statuses

Normalized status values:
- `pending_payment` - Registration created, awaiting payment
- `paid` - Payment successful
- `confirmed` - Event confirmed (optional state)
- `cancelled_by_user` - User cancelled
- `cancelled_admin` - Admin cancelled
- `failed` - Payment failed
- `invited` - Partner invited to team (not yet accepted)

### Audit Logging

All state changes are logged to `audit_logs` collection:

```javascript
{
  entity_type: 'registration' | 'payment',
  entity_id: '...',
  action: 'created' | 'status_change' | 'cancelled' | 'payment_success',
  actor: 'user@example.com',
  timestamp: ISODate(...),
  old_state: { status: 'pending_payment' },
  new_state: { status: 'paid' },
  reason: 'Payment succeeded',
  ip_address: '...' // optional
}
```

### Notification System

Email notifications sent for:
- `registration_created` - Registration created (pending_payment)
- `payment_succeeded` - Payment successful (existing)
- `payment_failed` - Payment failed
- `registration_cancelled` - Registration cancelled

All notifications use the existing `send_email()` infrastructure.

## API Changes

### Registration Endpoints

**Endpoints**: `POST /registrations/solo`, `POST /registrations/team`

**New Response Field**:
```json
{
  "registration_id": "...",
  "team_size": 1,
  "amount_cents": 500,
  "payment_create_endpoint": "/payments",
  "registration_status": "pending_payment"  // NEW
}
```

**New Error (409 Conflict)**:
Returned when user attempts to register for a second event while having an active registration.

### Frontend Handling

The frontend now detects 409 errors and displays user-friendly messages:

```javascript
if (res.status === 409 && data.existing_registration) {
  const existing = data.existing_registration;
  alert(
    `You already have an active registration for ${existing.event_title}.\n\n` +
    `Status: ${existing.status}\n\n` +
    `Please cancel that registration first, or wait until it completes.`
  );
}
```

## Testing

**Total Tests**: 44 tests (36 original + 8 new)

### New Tests

**Single Active Registration** (5 tests):
- `test_solo_registration_prevents_second_active_registration`
- `test_solo_registration_allows_reregistration_same_event`
- `test_cancelled_registration_allows_new_registration`
- `test_team_registration_prevents_second_active_registration`
- `test_team_registration_blocks_if_partner_has_active_registration`

**Payment Amount Validation** (3 tests):
- `test_payment_amount_must_match_event_fee`
- `test_payment_amount_calculated_for_team`
- `test_payment_with_no_fee_event`

### Running Tests

```bash
cd backend
python -m pytest tests/ -v
```

## Database Changes

### New Collection: audit_logs

Indexes:
- `entity_type`
- `entity_id`
- `timestamp`
- Compound: `(entity_type, entity_id, timestamp)`

**No migration required** - collection and indexes created automatically on startup.

## Deployment

### Prerequisites
- MongoDB connection configured
- SMTP/email service configured (for notifications)

### Steps
1. Deploy backend code
2. Restart application
3. Indexes will be created automatically
4. Deploy frontend code

### Rollback
Safe to rollback - no breaking changes to data model.

## Configuration

All features work out of the box with no additional configuration.

**Optional Environment Variables**:
- Email configuration (existing)
- See `.env.example` for details

## Monitoring

### Metrics to Track
- `registration_conflict_rate` - 409 errors / total registration attempts
- `payment_validation_failures` - 400 errors from payment amount mismatch
- `audit_log_growth` - Monitor audit_logs collection size

### Logs to Monitor
- Audit logs: `db.audit_logs.find({}).sort({timestamp: -1})`
- Failed registrations: Search logs for "409" status
- Payment validation errors: Search logs for "Amount must match"

## Future Enhancements

- **Metrics Dashboard**: Visualize registration conflicts and payment failures
- **Admin UI**: View audit logs in web interface
- **Option B**: Time-overlap constraint instead of global rule
- **Rate Limiting**: Prevent rapid registration attempts
- **Automated Refunds**: Trigger refunds automatically based on policy

## Troubleshooting

### User reports "Cannot register for event"

1. Check if user has existing active registration:
   ```javascript
   db.registrations.find({
     user_email_snapshot: "user@example.com",
     status: { $nin: ["cancelled_by_user", "cancelled_admin"] }
   })
   ```

2. If found, user must cancel existing registration first

3. If no active registration found, check application logs for errors

### Payment amount validation fails

1. Verify event fee: `db.events.findOne({_id: ...}, {fee_cents: 1})`
2. Verify team size in registration
3. Check payment request amount matches: `fee_cents * team_size`

### Audit logs not created

1. Check database connection
2. Verify indexes created: `db.audit_logs.getIndexes()`
3. Check application logs for PyMongoError

## Support

For issues or questions:
1. Check application logs
2. Review audit logs for the specific registration/payment
3. Contact development team with audit log IDs for investigation
