"""Notification service module.

Central place to build and send domain specific notification emails.
Each function returns a bool indicating best-effort success (True also when
printed to console in dev fallback mode).

Templates kept intentionally simple (plain text). For richer formatting we
could add Jinja2 later.
"""
from typing import Iterable, Sequence, Mapping, Any
from .utils import send_email
from . import db as db_mod
import re
import html
import datetime
import os

# Match literal double-curly placeholders like {{ variable }}.  Curly braces
# must be escaped in the regex so they are treated as literal characters.
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_\.]+)\s*\}\}")

async def _render_template(key: str, fallback_subject: str, fallback_lines: Iterable[str], variables: Mapping[str, Any] | None = None) -> tuple[str,str]:
    """Load template by key from DB and render with {{placeholders}}.

    Falls back to provided plaintext subject/body if template not found.
    Supports simple variable substitution; missing variables become empty string.
    
    Automatic variables added to all templates:
    - current_date: Current date in YYYY-MM-DD format
    - current_time: Current time in HH:MM:SS format
    - current_datetime: Current datetime in ISO format
    - current_year: Current year (e.g., 2024)
    """
    import datetime
    
    # Add automatic time/date variables
    now = datetime.datetime.now(datetime.timezone.utc)
    auto_vars = {
        'current_date': now.strftime('%Y-%m-%d'),
        'current_time': now.strftime('%H:%M:%S'),
        'current_datetime': now.isoformat(),
        'current_year': str(now.year),
    }
    
    # Merge user variables with automatic variables (user variables take precedence)
    variables = variables or {}
    merged_vars = {**auto_vars, **variables}
    
    tpl = await db_mod.db.email_templates.find_one({'key': key})
    if not tpl:
        body = "\n".join(fallback_lines) + "\n"
        return fallback_subject, body
    # Render subject and body using the same placeholder substitution logic
    raw_subject = tpl.get('subject') or fallback_subject
    html_body = tpl.get('html_body') or "\n".join(fallback_lines)

    def _sub(match):
        name = match.group(1)
        value = merged_vars
        for part in name.split('.'):
            if isinstance(value, Mapping) and part in value:
                value = value[part]
            else:
                return ''
        return html.escape(str(value))

    # Substitute placeholders in subject and body
    subject = PLACEHOLDER_PATTERN.sub(_sub, raw_subject)
    rendered = PLACEHOLDER_PATTERN.sub(_sub, html_body)
    # Simple heuristic: if template contains HTML tags, send as text by stripping tags? For now keep raw.
    # Current send_email only supports plaintext; we downgrade by stripping basic tags.
    # Basic strip: remove <...> tags
    text_body = re.sub(r'<[^>]+>', '', rendered)
    return subject, text_body + ("\n" if not text_body.endswith('\n') else '')

async def _send(to: Sequence[str] | str, subject: str, lines: Iterable[str], category: str, template_key: str | None = None, variables: Mapping[str, Any] | None = None) -> bool:
    # If a `template_key` is provided, delegate rendering to the central
    # `send_email` helper by passing the template key as the email category and
    # the caller-provided variables. This ensures a single place performs
    # DB lookups and rendering (avoids double-rendering or mismatched keys).
    if template_key:
        # Pass the plaintext fallback lines as the body; send_email will use
        # these as a fallback if the DB template is missing.
        fallback_body = "\n".join(lines)
        return await send_email(to=to, subject=subject, body=fallback_body, category=template_key, template_vars=variables)
    else:
        body = "\n".join(lines) + "\n"
        return await send_email(to=to, subject=subject, body=body, category=category)


# Account / Auth
async def send_verification_reminder(email: str) -> bool:
    return await _send(email, "Reminder: verify your DinnerHopping account", [
        "Hi!",
        "You still need to verify your email to activate your account.",
        "If you've already verified, you can ignore this message.",
    ], "verification_reminder", template_key="verification_reminder", variables={'email': email})


# Registrations / Payments
async def send_payment_confirmation_emails(event_title: str, event_date, recipients: Sequence[str]) -> bool:
    lines = [
        f"Your registration for '{event_title}' is confirmed.",
        f"Event date: {event_date}",
        "You'll receive your detailed schedule closer to the event.",
        "Have fun!",
        "— DinnerHopping Team",
    ]
    ok_any = False
    for r in recipients:
        ok_any = await _send(r, f"Registration confirmed for {event_title}", lines, "payment_confirmation", template_key="payment_confirmation", variables={'event_title': event_title, 'event_date': event_date, 'email': r}) or ok_any
    return ok_any


async def send_team_creator_cancelled(partner_email: str, event_title: str, creator_email: str) -> bool:
    """Notify the partner that the team was cancelled by the creator.

    This is used when the team leader cancels the team; the partner should be
    informed so they don't expect to attend.
    """
    lines = [
        f"Hello,",
        "",
        f"The team for '{event_title}' has been cancelled by the team creator ({creator_email}).",
        "You are no longer registered for this event as part of that team.",
        "If you believe this is an error, please contact the event organiser.",
        "",
        "— DinnerHopping Team",
    ]
    return await _send(
        partner_email,
        f"Team cancelled by creator - {event_title}",
        lines,
        "team_cancellation",
        template_key="team_creator_cancelled",
        variables={'event_title': event_title, 'creator_email': creator_email, 'email': partner_email}
    )


async def send_cancellation_confirmation(email: str, event_title: str, refund_flag: bool) -> bool:
    lines = [
        f"Your registration for '{event_title}' has been cancelled.",
        "A refund will be processed." if refund_flag else "The event did not have refundable cancellations.",
    ]
    return await _send(email, f"Cancellation confirmed: {event_title}", lines, "cancellation", template_key="cancellation_confirmation", variables={'event_title': event_title, 'refund': refund_flag, 'email': email})


async def send_team_partner_cancelled(creator_email: str, event_title: str) -> bool:
    return await _send(creator_email, f"Your team partner cancelled - {event_title}", [
        f"Your partner has declined the team invitation for '{event_title}'.",
        "You'll need to find a new partner or register solo.",
    ], "team_cancellation", template_key="team_partner_cancelled", variables={'event_title': event_title, 'email': creator_email})


async def send_team_partner_accepted(creator_email: str, partner_email: str, event_title: str, team_id: str) -> bool:
    """Notify team creator that partner has accepted the invitation."""
    lines = [
        f"Great news! Your partner {partner_email} has accepted the team invitation for '{event_title}'.",
        "",
        "Your team is now confirmed. Please complete the payment to finalize your registration.",
        "",
        f"Team ID: {team_id}",
    ]
    return await _send(
        creator_email, 
        f"Your partner accepted - {event_title}", 
        lines, 
        "team_partner_accepted", 
        template_key="team_partner_accepted", 
        variables={
            'event_title': event_title, 
            'partner_email': partner_email,
            'team_id': team_id,
            'email': creator_email
        }
    )


async def send_team_invitation(partner_email: str, creator_email: str, event_title: str, event_date: str, decline_url: str, team_id: str) -> bool:
    """Send invitation email to a partner who was added to a team.
    
    The email should include:
    - Event details
    - Creator information
    - Link to decline the invitation
    - Information that they have been automatically registered
    """
    lines = [
        f"Hi!",
        "",
        f"You have been invited to join a DinnerHopping team by {creator_email}.",
        f"Event: {event_title}",
        f"Date: {event_date}",
        "",
        "You have been automatically registered for this event as part of this team.",
        "",
        "If you cannot participate, you can decline your participation using the link below:",
        f"{decline_url}",
        "",
        "If you decline, the team creator will be notified and can find a replacement partner.",
        "",
        "Looking forward to seeing you at the event!",
        "— DinnerHopping Team",
    ]
    return await _send(
        partner_email, 
        f"You've been invited to join a DinnerHopping team - {event_title}", 
        lines, 
        "team_invitation", 
        template_key="team_invitation", 
        variables={
            'event_title': event_title, 
            'event_date': event_date,
            'creator_email': creator_email,
            'partner_email': partner_email,
            'decline_url': decline_url,
            'team_id': team_id
        }
    )


async def send_team_created(creator_email: str, partner_email: str | None, event_title: str, invite_link: str | None, team_id: str) -> bool:
    """Notify creator (and optionally partner) that a team was created.

    - Creator: always notify with team details and invite link for partner.
    - Partner: if partner_email provided, send an informational email (non-critical).
    Returns True if at least one email send succeeded.
    """
    ok_any = False
    # Creator notification
    try:
        lines = [
            f"Your team for '{event_title}' has been created.",
            "Your partner has been invited and will need to accept the invitation.",
        ]
        if invite_link:
            lines.append(f"Invite link: {invite_link}")
        lines.append("")
        lines.append("— DinnerHopping Team")
        ok_any = await _send(
            creator_email,
            f"Team created for {event_title}",
            lines,
            "team_created",
            template_key="team_created",
            variables={'event_title': event_title, 'team_id': team_id, 'invite_link': invite_link, 'email': creator_email}
        ) or ok_any
    except Exception:
        ok_any = ok_any or False
    return ok_any


# Replacement flow
async def send_partner_replaced_notice(old_partner_email: str | None, new_partner_email: str | None, creator_email: str, event_title: str) -> bool:
    lines = [
        f"The team composition for '{event_title}' changed.",
    ]
    if new_partner_email:
        lines.append(f"New partner: {new_partner_email}")
    if old_partner_email:
        lines.append(f"Replaced partner: {old_partner_email}")
    recipients = [e for e in [old_partner_email, new_partner_email, creator_email] if e]
    ok_any = False
    for r in recipients:
        ok_any = await _send(r, "Team update", lines, "team_update", template_key="team_update", variables={'event_title': event_title, 'email': r, 'old_partner_email': old_partner_email, 'new_partner_email': new_partner_email}) or ok_any
    return ok_any

async def send_refund_processed(email: str, event_title: str, amount_cents: int) -> bool:
    amount_eur = f"{amount_cents/100:.2f}"
    lines = [
        f"Your refund for '{event_title}' has been processed.",
        f"Amount: {amount_eur} €",
        "It may take a few days to appear depending on your payment provider.",
    ]
    return await _send(email, f"Refund processed for {event_title}", lines, "refund_processed", template_key="refund_processed", variables={'event_title': event_title, 'amount_eur': amount_eur, 'email': email})


async def send_team_incomplete_reminder(email: str, event_title: str, replace_url: str) -> bool:
    """Send reminder to team creator to find a replacement partner."""
    lines = [
        f"Your team for '{event_title}' is currently incomplete.",
        "Your partner has cancelled, and you need to find a replacement.",
        "",
        f"Please visit: {replace_url}",
        "",
        "If you don't find a replacement, your team may be excluded from matching.",
    ]
    return await _send(email, f"Action needed: Find a replacement partner for {event_title}", lines, "team_incomplete_reminder", template_key="team_incomplete_reminder", variables={'event_title': event_title, 'replace_url': replace_url, 'email': email})


async def send_final_plan_released(email: str, event_title: str, plan_url: str) -> bool:
    """Notify participant that final event schedule is available."""
    lines = [
        f"Great news! The final schedule for '{event_title}' is now available.",
        "",
        f"View your personal event plan here: {plan_url}",
        "",
        "See you soon!",
    ]
    return await _send(email, f"Your DinnerHopping schedule is ready - {event_title}", lines, "final_plan", template_key="final_plan", variables={'event_title': event_title, 'plan_url': plan_url, 'email': email})


async def notify_admin_manual_payment(
    payment_id: str,
    registration_id: str | None,
    user_email: str | None,
    amount_cents: int,
    event_title: str | None = None,
    user_message: str | None = None,
    event_id: str | None = None,
    user_name: str | None = None,
    team_size: int | None = None,
    registration_status: str | None = None,
    currency: str | None = 'EUR',
) -> bool:
    """Notify admins that a manual/contact-us payment needs review.

    - Inserts a lightweight admin_alerts document for dashboard consumption.
    - Sends an email to the admin contact (SMTP_FROM_ADDRESS / fallback) informing them.
    Best-effort: failures do not raise.
    """
    try:
        unit = (currency or 'EUR').upper()
    except Exception:
        unit = 'EUR'
    try:
        amount_value = f"{amount_cents/100:.2f}"
    except Exception:
        amount_value = str(amount_cents)

    amount_display = f"{amount_value} {unit}" if unit not in {'EUR', '€'} else f"{amount_value} €"

    title = f"Manual payment awaiting review: {amount_display}"
    if event_title:
        title = f"Manual payment awaiting review for '{event_title}': {amount_display}"

    lines = [
        f"A manual payment was created and requires admin validation.",
        f"Payment id: {payment_id}",
    ]
    if event_title:
        lines.append(f"Event: {event_title}")
    if event_id:
        lines.append(f"Event id: {event_id}")
    lines.extend([
        f"Registration id: {registration_id}",
        f"User email: {user_email}",
    ])
    if user_name:
        lines.append(f"User name: {user_name}")
    if team_size is not None:
        lines.append(f"Team size: {team_size}")
    if registration_status:
        lines.append(f"Registration status: {registration_status}")
    lines.append(f"Amount: {amount_display}")
    if user_message:
        lines.extend(["", "User message:", user_message])
        # augment title so admins notice message presence
        title = f"{title} — message attached"
    lines.append("Please review and validate this payment from the admin dashboard.")

    # Insert an admin alert document for dashboard consumption (best-effort)
    try:
        await db_mod.db.admin_alerts.insert_one({
            'type': 'manual_payment',
            'payment_id': payment_id,
            'registration_id': registration_id,
            'user_email': user_email,
            'amount_cents': amount_cents,
            'event_title': event_title,
            'user_message': user_message,
            'event_id': event_id,
            'user_name': user_name,
            'team_size': team_size,
            'registration_status': registration_status,
            'currency': unit,
            'status': 'open',
            'created_at': datetime.datetime.now(datetime.timezone.utc),
        })
    except Exception:
        # ignore DB insertion failures
        pass

    # Send email to configured admin contact (fallback to from address)
    admin_contact = None
    try:
        sdoc = await db_mod.db.settings.find_one({'key': 'admin_contact'})
        if sdoc and isinstance(sdoc, dict):
            admin_contact = sdoc.get('value')
    except Exception:
        admin_contact = None

    from_addr = os.getenv('SMTP_FROM_ADDRESS') or os.getenv('FROM_ADDRESS')

    recipients = [r for r in [admin_contact or from_addr] if r]
    if not recipients:
        # nothing to send, but return True since DB alert may be enough
        return True

    try:
        return await _send(recipients, title, lines, 'admin_notification', template_key=None, variables=None)
    except Exception:
        return False

__all__ = [
    "send_payment_confirmation_emails",
    "send_cancellation_confirmation",
    "send_team_partner_cancelled",
    "send_team_partner_accepted",
    "send_team_invitation",
    "send_partner_replaced_notice",
    "send_verification_reminder",
    "send_refund_processed",
    "send_team_incomplete_reminder",
    "send_final_plan_released",
]
