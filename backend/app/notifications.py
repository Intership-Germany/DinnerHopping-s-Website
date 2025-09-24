"""Notification service module.

Central place to build and send domain specific notification emails.
Each function returns a bool indicating best-effort success (True also when
printed to console in dev fallback mode).

Templates kept intentionally simple (plain text). For richer formatting we
could add Jinja2 later.
"""
from typing import Iterable, Sequence
from .utils import send_email


async def _send(to: Sequence[str] | str, subject: str, lines: Iterable[str], category: str) -> bool:
    body = "\n".join(lines) + "\n"
    return await send_email(to=to, subject=subject, body=body, category=category)


# Account / Auth
async def send_verification_reminder(email: str) -> bool:
    return await _send(email, "Reminder: verify your DinnerHopping account", [
        "Hi!",
        "You still need to verify your email to activate your account.",
        "If you've already verified, you can ignore this message.",
    ], "verification_reminder")


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
        ok_any = await _send(r, f"Registration confirmed for {event_title}", lines, "payment_confirmation") or ok_any
    return ok_any


async def send_cancellation_confirmation(email: str, event_title: str, refund_flag: bool) -> bool:
    lines = [
        f"Your registration for '{event_title}' has been cancelled.",
        "A refund will be processed." if refund_flag else "The event did not have refundable cancellations.",
    ]
    return await _send(email, f"Cancellation confirmed: {event_title}", lines, "cancellation")


async def send_team_partner_cancelled(creator_email: str, event_title: str) -> bool:
    return await _send(creator_email, "Team partner cancelled", [
        f"Your partner cancelled their participation for '{event_title}'.",
        "You can invite a replacement or cancel the team.",
    ], "team_cancellation")


# Replacement flow
async def send_partner_replaced_notice(old_partner_email: str | None, new_partner_email: str | None, creator_email: str, event_title: str) -> bool:
    lines = [
        f"The team composition for '{event_title}' changed.",
    ]
    if new_partner_email:
        lines.append(f"New partner: {new_partner_email}")
    if old_partner_email:
        lines.append(f"Replaced partner: {old_partner_email}")
    return await _send([e for e in [old_partner_email, new_partner_email, creator_email] if e], "Team update", lines, "team_update")


# Future notifications placeholder: plan release, reminders, refund processed, etc.

__all__ = [
    "send_payment_confirmation_emails",
    "send_cancellation_confirmation",
    "send_team_partner_cancelled",
    "send_partner_replaced_notice",
    "send_verification_reminder",
]
