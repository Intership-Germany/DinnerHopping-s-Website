"""Seed default email templates if they do not already exist.

Usage (inside backend container or venv):
  python -m scripts.seed_email_templates

Environment:
  MONGODB_URI / DB connection handled by app.db module (uses existing config)
"""
import asyncio
from datetime import datetime
from app import db as db_mod

DEFAULT_TEMPLATES = [
    {
        'key': 'verification_reminder',
        'subject': 'Reminder: verify your DinnerHopping account',
        'html_body': '<p>Hi!</p><p>You still need to verify your email to activate your account.</p><p>If you\'ve already verified, you can ignore this message.</p>',
        'description': 'Sent to users who have not yet verified their email.',
        'variables': ['email']
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
    }
]

async def run():
    for tpl in DEFAULT_TEMPLATES:
        existing = await db_mod.db.email_templates.find_one({'key': tpl['key']})
        if existing:
            print(f"Template {tpl['key']} exists; skipping")
            continue
        tpl['updated_at'] = datetime.utcnow()
        await db_mod.db.email_templates.insert_one(tpl)
        print(f"Inserted template {tpl['key']}")

if __name__ == '__main__':
    asyncio.run(run())
