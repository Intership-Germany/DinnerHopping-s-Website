from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Set

from bson.objectid import ObjectId

from ... import datetime_utils
from ... import db as db_mod
from ...notifications import send_refund_processed
from ...utils import ensure_chats_from_matches, ensure_general_chat_full, send_email

from .config import meal_time_defaults
from .data import augment_emails_map_with_splits, get_event, team_emails_map, team_key


async def persist_match_proposal(event_id: str, proposal: dict) -> dict:
    latest = await db_mod.db.matches.find_one({'event_id': event_id}, sort=[('version', -1)])
    version = 1 + int(latest.get('version') or 0) if latest else 1
    doc = {
        'event_id': event_id,
        'groups': proposal.get('groups') or [],
        'metrics': proposal.get('metrics') or {},
        'status': 'proposed',
        'version': version,
        'algorithm': proposal.get('algorithm') or 'unknown',
        'created_at': datetime.datetime.now(datetime.timezone.utc),
    }
    res = await db_mod.db.matches.insert_one(doc)
    doc['id'] = str(res.inserted_id)
    return doc


async def mark_finalized(event_id: str, version: int, finalized_by: Optional[str]) -> dict:
    record = await db_mod.db.matches.find_one({'event_id': event_id, 'version': int(version)})
    if not record:
        raise ValueError('match version not found')
    now = datetime.datetime.now(datetime.timezone.utc)
    await db_mod.db.matches.update_one({'_id': record['_id']}, {'$set': {'status': 'finalized', 'finalized_by': finalized_by, 'finalized_at': now}})
    await db_mod.db.events.update_one({'_id': ObjectId(event_id)}, {'$set': {'matching_status': 'finalized', 'updated_at': now}})
    return await db_mod.db.matches.find_one({'_id': record['_id']})


async def list_issues(event_id: str, version: Optional[int] = None) -> dict:
    query: Dict[str, Any] = {'event_id': event_id}
    if version is not None:
        query['version'] = int(version)
    match_doc = await db_mod.db.matches.find_one(query, sort=[('version', -1)])
    if not match_doc:
        return {'groups': [], 'issues': []}
    groups = match_doc.get('groups') or []
    issues: List[dict] = []
    team_cancelled: Set[str] = set()
    team_incomplete: Set[str] = set()
    reg_by_team: Dict[str, List[dict]] = {}
    async for registration in db_mod.db.registrations.find({'event_id': ObjectId(event_id)}):
        team_id = team_key(registration)
        reg_by_team.setdefault(team_id, []).append(registration)
    async for team in db_mod.db.teams.find({'event_id': ObjectId(event_id)}):
        if (team.get('status') or '').lower() == 'cancelled':
            team_cancelled.add(str(team['_id']))
    for team_id, regs in reg_by_team.items():
        cancelled = [reg for reg in regs if reg.get('status') in ('cancelled_by_user', 'cancelled_admin', 'refunded')]
        if len(regs) >= 2:
            if len(cancelled) == len(regs):
                team_cancelled.add(team_id)
            elif len(cancelled) == 1:
                team_incomplete.add(team_id)
        else:
            if cancelled:
                team_cancelled.add(team_id)
    event = await get_event(event_id)
    include_payment_checks = _should_check_payments(event)
    team_payment_missing: Set[str] = set()
    team_payment_partial: Set[str] = set()
    if include_payment_checks:
        payments_by_reg = await _payments_by_registration(reg_by_team)
        cancelled_statuses = {'cancelled_by_user', 'cancelled_admin', 'refunded', 'expired'}
        for team_id, regs in reg_by_team.items():
            active = [reg for reg in regs if reg.get('status') not in cancelled_statuses]
            if not active:
                continue
            paid_count = sum(
                1 for reg in active
                if payments_by_reg.get(str(reg.get('_id')), {}).get('status') in ('paid', 'succeeded')
            )
            if paid_count == 0:
                team_payment_missing.add(team_id)
            elif paid_count < len(active):
                team_payment_partial.add(team_id)
    for group in groups:
        group_issues: List[str] = []
        for team_id in [group.get('host_team_id'), *(group.get('guest_team_ids') or [])]:
            if team_id in team_cancelled:
                group_issues.append('faulty_team_cancelled')
            if team_id in team_incomplete:
                group_issues.append('team_incomplete')
            if team_id in team_payment_missing:
                group_issues.append('payment_missing')
            if team_id in team_payment_partial:
                group_issues.append('payment_partial')
        if group_issues:
            issues.append({'group': group, 'issues': sorted(set(group_issues))})
    return {'groups': groups, 'issues': issues}


async def refunds_overview(event_id: str) -> dict:
    event = await get_event(event_id)
    if not event:
        return {'enabled': False, 'total_refund_cents': 0, 'items': []}
    enabled = bool(event.get('refund_on_cancellation'))
    if not enabled:
        return {'enabled': False, 'total_refund_cents': 0, 'items': []}
    fee = int(event.get('fee_cents') or 0)
    items: List[dict] = []
    total = 0
    async for registration in db_mod.db.registrations.find({'event_id': event['_id'], 'status': {'$in': ['cancelled_by_user', 'cancelled_admin']}}):
        amount = fee * int(registration.get('team_size') or 1)
        payment = await db_mod.db.payments.find_one({'registration_id': registration.get('_id')})
        refunded = amount if payment and (payment.get('status') == 'refunded' or payment.get('refunded') is True) else 0
        due = max(0, amount - refunded)
        if due > 0:
            items.append({'registration_id': str(registration.get('_id')), 'user_email': registration.get('user_email_snapshot'), 'amount_cents': due})
            total += due
    return {'enabled': True, 'total_refund_cents': total, 'items': items}


async def generate_plans_from_matches(event_id: str, version: int) -> int:
    match_doc = await db_mod.db.matches.find_one({'event_id': event_id, 'version': int(version)})
    if not match_doc:
        return 0
    base_map = await team_emails_map(event_id)
    groups = match_doc.get('groups') or []
    team_to_emails = augment_emails_map_with_splits(base_map, groups)
    meal_times = meal_time_defaults()
    sections_by_email: Dict[str, List[dict]] = {}
    for group in groups:
        meal = group.get('phase')
        host = group.get('host_team_id')
        guests = group.get('guest_team_ids') or []
        host_emails = team_to_emails.get(str(host), [])
        guest_emails: List[str] = []
        for team_id in guests:
            guest_emails.extend(team_to_emails.get(str(team_id), []))
        host_email = host_emails[0] if host_emails else None
        section = {
            'meal': meal,
            'time': meal_times.get(meal, '20:00'),
            'host': {'email': host_email, 'emails': host_emails},
            'guests': guest_emails,
        }
        for email in set((host_emails or []) + guest_emails):
            sections_by_email.setdefault(email, []).append(section)
    written = 0
    for email, sections in sections_by_email.items():
        await db_mod.db.plans.delete_many({'event_id': ObjectId(event_id), 'user_email': email})
        doc = {
            'event_id': ObjectId(event_id),
            'user_email': email,
            'sections': sections,
            'created_at': datetime.datetime.now(datetime.timezone.utc),
        }
        await db_mod.db.plans.insert_one(doc)
        written += 1
    return written


async def finalize_and_generate_plans(event_id: str, version: int, finalized_by: Optional[str]) -> dict:
    record = await mark_finalized(event_id, version, finalized_by)
    count = await generate_plans_from_matches(event_id, version)
    try:
        await ensure_chats_from_matches(event_id, version)
        await ensure_general_chat_full(event_id)
    except Exception:
        pass
    sent = 0
    event = await get_event(event_id)
    event_title = event.get('title') if event else None
    async for plan in db_mod.db.plans.find({'event_id': ObjectId(event_id)}):
        email = plan.get('user_email')
        if not email:
            continue
        subject = 'Your DinnerHopping schedule is ready'
        body_lines = [
            'Hello,',
            'Your schedule for the event is ready. Log in to see details.',
            'Have a great time!',
            '— DinnerHopping Team',
        ]
        try:
            ok = await send_email(
                to=email,
                subject=subject,
                body='\n'.join(body_lines),
                category='final_plan',
                template_vars={'event_title': event_title, 'email': email},
            )
            sent += 1 if ok else 0
        except Exception:
            pass
    return {'finalized_version': record.get('version'), 'plans_written': count, 'emails_attempted': sent}


async def process_refunds(event_id: str, registration_ids: Optional[List[str]] = None) -> dict:
    event = await get_event(event_id)
    if not event or not event.get('refund_on_cancellation'):
        return {'processed': 0, 'items': [], 'reason': 'refunds_disabled_or_event_missing'}
    fee = int(event.get('fee_cents') or 0)
    if fee <= 0:
        return {'processed': 0, 'items': [], 'reason': 'no_fee_configured'}
    query: Dict[str, Any] = {'event_id': event['_id'], 'status': {'$in': ['cancelled_by_user', 'cancelled_admin']}}
    if registration_ids:
        if 'refunded' not in query['status']['$in']:
            query['status']['$in'].append('refunded')
        object_ids = []
        for registration_id in registration_ids:
            try:
                object_ids.append(ObjectId(registration_id))
            except Exception:
                continue
        if not object_ids:
            return {'processed': 0, 'items': [], 'reason': 'invalid_registration_ids'}
        query['_id'] = {'$in': object_ids}
    candidates: List[dict] = []
    async for registration in db_mod.db.registrations.find(query):
        candidates.append(registration)
    if not candidates:
        return {'processed': 0, 'items': []}
    processed_items: List[dict] = []
    event_title = event.get('title') or 'DinnerHopping Event'
    for registration in candidates:
        registration_id = registration.get('_id')
        team_size = int(registration.get('team_size') or 1)
        amount_cents = fee * team_size
        payment = await db_mod.db.payments.find_one({'registration_id': registration_id})
        payment_status = (payment or {}).get('status')
        already_refunded = payment_status == 'refunded' or (payment and payment.get('refunded') is True)
        if already_refunded:
            if registration.get('status') != 'refunded':
                try:
                    await db_mod.db.registrations.update_one({'_id': registration_id}, {'$set': {'status': 'refunded', 'updated_at': datetime.datetime.now(datetime.timezone.utc)}})
                except Exception:
                    pass
            processed_items.append({'registration_id': str(registration_id), 'status': 'already_refunded', 'amount_cents': 0})
            continue
        if not payment:
            processed_items.append({'registration_id': str(registration_id), 'status': 'no_payment_record', 'amount_cents': 0})
            continue
        try:
            await db_mod.db.payments.update_one(
                {'_id': payment.get('_id')},
                {'$set': {'status': 'refunded', 'refunded': True, 'refund_amount_cents': amount_cents, 'refund_at': datetime.datetime.now(datetime.timezone.utc)}},
            )
        except Exception:
            processed_items.append({'registration_id': str(registration_id), 'status': 'payment_update_failed', 'amount_cents': 0})
            continue
        try:
            await db_mod.db.registrations.update_one({'_id': registration_id}, {'$set': {'status': 'refunded', 'updated_at': datetime.datetime.now(datetime.timezone.utc)}})
        except Exception:
            pass
        try:
            email = registration.get('user_email_snapshot')
            if email:
                await send_refund_processed(email, event_title, amount_cents)
        except Exception:
            pass
        processed_items.append({'registration_id': str(registration_id), 'status': 'refunded', 'amount_cents': amount_cents})
    return {'processed': sum(1 for item in processed_items if item['status'] == 'refunded'), 'items': processed_items}


def _should_check_payments(event: Optional[dict]) -> bool:
    if not event or int(event.get('fee_cents') or 0) <= 0:
        return False
    deadline = event.get('payment_deadline') or event.get('registration_deadline')
    if isinstance(deadline, str):
        try:
            deadline_dt = datetime_utils.parse_iso(deadline)
        except Exception:
            deadline_dt = None
    else:
        deadline_dt = deadline
    if deadline is None:
        return (event.get('status') or '').lower() not in ('draft', 'coming_soon')
    try:
        if deadline_dt and deadline_dt.tzinfo is None:
            deadline_dt = deadline_dt.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        pass
    now = datetime.datetime.now(datetime.timezone.utc)
    return bool(deadline_dt and now >= deadline_dt)


async def _payments_by_registration(reg_by_team: Dict[str, List[dict]]) -> Dict[str, dict]:
    all_reg_ids: List[ObjectId] = []
    for regs in reg_by_team.values():
        for registration in regs:
            reg_id = registration.get('_id')
            if reg_id is not None:
                all_reg_ids.append(reg_id)
    payments_by_reg: Dict[str, dict] = {}
    if all_reg_ids:
        async for payment in db_mod.db.payments.find({'registration_id': {'$in': all_reg_ids}}):
            reg_id = payment.get('registration_id')
            if reg_id is not None:
                payments_by_reg[str(reg_id)] = payment
    return payments_by_reg
