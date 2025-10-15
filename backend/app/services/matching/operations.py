from __future__ import annotations

from datetime import datetime, timezone, timedelta
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
        'created_at': datetime.now(timezone.utc),
    }
    res = await db_mod.db.matches.insert_one(doc)
    doc['id'] = str(res.inserted_id)
    return doc


async def mark_finalized(event_id: str, version: int, finalized_by: Optional[str]) -> dict:
    record = await db_mod.db.matches.find_one({'event_id': event_id, 'version': int(version)})
    if not record:
        raise ValueError('match version not found')
    now = datetime.now(timezone.utc)
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

    def _pair_key(a: Optional[str], b: Optional[str]) -> Optional[tuple[str, str]]:
        if not a or not b:
            return None
        sa, sb = str(a), str(b)
        return (sa, sb) if sa <= sb else (sb, sa)

    pair_counts: Dict[tuple[str, str], int] = {}
    for group in groups:
        host_id = group.get('host_team_id')
        guest_ids = [gid for gid in (group.get('guest_team_ids') or []) if gid is not None]
        for guest_id in guest_ids:
            key = _pair_key(host_id, guest_id)
            if key:
                pair_counts[key] = pair_counts.get(key, 0) + 1
        for idx in range(len(guest_ids)):
            for jdx in range(idx + 1, len(guest_ids)):
                key = _pair_key(guest_ids[idx], guest_ids[jdx])
                if key:
                    pair_counts[key] = pair_counts.get(key, 0) + 1

    for group in groups:
        group_issue_types: Set[str] = set()
        issue_counts: Dict[str, int] = {}
        actors: Dict[str, List[dict]] = {}

        def register_issue(issue_type: str, team_id: Optional[str] = None, role: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> None:
            group_issue_types.add(issue_type)
            issue_counts[issue_type] = issue_counts.get(issue_type, 0) + 1
            if team_id is not None or extra:
                payload: Dict[str, Any] = {'team_id': str(team_id) if team_id is not None else None}
                if role:
                    payload['role'] = role
                if extra:
                    payload.update(extra)
                actors.setdefault(issue_type, []).append(payload)

        host_id = group.get('host_team_id')
        guest_ids = group.get('guest_team_ids') or []
        for team_id in [host_id, *guest_ids]:
            tid = str(team_id) if team_id is not None else None
            role = 'host' if team_id == host_id else 'guest'
            if tid in team_cancelled:
                register_issue('faulty_team_cancelled', tid, role)
            if tid in team_incomplete:
                register_issue('team_incomplete', tid, role)
            if tid in team_payment_missing:
                register_issue('payment_missing', tid, role)
            if tid in team_payment_partial:
                register_issue('payment_partial', tid, role)
            if isinstance(team_id, str) and team_id.startswith('split:'):
                register_issue('team_split_detected', tid, role)

        if group.get('uncovered_allergies'):
            register_issue('uncovered_allergy', str(host_id) if host_id is not None else None, 'host', {'allergies': list(group.get('uncovered_allergies') or [])})

        for warn in group.get('warnings') or []:
            warning = str(warn)
            if warning in {'host_cannot_main', 'host_no_kitchen'}:
                register_issue('capacity_mismatch', str(host_id) if host_id is not None else None, 'host', {'warning': warning})
            elif warning == 'allergy_uncovered':
                if not group.get('uncovered_allergies'):
                    register_issue('uncovered_allergy', str(host_id) if host_id is not None else None, 'host')
                continue
            elif warning == 'diet_conflict':
                register_issue('diet_conflict')
            elif warning == 'host_reuse':
                register_issue('host_reuse', str(host_id) if host_id is not None else None, 'host')

        # Duplicate encounters (host-guest and guest-guest) occurring across groups/phases
        candidate_pairs: List[tuple[str, str]] = []
        if host_id is not None:
            for guest_id in guest_ids:
                key = _pair_key(host_id, guest_id)
                if key and pair_counts.get(key, 0) > 1:
                    candidate_pairs.append(key)
        for idx in range(len(guest_ids)):
            for jdx in range(idx + 1, len(guest_ids)):
                key = _pair_key(guest_ids[idx], guest_ids[jdx])
                if key and pair_counts.get(key, 0) > 1:
                    candidate_pairs.append(key)
        for key in candidate_pairs:
            register_issue('duplicate_pair', None, None, {'pair': list(key), 'total': pair_counts.get(key, 0)})

        if group_issue_types:
            issues.append({
                'group': group,
                'issues': sorted(group_issue_types),
                'issue_counts': issue_counts,
                'actors': actors,
            })
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


async def _team_emails_map(event_id: str) -> Dict[str, List[str]]:
    """Return mapping team_id(str)->list of member emails; include pseudo-team for solo regs.
    Also supports split units: keys of the form 'split:<email>' map to [email].
    """
    out: Dict[str, List[str]] = {}
    # real teams
    async for t in db_mod.db.teams.find({'event_id': ObjectId(event_id)}):
        emails = [m.get('email') for m in (t.get('members') or []) if m.get('email')]
        out[str(t['_id'])] = emails
    # solo registrations
    async for r in db_mod.db.registrations.find({'event_id': ObjectId(event_id)}):
        if not r.get('team_id'):
            tid = f"solo:{str(r.get('_id'))}"
            em = r.get('user_email_snapshot')
            if em:
                out.setdefault(tid, []).append(em)
    return out


def _augment_emails_map_with_splits(base: Dict[str, List[str]], groups: List[dict]) -> Dict[str, List[str]]:
    """Extend mapping with any split:<email> and pair:<a+b> ids seen in groups."""
    out = dict(base)
    for g in groups:
        ids = [g.get('host_team_id'), *(g.get('guest_team_ids') or [])]
        for tid in ids:
            if not isinstance(tid, str):
                continue
            if tid.startswith('split:'):
                email = tid.split(':', 1)[1]
                out.setdefault(tid, []).append(email)
            elif tid.startswith('pair:'):
                part = tid.split(':', 1)[1]
                ems = part.split('+') if '+' in part else []
                if ems:
                    out.setdefault(tid, []).extend([e for e in ems if e])
    return out


async def generate_plans_from_matches(event_id: str, version: int) -> int:
    """Generate per-user plans documents from a proposed/finalized match version.

    Overwrites existing plans for (event_id, user_email). Returns number of plans written.
    """
    m = await db_mod.db.matches.find_one({'event_id': event_id, 'version': int(version)})
    if not m:
        return 0
    base_map = await _team_emails_map(event_id)
    groups = m.get('groups') or []
    team_to_emails = _augment_emails_map_with_splits(base_map, groups)

    # Preload user profiles for first names, allergies, dietary
    all_emails = set()
    for g in groups:
        host = g.get('host_team_id')
        guests = g.get('guest_team_ids') or []
        host_emails = team_to_emails.get(str(host), [])
        guest_emails: List[str] = []
        for tid in guests:
            guest_emails.extend(team_to_emails.get(str(tid), []))
        for em in host_emails + guest_emails:
            all_emails.add(em)
    profiles = {}
    async for u in db_mod.db.users.find({"email": {"$in": list(all_emails)}}):
        profiles[u['email']] = {
            "first_name": u.get('first_name', ''),
            "allergies": u.get('allergies', []),
            "dietary": u.get('default_dietary_preferences', []),
            "lat": u.get('lat'),
            "lon": u.get('lon'),
            "address_full": u.get('address_struct', {}).get('address_full'),
        }

    # Get event config for chat and address unlock
    event = await db_mod.db.events.find_one({"_id": ObjectId(event_id)}) if event_id else None
    chat_enabled = event.get('chat_enabled', False) if event else False
    unlock_minutes = event.get('address_unlock_minutes', 30) if event else 30

    now = datetime.now(timezone.utc)

    def _meal_time(meal: str) -> str:
        return '20:00' if meal=='main' else ('18:00' if meal=='appetizer' else '22:00')

    sections_by_email: Dict[str, List[dict]] = {}
    for g in groups:
        meal = g.get('phase')
        host = g.get('host_team_id')
        guests = g.get('guest_team_ids') or []
        host_emails = team_to_emails.get(str(host), [])
        guest_emails: List[str] = []
        for tid in guests:
            guest_emails.extend(team_to_emails.get(str(tid), []))
        host_email = host_emails[0] if host_emails else None

        # Compose section
        sec = {
            'meal': meal,
            'time': _meal_time(meal),
            'host_email': host_email,
            'host_location': None,
            'guests': [profiles.get(g, {}).get('first_name', '') for g in guest_emails],
            'chat_room_id': None
        }
        # Address unlock logic
        unlock_dt = None
        try:
            unlock_dt = datetime.fromisoformat(sec['time'])
        except Exception:
            pass
        lat = profiles.get(host_email, {}).get('lat')
        lon = profiles.get(host_email, {}).get('lon')
        if lat is not None and lon is not None:
            if unlock_dt and (now >= unlock_dt - timedelta(minutes=unlock_minutes)):
                sec['host_location'] = profiles.get(host_email, {}).get('address_full', None)
            else:
                from ...utils import anonymize_address
                sec['host_location'] = anonymize_address(lat, lon)

        # Add chat room info if enabled
        if chat_enabled:
            from ...utils import create_chat_group
            chat_id = None
            try:
                await create_chat_group(str(event_id), [host_email] + guest_emails, 'system', section_ref=meal)
                chat_group = await db_mod.db.chat_groups.find_one({
                    'event_id': str(event_id),
                    'section_ref': meal,
                    'participant_emails': { '$all': [e for e in [host_email] + guest_emails if e] },
                })
                if chat_group:
                    chat_id = str(chat_group.get('_id'))
            except Exception:
                chat_id = None
            sec['chat_room_id'] = chat_id

        # If current user is host, show allergies/dietary for guests
        if host_email:
            sec['guests_info'] = [
                {
                    'first_name': profiles.get(g, {}).get('first_name', ''),
                    'allergies': profiles.get(g, {}).get('allergies', []),
                    'dietary': profiles.get(g, {}).get('dietary', [])
                } for g in guest_emails
            ]

        for em in set((host_emails or []) + guest_emails):
            sections_by_email.setdefault(em, []).append(sec)

    # write plans
    written = 0
    for em, secs in sections_by_email.items():
        await db_mod.db.plans.delete_many({'event_id': ObjectId(event_id), 'user_email': em})
        doc = {
            'event_id': ObjectId(event_id),
            'user_email': em,
            'sections': secs,
                'created_at': datetime.now(timezone.utc),
        }
        await db_mod.db.plans.insert_one(doc)
        written += 1
    return written


async def finalize_and_generate_plans(event_id: str, version: int, finalized_by: Optional[str]) -> dict:
    rec = await mark_finalized(event_id, version, finalized_by)
    count = await generate_plans_from_matches(event_id, version)
    # create chats: per-dinner groups and ensure general chat has all participants
    try:
        from ...utils import ensure_chats_from_matches, ensure_general_chat_full
        _ = await ensure_chats_from_matches(event_id, version)
        await ensure_general_chat_full(event_id)
    except Exception:
        pass
    # notify participants (best-effort)
    sent = 0
    async for p in db_mod.db.plans.find({'event_id': ObjectId(event_id)}):
        em = p.get('user_email')
        if not em:
            continue
        title = 'Your DinnerHopping schedule is ready'
        lines = [
            'Hello,',
            'Your schedule for the event is ready. Log in to see details.',
            'Have a great time!',
            '— DinnerHopping Team',
        ]
        try:
            # include event title if available so template can use it
            ev = await db_mod.db.events.find_one({'_id': ObjectId(event_id)})
            ev_title = ev.get('title') if ev else None
            ok = await send_email(to=em, subject=title, body='\n'.join(lines), category='final_plan', template_vars={'event_title': ev_title, 'email': em})
            sent += 1 if ok else 0
        except Exception:
            pass
    return {'finalized_version': rec.get('version'), 'plans_written': count, 'emails_attempted': sent}


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
                    await db_mod.db.registrations.update_one({'_id': registration_id}, {'$set': {'status': 'refunded', 'updated_at': datetime.now(timezone.utc)}})
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
                {'$set': {'status': 'refunded', 'refunded': True, 'refund_amount_cents': amount_cents, 'refund_at': datetime.now(timezone.utc)}},
            )
        except Exception:
            processed_items.append({'registration_id': str(registration_id), 'status': 'payment_update_failed', 'amount_cents': 0})
            continue
        try:
            await db_mod.db.registrations.update_one({'_id': registration_id}, {'$set': {'status': 'refunded', 'updated_at': datetime.now(timezone.utc)}})
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
            deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    now = datetime.now(timezone.utc)
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
