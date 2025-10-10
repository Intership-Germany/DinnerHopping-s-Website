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
        'key': 'email_verification',
        'subject': 'Please verify your DinnerHopping account',
        'html_body': '<p>Hi!</p><p>Please verify your email by clicking the link below:</p><p><a href="{{verification_url}}">Verify my email</a></p><p>If you didn\'t request this, ignore this message.</p><p>— DinnerHopping Team</p>',
        'description': 'Initial verification email with a link to verify the address.',
        'variables': ['verification_url','email']
    },
    {
        'key': 'invitation',
        'subject': 'You\'ve been invited to an event on DinnerHopping',
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
        'html_body': '<p>Hi!</p><p>Reset your password by clicking: <a href="{{reset_url}}">Reset password</a></p><p>If you didn\'t request this, ignore this message.</p>',
        'description': 'Password reset email',
        'variables': ['reset_url','email']
    },
    {
        'key': 'team_invitation',
        'subject': 'You\'ve been invited to join a DinnerHopping team - {{event_title}}',
        'html_body': '''<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #059669;">You\'ve Been Invited to Join a DinnerHopping Team!</h2>
            <p>Hi!</p>
            <p>You have been invited to join a DinnerHopping team by <strong>{{creator_email}}</strong>.</p>
            
            <div style="background-color: #f3f4f6; padding: 15px; border-radius: 8px; margin: 20px 0;">
                <h3 style="margin-top: 0; color: #374151;">Event Details</h3>
                <p style="margin: 5px 0;"><strong>Event:</strong> {{event_title}}</p>
                <p style="margin: 5px 0;"><strong>Date:</strong> {{event_date}}</p>
            </div>
            
            <p><strong>You have been automatically registered for this event as part of this team.</strong></p>
            
            <p>If you cannot participate, you can decline your participation using the button below:</p>
            
            <div style="text-align: center; margin: 30px 0;">
                <a href="{{decline_url}}" style="display: inline-block; background-color: #dc2626; color: white; padding: 12px 30px; text-decoration: none; border-radius: 6px; font-weight: bold;">Decline Invitation</a>
            </div>
            
            <p style="color: #6b7280; font-size: 14px;">If you decline, the team creator will be notified and can find a replacement partner.</p>
            
            <p style="margin-top: 30px;">Looking forward to seeing you at the event!</p>
            <p>— DinnerHopping Team</p>
        </div>''',
        'description': 'Team invitation email with event details and decline option',
        'variables': ['event_title', 'event_date', 'creator_email', 'partner_email', 'decline_url', 'team_id', 'email']
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
