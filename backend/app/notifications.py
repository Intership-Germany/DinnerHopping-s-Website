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

# Match literal double-curly placeholders like {{ variable }}.  Curly braces
# must be escaped in the regex so they are treated as literal characters.
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_\.]+)\s*\}\}")

async def _render_template(key: str, fallback_subject: str, fallback_lines: Iterable[str], variables: Mapping[str, Any] | None = None, category: str = "generic") -> tuple[str,str]:
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
    subject = tpl.get('subject') or fallback_subject
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


async def send_cancellation_confirmation(email: str, event_title: str, refund_flag: bool) -> bool:
    lines = [
        f"Your registration for '{event_title}' has been cancelled.",
        "A refund will be processed." if refund_flag else "The event did not have refundable cancellations.",
    ]
    return await _send(email, f"Cancellation confirmed: {event_title}", lines, "cancellation", template_key="cancellation_confirmation", variables={'event_title': event_title, 'refund': refund_flag, 'email': email})


async def send_team_partner_cancelled(creator_email: str, event_title: str) -> bool:
    return await _send(creator_email, "Team partner cancelled", [
        f"Your partner cancelled their participation for '{event_title}'.",
        "You can invite a replacement or cancel the team.",
    ], "team_cancellation", template_key="team_partner_cancelled", variables={'event_title': event_title, 'email': creator_email})


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

# Future notifications placeholder: plan release, reminders, refund processed, etc.

__all__ = [
    "send_payment_confirmation_emails",
    "send_cancellation_confirmation",
    "send_team_partner_cancelled",
    "send_partner_replaced_notice",
    "send_verification_reminder",
]
