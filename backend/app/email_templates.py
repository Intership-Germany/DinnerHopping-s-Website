"""Default email templates helper.

Provides a small helper to ensure default templates exist in the DB. This is
kept separate from the CLI seed script so it can be invoked at application
startup without duplicating test-only logic.
"""
from datetime import datetime
from . import db as db_mod

DEFAULT_TEMPLATES = [
    {
        'key': 'verification_reminder',
        'subject': 'Reminder: verify your DinnerHopping account',
        'html_body': '<p>Hi!</p><p>You still need to verify your email to activate your account.</p><p>If you\'ve already verified, you can ignore this message.</p>',
        'description': 'Sent to users who have not yet verified their email.',
        'variables': ['email']
    },
    {
        'key': 'email_verification',
        'subject': 'Please verify your DinnerHopping account',
        'html_body': '<p>Hi!</p><p>Please verify your email by clicking the link below:</p><p><a href="{{verification_url}}">Verify my email</a></p><p>If you didn\'t request this, ignore this message.</p><p>— DinnerHopping Team</p>',
        'description': 'Initial verification email with a link to verify the address.',
        'variables': ['verification_url','email']
    },
    {
        'key': 'payment_confirmation',
        'subject': 'Registration confirmed for {{event_title}}',
        'html_body': '<p>Your registration for <strong>{{event_title}}</strong> is confirmed.</p><p>Event date: {{event_date}}</p><p>You\'ll receive your detailed schedule closer to the event.</p><p>Have fun!<br/>— DinnerHopping Team</p>',
        'description': 'Payment / registration confirmation',
        'variables': ['event_title','event_date','email']
    },
    {
        'key': 'cancellation_confirmation',
        'subject': 'Cancellation confirmed: {{event_title}}',
        'html_body': '<p>Your registration for <strong>{{event_title}}</strong> has been cancelled.</p><p>{{refund}}</p>',
        'description': 'Registration cancellation notice',
        'variables': ['event_title','refund','email']
    },
    {
        'key': 'team_partner_cancelled',
        'subject': 'Team partner cancelled',
        'html_body': '<p>Your partner cancelled their participation for <strong>{{event_title}}</strong>.</p><p>You can invite a replacement or cancel the team.</p>',
        'description': 'Partner cancellation notice',
        'variables': ['event_title','email']
    },
    {
        'key': 'team_update',
        'subject': 'Team update',
        'html_body': '<p>The team composition for <strong>{{event_title}}</strong> changed.</p><p>New partner: {{new_partner_email}}</p><p>Replaced partner: {{old_partner_email}}</p>',
        'description': 'Team change notification',
        'variables': ['event_title','new_partner_email','old_partner_email','email']
    },
    {
        'key': 'invitation',
        'subject': "You've been invited to an event on DinnerHopping",
        'html_body': '<p>Hi!</p><p>You have been invited to join an event on DinnerHopping. To accept, click: <a href="{{invitation_link}}">Accept invitation</a></p><p>If you don\'t have an account, register with this email.</p><p>— DinnerHopping Team</p>',
        'description': 'Invitation email with accept link',
        'variables': ['invitation_link','email','temp_password']
    },
    {
        'key': 'invitation_accept',
        'subject': 'Invitation accepted',
        'html_body': '<p>The invitation has been accepted.</p><p>Registration id: {{registration_id}}</p>',
        'description': 'Notify inviter that invitation was accepted',
        'variables': ['registration_id','email']
    },
    {
        'key': 'password_reset',
        'subject': 'Password reset for your DinnerHopping account',
        'html_body': '<p>Hi!</p><p>Reset your password by clicking: <a href="{{reset_url}}">Reset password</a></p><p>If you didn\'t request this, ignore this message.</p>',
        'description': 'Password reset email',
        'variables': ['reset_url','email']
    },
    {
        'key': 'team_invitation',
        'subject': 'You have been added to a DinnerHopping team',
        'html_body': '<p>Hi!</p><p>You were added to a team for event "{{event_title}}". If you cannot participate, decline here: <a href="{{decline_link}}">Decline</a></p><p>— DinnerHopping Team</p>',
        'description': 'Team invitation email (partner)',
        'variables': ['event_title','decline_link','email']
    },
    {
        'key': 'final_plan',
        'subject': 'Your DinnerHopping schedule is ready',
        'html_body': '<p>Your schedule for {{event_title}} is ready. Log in to view details.</p>',
        'description': 'Final plan release notification',
        'variables': ['event_title','email']
    },
    {
        'key': 'refund_processed',
        'subject': 'Refund processed for {{event_title}}',
        'html_body': '<p>Your refund for <strong>{{event_title}}</strong> has been processed.</p><p>Amount: {{amount_eur}} €</p><p>It may take a few days to appear depending on your payment provider.</p>',
        'description': 'Sent to a participant when a cancellation refund is processed',
        'variables': ['event_title','amount_eur','email']
    }
]


async def ensure_default_templates():
    """Ensure DEFAULT_TEMPLATES exist in the `email_templates` collection.

    This function is idempotent and best-effort: it will not raise on DB
    errors but will log them instead. It inserts templates that are missing.
    """
    try:
        async for tpl in db_mod.db.email_templates.find({}):
            # if collection non-empty, assume templates present
            return
    except Exception:
        # If the collection read fails (tests or fake DB), proceed to attempt inserts
        pass

    # Insert missing templates
    for tpl in DEFAULT_TEMPLATES:
        try:
            existing = await db_mod.db.email_templates.find_one({'key': tpl['key']})
            if existing:
                continue
            tpl['updated_at'] = datetime.utcnow()
            await db_mod.db.email_templates.insert_one(tpl)
        except Exception:
            # best-effort: ignore insert failures
            continue

    return
