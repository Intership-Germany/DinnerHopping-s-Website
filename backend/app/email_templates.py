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
        'html_body': (
            '<div style="font-family: -apple-system, system-ui, Roboto, \"Helvetica Neue\", Arial, '
            'sans-serif; color: #111; line-height:1.6;">\n'
            '<p style="font-size:16px; margin:0 0 12px 0;">Hi!</p>\n'
            '<p style="margin:0 0 16px 0;">Please verify your email by clicking the button below:</p>\n'
            '<p style="margin:0 0 20px 0;">\n'
            '<a href="{{verification_url}}" '
            'style="display:inline-block; background-color:#2563eb; color:#fff; text-decoration:none; '
            'padding:10px 16px; border-radius:6px; font-weight:600;">Verify my email</a>\n'
            '</p>\n'
            '<p style="font-size:13px; color:#6b7280; margin:0 0 8px 0;">\n'
            'If the button above does not work, copy and paste the following link into your browser:\n'
            '</p>\n'
            '<p style="font-size:13px; word-break:break-all;">\n'
            '<a href="{{verification_url}}" style="color:#2563eb; text-decoration:underline;">{{verification_url}}</a>\n'
            '</p>\n'
            '<p style="margin-top:18px; color:#374151;">If you didn\'t request this, ignore this message.</p>\n'
            '<p style="margin-top:18px; color:#6b7280;">— DinnerHopping Team</p>\n'
            '</div>'
        ),
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
        'html_body': '<p>Hi!</p><p>You have been invited to join an event on DinnerHopping. To accept, click: <a href="{{invitation_link}}">Accept invitation</a></p><p>If you don\'t have an account, register with this email. If an account was created for you, set your password here: <a href="{{set_password_url}}">Set password</a></p><p>— DinnerHopping Team</p>',
        'description': 'Invitation email with accept link',
        'variables': ['invitation_link','email','set_password_url']
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
        'html_body': (
            '<div style="font-family: -apple-system, system-ui, Roboto, \"Helvetica Neue\", Arial, '
            'sans-serif; color:#111; line-height:1.6;">\n'
            '<p style="font-size:16px; margin:0 0 12px 0;">Hi!</p>\n'
            '<p style="margin:0 0 16px 0;">Reset your password by clicking the button below:</p>\n'
            '<p style="margin:0 0 20px 0;">\n'
            '<a href="{{reset_url}}" '
            'style="display:inline-block; background-color:#ef4444; color:#fff; text-decoration:none; '
            'padding:10px 16px; border-radius:6px; font-weight:600;">Reset password</a>\n'
            '</p>\n'
            '<p style="font-size:13px; color:#6b7280; margin:0 0 8px 0;">\n'
            'If the button above does not work, copy and paste the following link into your browser:\n'
            '</p>\n'
            '<p style="font-size:13px; word-break:break-all;">\n'
            '<a href="{{reset_url}}" style="color:#2563eb; text-decoration:underline;">{{reset_url}}</a>\n'
            '</p>\n'
            '<p style="margin-top:18px; color:#374151;">If you didn\'t request this, ignore this message.</p>\n'
            '<p style="margin-top:18px; color:#6b7280;">— DinnerHopping Team</p>\n'
            '</div>'
        ),
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
        'subject': "You've been invited to an event on DinnerHopping",
        'html_body': (
            '<div style="font-family: -apple-system, system-ui, Roboto, \"Helvetica Neue\", Arial, '
            'sans-serif; color:#111; line-height:1.6;">\n'
            '<p style="font-size:16px; margin:0 0 12px 0;">Hi!</p>\n'
            '<p style="margin:0 0 12px 0;">You have been invited to join an event on DinnerHopping.</p>\n'
            '<p style="margin:0 0 12px 0;">\n'
            '<a href="{{invitation_link}}" '
            'style="display:inline-block; background-color:#10b981; color:#fff; text-decoration:none; '
            'padding:10px 16px; border-radius:6px; font-weight:600;">Accept invitation</a>\n'
            '</p>\n'
            '<p style="margin:0 0 8px 0; font-size:13px; color:#6b7280;">\n'
            'If the button above does not work, copy and paste the following link into your browser:\n'
            '</p>\n'
            '<p style="font-size:13px; word-break:break-all;">\n'
            '<a href="{{invitation_link}}" style="color:#2563eb; text-decoration:underline;">{{invitation_link}}</a>\n'
            '</p>\n'
            '<p style="margin:12px 0 0 0;">If you don\'t have an account, register with this email. If an account was created for you, set your password here: '
            '<a href="{{set_password_url}}" style="color:#2563eb; text-decoration:underline;">Set password</a></p>\n'
            '<p style="margin-top:18px; color:#6b7280;">— DinnerHopping Team</p>\n'
            '</div>'
        ),
        'description': 'Final plan release notification',
        'variables': ['event_title','email']
    },
    {
        'subject': 'You have been added to a DinnerHopping team',
        'html_body': (
            '<div style="font-family: -apple-system, system-ui, Roboto, \"Helvetica Neue\", Arial, '
            'sans-serif; color:#111; line-height:1.6;">\n'
            '<p style="font-size:16px; margin:0 0 12px 0;">Hi!</p>\n'
            '<p style="margin:0 0 12px 0;">You were added to a team for event "{{event_title}}".</p>\n'
            '<p style="margin:0 0 12px 0;">\n'
            '<a href="{{decline_link}}" '
            'style="display:inline-block; background-color:#f59e0b; color:#fff; text-decoration:none; '
            'padding:10px 16px; border-radius:6px; font-weight:600;">Decline</a>\n'
            '</p>\n'
            '<p style="margin:0 0 8px 0; font-size:13px; color:#6b7280;">\n'
            'If the button above does not work, copy and paste the following link into your browser:\n'
            '</p>\n'
            '<p style="font-size:13px; word-break:break-all;">\n'
            '<a href="{{decline_link}}" style="color:#2563eb; text-decoration:underline;">{{decline_link}}</a>\n'
            '</p>\n'
            '<p style="margin-top:18px; color:#6b7280;">— DinnerHopping Team</p>\n'
            '</div>'
        ),
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
