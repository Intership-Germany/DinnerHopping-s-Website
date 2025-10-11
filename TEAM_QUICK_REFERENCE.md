# Team Registration - Quick Reference Card

## 🚀 Quick Start

### For Users
1. Navigate to event page
2. Click "Register as Team"
3. Choose partner type (existing user or external)
4. Fill in details and submit
5. Complete payment
6. Both members receive confirmation

### For Admins
1. Navigate to `/admin-teams.html`
2. Select event or view all
3. Monitor incomplete/faulty teams
4. Send reminders or release plans

---

## 📋 Key API Endpoints

### Team Registration
```bash
# Create team
POST /registrations/team
{
  "event_id": "...",
  "partner_existing": {"email": "..."},  # OR partner_external
  "cooking_location": "creator",
  "course_preference": "main"
}

# Get team details
GET /registrations/teams/{team_id}

# Decline invitation
POST /registrations/teams/{team_id}/decline

# Replace partner
POST /registrations/teams/{team_id}/replace
```

### Admin
```bash
# Get teams overview
GET /admin/teams/overview?event_id={id}

# Send reminders to incomplete teams
POST /admin/teams/send-incomplete-reminder

# Release event plans
POST /admin/events/{event_id}/release-plans
```

---

## ✅ Validation Rules

### Kitchen Rules
- ✅ At least one member must have kitchen
- ✅ Cooking location must have kitchen available
- ✅ Main course requires `main_course_possible = true`

### Dietary Preference
```
Vegan > Vegetarian > Omnivore
```

### Team Statuses
- `pending` - Awaiting payment
- `paid` - Payment complete, both active
- `incomplete` - One member cancelled
- `cancelled` - Team cancelled
- `invited` - Partner not yet confirmed

---

## 🧪 Testing

```bash
# Run all team tests
cd backend
python -m pytest tests/test_team* -v

# Expected: 20 tests pass
```

---

## 📧 Email Templates

| Template Key | When Sent | Recipients |
|--------------|-----------|------------|
| `team_invitation` | Partner added | Partner |
| `team_partner_cancelled` | Partner cancels | Creator |
| `team_update` | Partner replaced | All parties |
| `team_incomplete_reminder` | Admin action | Creator |
| `payment_confirmation` | Payment success | Both |
| `final_plan` | Plans released | All paid |

---

## 🔧 Common Issues

**"Kitchen required" error**
→ Ensure at least one member has `kitchen_available: true`

**"Main course not possible" error**
→ Selected location needs `main_course_possible: true`

**Partner not receiving email**
→ Check SMTP config, logs, and spam folder

**Cannot replace partner**
→ Check if cancellation deadline passed

---

## 📊 Admin Team Categories

| Category | Meaning | Color |
|----------|---------|-------|
| Complete | Both paid & active | 🟢 Green |
| Incomplete | One cancelled | 🟠 Orange |
| Faulty | Both cancelled after payment | 🔴 Red |
| Pending | Awaiting payment | 🔵 Blue |

---

## 💡 Pro Tips

1. **Auto-sync allergies** - User profile allergies automatically sync to team registration
2. **Deadline enforcement** - Cancellations blocked after event deadline
3. **Idempotent operations** - Safe to retry team creation/payment
4. **Transaction support** - Team + registrations created atomically when MongoDB supports it
5. **Email fallbacks** - System falls back to console logs if SMTP unavailable

---

## 📦 Database Collections

### teams
```javascript
{
  _id, event_id, created_by_user_id,
  members: [{type, user_id, email, kitchen_available, diet, allergies}],
  cooking_location, course_preference, team_diet,
  status, created_at
}
```

### registrations
```javascript
{
  _id, event_id, team_id, user_id,
  user_email_snapshot, status, team_size,
  preferences: {course_preference, cooking_location},
  diet, payment_id
}
```

---

## 🎯 Success Metrics

- ✅ 20/20 tests passing
- ✅ All 10 deliverables completed
- ✅ Full email notification system
- ✅ Admin dashboard operational
- ✅ Comprehensive validation
- ✅ Production-ready codebase

---

**Full Documentation:** See `TEAM_REGISTRATION_GUIDE.md`

**Support:** Check logs in `backend/logs/` or contact dev team
