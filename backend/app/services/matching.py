from __future__ import annotations
import os
import random
from typing import Any, Dict, List, Optional, Tuple, Set
from bson.objectid import ObjectId

from .. import db as db_mod
from .geocoding import geocode_address
from .routing import route_duration_seconds
from ..utils import send_email  # reuse notification helper

# Weights default from env with sensible fallbacks
_DEF = lambda name, d: float(os.getenv(name, d))
W_DUP = _DEF('MATCH_W_DUP', '1000')           # penalty for duplicate pair meeting (reserved)
W_DIST = _DEF('MATCH_W_DIST', '1')            # weight for travel time seconds
W_PREF = _DEF('MATCH_W_PREF', '5')            # reward for course preference satisfied
W_ALL = _DEF('MATCH_W_ALLERGY', '3')          # penalty for allergy/diet conflict
W_HOST = _DEF('MATCH_W_DESIRED_HOST', '10')   # reward if team hosts their desired course


async def _get_event(event_id: str) -> Optional[dict]:
    try:
        oid = ObjectId(event_id)
    except Exception:
        return None
    return await db_mod.db.events.find_one({'_id': oid})


async def _load_registrations(event_oid) -> List[dict]:
    regs = []
    async for r in db_mod.db.registrations.find({'event_id': event_oid, 'status': {'$nin': ['cancelled_by_user','cancelled_admin','refunded','expired']}}):
        regs.append(r)
    return regs

async def _load_teams(event_oid) -> Dict[str, dict]:
    """Return map team_id(str) -> team_doc for the event."""
    teams: Dict[str, dict] = {}
    async for t in db_mod.db.teams.find({'event_id': event_oid}):
        teams[str(t['_id'])] = t
    return teams

async def _user_profile(email: str) -> Optional[dict]:
    return await db_mod.db.users.find_one({'email': email})

async def _team_location(team: dict) -> Tuple[Optional[float], Optional[float]]:
    """Determine representative lat/lon for a team; try explicit member coords then geocode their address_struct.

    Returns (lat, lon) or (None, None) if unresolved.
    """
    members = team.get('members') or []
    coords: List[Tuple[float,float]] = []
    for m in members:
        email = m.get('email')
        if not email:
            continue
        u = await _user_profile(email)
        if not u:
            continue
        lat = u.get('lat'); lon = u.get('lon')
        if isinstance(lat,(int,float)) and isinstance(lon,(int,float)):
            coords.append((float(lat), float(lon)))
            continue
        # try geocode
        st = ((u.get('address_struct') or {}).get('street') or '')
        no = ((u.get('address_struct') or {}).get('street_no') or '')
        pc = ((u.get('address_struct') or {}).get('postal_code') or '')
        city = ((u.get('address_struct') or {}).get('city') or '')
        parts = " ".join([st, no]).strip()
        right = " ".join([pc, city]).strip()
        addr = f"{parts}, {right}".strip(', ')
        if addr:
            latlon = await geocode_address(addr)
            if latlon:
                coords.append(latlon)
    if coords:
        # average
        lat = sum(c[0] for c in coords)/len(coords)
        lon = sum(c[1] for c in coords)/len(coords)
        return (lat, lon)
    return (None, None)


def _team_key(reg: dict) -> str:
    tid = reg.get('team_id')
    if tid:
        return str(tid)
    # solo registration -> own pseudo-team id based on registration id
    return f"solo:{str(reg.get('_id'))}"

async def _build_teams(event_oid) -> List[dict]:
    regs = await _load_registrations(event_oid)
    teams_docs = await _load_teams(event_oid)
    # Load event to access valid_zip_codes if any
    ev = await db_mod.db.events.find_one({'_id': event_oid})
    allowed_zips = set([str(z).strip() for z in (ev.get('valid_zip_codes') or [])]) if ev else set()
    groups: Dict[str, List[dict]] = {}
    for r in regs:
        key = _team_key(r)
        groups.setdefault(key, []).append(r)
    teams: List[dict] = []
    for tid, members_regs in groups.items():
        # try to enrich from teams collection if exists
        team_doc = teams_docs.get(tid) if tid in teams_docs else None
        # If zip restrictions apply, check members' postal codes
        if allowed_zips:
            member_emails = []
            if team_doc and isinstance(team_doc.get('members'), list):
                for m in team_doc['members']:
                    em = m.get('email')
                    if em:
                        member_emails.append(em)
            else:
                for r in members_regs:
                    em = r.get('user_email_snapshot')
                    if em:
                        member_emails.append(em)
            ok = False
            for em in set(member_emails):
                u = await db_mod.db.users.find_one({'email': em})
                if u:
                    pc = ((u.get('address_struct') or {}).get('postal_code'))
                    if pc and str(pc).strip() in allowed_zips:
                        ok = True
                        break
            if not ok:
                continue
        size = max(r.get('team_size') or 1 for r in members_regs)
        pref = None
        diet = None
        for r in members_regs:
            pref = (pref or (r.get('preferences') or {}).get('course_preference'))
            diet = (diet or r.get('diet'))
        t = {
            'team_id': tid,
            'member_regs': members_regs,
            'size': size,
            'course_preference': (pref or None),
            'diet': (diet or 'omnivore'),
            'team_doc': team_doc,
        }
        if team_doc:
            t['team_diet'] = team_doc.get('team_diet') or t['diet']
            t['cooking_location'] = team_doc.get('cooking_location') or 'creator'
        else:
            t['team_diet'] = t['diet']
            t['cooking_location'] = 'creator'
        teams.append(t)
    # sort for stable behavior
    teams.sort(key=lambda x: x['team_id'])
    # attach coordinates
    for t in teams:
        lat, lon = await _team_location(t.get('team_doc') or {'members': [{'email': (t['member_regs'][0].get('user_email_snapshot'))}]})
        t['lat'] = lat; t['lon'] = lon
        # basic capability flags
        # main_course_possible if any member has main_course_possible at cooking location
        can_main = False
        team_doc = t.get('team_doc') or {}
        members = (team_doc.get('members') or [])
        if members:
            if t['cooking_location'] == 'creator':
                can_main = bool(members[0].get('main_course_possible'))
            elif len(members) > 1:
                can_main = bool(members[1].get('main_course_possible'))
        t['can_host_main'] = can_main
    return teams


def _compatible_diet(host_diet: str, guest_diet: str) -> bool:
    # vegan host is compatible with everyone (they cook vegan)
    # omnivore host should not host vegan guest ideally => mark as penalty
    host = (host_diet or 'omnivore').lower()
    guest = (guest_diet or 'omnivore').lower()
    if host == 'omnivore' and guest in ('vegan',):
        return False
    if host == 'vegetarian' and guest == 'vegan':
        return False
    return True


def _score_group_phase(host: dict, guests: List[dict], meal: str, weights: dict) -> Tuple[float, List[str]]:
    score = 0.0
    warnings: List[str] = []
    # Preference: reward if host prefers this course
    if (host.get('course_preference') or '').lower() == meal:
        score += weights.get('pref', W_PREF)
    # Host capability for main
    if meal == 'main' and not host.get('can_host_main'):
        score -= weights.get('cap_penalty', W_PREF)
        warnings.append('host_cannot_main')
    # Diet compatibility
    for g in guests:
        if not _compatible_diet(host.get('team_diet'), g.get('team_diet')):
            score -= weights.get('allergy', W_ALL)
            warnings.append('diet_conflict')
    return score, warnings


async def _travel_time_for_phase(host: dict, guests: List[dict]) -> float:
    # guests travel from their home to host
    coords: List[Tuple[float,float]] = []
    for g in guests:
        if g.get('lat') is None or g.get('lon') is None or host.get('lat') is None:
            continue
        coords.append( (g['lat'], g['lon']) )
        coords.append( (host['lat'], host['lon']) )
    if not coords:
        return 0.0
    # compute pairwise trips guest->host for each guest
    total = 0.0
    for i in range(0, len(coords), 2):
        seg = coords[i:i+2]
        if len(seg) == 2:
            d = await route_duration_seconds(seg)
            total += (d or 0.0)
    return total


def _pair_key(a: str, b: str) -> Tuple[str, str]:
    x, y = sorted([a, b])
    return (x, y)


def _triad_pairs(triad: List[str]) -> List[Tuple[str,str]]:
    a,b,c = triad
    return [ _pair_key(a,b), _pair_key(a,c), _pair_key(b,c) ]


def _shuffle(seed: int, arr: List[Any]) -> List[Any]:
    rnd = random.Random(seed)
    out = arr[:]
    rnd.shuffle(out)
    return out


async def algo_greedy(event_oid, weights: dict, seed: int = 42) -> dict:
    teams = await _build_teams(event_oid)
    # Build three phase groupings without repeating pairs
    phases = ['appetizer','main','dessert']
    used_pairs: Set[Tuple[str,str]] = set()
    groups: List[dict] = []
    # base ordering
    order = _shuffle(seed, teams)
    for phase in phases:
        chunk = [order[i:i+3] for i in range(0, len(order), 3)]
        if len(chunk) and len(chunk[-1]) != 3:
            # if remainder, redistribute greedily by moving last teams to earlier chunks
            while len(chunk) and len(chunk[-1]) != 3:
                leftover = chunk.pop()
                for i, t in enumerate(leftover):
                    chunk[i % len(chunk)].append(t)
        for tri in chunk:
            if len(tri) < 3:
                continue
            tri_ids = [t['team_id'] for t in tri]
            # avoid duplicate pairs; if conflict, rotate triad
            rot_attempts = 0
            while any(pk in used_pairs for pk in _triad_pairs(tri_ids)) and rot_attempts < 3:
                tri = tri[1:] + tri[:1]
                tri_ids = [t['team_id'] for t in tri]
                rot_attempts += 1
            # choose host for phase: prefer team pref == phase; for main require can_host_main
            candidates = tri[:]
            candidates.sort(key=lambda t: (
                -1 if (t.get('course_preference') or '').lower()==phase else 0,
                -1 if (phase!='main' or t.get('can_host_main')) else 1,
            ))
            host = candidates[0]
            if phase == 'main' and not host.get('can_host_main'):
                # pick next who can main
                for c in candidates[1:]:
                    if c.get('can_host_main'):
                        host = c
                        break
            guest_list = [t for t in tri if t['team_id'] != host['team_id']]
            # compute score and travel
            base_score, warns = _score_group_phase(host, guest_list, phase, weights)
            travel = await _travel_time_for_phase(host, guest_list)
            groups.append({
                'phase': phase,
                'host_team_id': host['team_id'],
                'guest_team_ids': [g['team_id'] for g in guest_list],
                'score': base_score - weights.get('dist', W_DIST) * (travel or 0.0),
                'travel_seconds': travel,
                'warnings': warns,
            })
            for pk in _triad_pairs([host['team_id'], *[g['team_id'] for g in guest_list]]):
                used_pairs.add(pk)
        # rotate order for next phase
        order = order[1:] + order[:1]
    metrics = _compute_metrics(groups, weights)
    return { 'algorithm': 'greedy', 'groups': groups, 'metrics': metrics }


async def algo_random(event_oid, weights: dict, seed: int = 99) -> dict:
    teams = await _build_teams(event_oid)
    rnd = random.Random(seed)
    order = teams[:]
    rnd.shuffle(order)
    phases = ['appetizer','main','dessert']
    used_pairs: Set[Tuple[str,str]] = set()
    groups: List[dict] = []
    for phase in phases:
        chunk = [order[i:i+3] for i in range(0, len(order), 3)]
        for tri in chunk:
            if len(tri) < 3:
                continue
            host = rnd.choice(tri)
            if phase == 'main' and not host.get('can_host_main'):
                others = [t for t in tri if t != host and t.get('can_host_main')]
                if others:
                    host = rnd.choice(others)
            guests = [t for t in tri if t['team_id'] != host['team_id']]
            base_score, warns = _score_group_phase(host, guests, phase, weights)
            travel = await _travel_time_for_phase(host, guests)
            groups.append({
                'phase': phase,
                'host_team_id': host['team_id'],
                'guest_team_ids': [g['team_id'] for g in guests],
                'score': base_score - weights.get('dist', W_DIST) * (travel or 0.0),
                'travel_seconds': travel,
                'warnings': warns,
            })
            for pk in _triad_pairs([host['team_id'], *[g['team_id'] for g in guests]]):
                used_pairs.add(pk)
        rnd.shuffle(order)
    metrics = _compute_metrics(groups, weights)
    return { 'algorithm': 'random', 'groups': groups, 'metrics': metrics }


async def algo_local_search(event_oid, weights: dict, seed: int = 7) -> dict:
    # Start from greedy then (placeholder) keep same grouping; real local search can be added iteratively
    base = await algo_greedy(event_oid, weights, seed)
    groups = base['groups'][:]
    metrics = _compute_metrics(groups, weights)
    return { 'algorithm': 'local_search', 'groups': groups, 'metrics': metrics }


def _compute_metrics(groups: List[dict], weights: dict) -> dict:
    total_travel = sum(float(g.get('travel_seconds') or 0.0) for g in groups)
    total_score = sum(float(g.get('score') or 0.0) for g in groups)
    issues = sum(1 for g in groups if g.get('warnings'))
    return {
        'total_travel_seconds': total_travel,
        'aggregate_group_score': total_score,
        'groups_with_warnings': issues,
    }


ALGORITHMS = {
    'greedy': algo_greedy,
    'random': algo_random,
    'local_search': algo_local_search,
}


async def run_algorithms(event_id: str, *, algorithms: List[str], weights: Optional[Dict[str, float]] = None) -> List[dict]:
    ev = await _get_event(event_id)
    if not ev:
        raise ValueError('event not found')
    oid = ev['_id']
    weights = weights or {}
    out: List[dict] = []
    for name in algorithms:
        fn = ALGORITHMS.get(name)
        if not fn:
            continue
        res = await fn(oid, weights)
        res['event_id'] = str(ev['_id'])
        out.append(res)
    return out


async def persist_match_proposal(event_id: str, proposal: dict) -> dict:
    # versioning: latest version +1
    latest = await db_mod.db.matches.find_one({'event_id': event_id}, sort=[('version', -1)])
    version = 1 + int(latest.get('version') or 0) if latest else 1
    doc = {
        'event_id': event_id,
        'groups': proposal.get('groups') or [],
        'metrics': proposal.get('metrics') or {},
        'status': 'proposed',
        'version': version,
        'algorithm': proposal.get('algorithm') or 'unknown',
        'created_at': __import__('datetime').datetime.utcnow(),
    }
    res = await db_mod.db.matches.insert_one(doc)
    doc['id'] = str(res.inserted_id)
    return doc


async def mark_finalized(event_id: str, version: int, finalized_by: Optional[str]) -> dict:
    rec = await db_mod.db.matches.find_one({'event_id': event_id, 'version': int(version)})
    if not rec:
        raise ValueError('match version not found')
    now = __import__('datetime').datetime.utcnow()
    await db_mod.db.matches.update_one({'_id': rec['_id']}, {'$set': {'status': 'finalized', 'finalized_by': finalized_by, 'finalized_at': now}})
    await db_mod.db.events.update_one({'_id': ObjectId(event_id)}, {'$set': {'matching_status': 'finalized', 'updated_at': now}})
    rec = await db_mod.db.matches.find_one({'_id': rec['_id']})
    return rec


async def list_issues(event_id: str, version: Optional[int] = None) -> dict:
    # load match doc
    q: Dict[str, Any] = {'event_id': event_id}
    if version is not None:
        q['version'] = int(version)
    m = await db_mod.db.matches.find_one(q, sort=[('version', -1)])
    if not m:
        return {'groups': [], 'issues': []}
    groups = m.get('groups') or []
    issues: List[dict] = []
    # Build team status map
    team_cancelled: Set[str] = set()
    team_incomplete: Set[str] = set()
    # Build mapping team_id -> reg statuses
    reg_by_team: Dict[str, List[dict]] = {}
    async for r in db_mod.db.registrations.find({'event_id': ObjectId(event_id)}):
        tid = _team_key(r)
        reg_by_team.setdefault(tid, []).append(r)
    async for t in db_mod.db.teams.find({'event_id': ObjectId(event_id)}):
        if (t.get('status') or '').lower() == 'cancelled':
            team_cancelled.add(str(t['_id']))
    for tid, regs in reg_by_team.items():
        cancelled = [r for r in regs if r.get('status') in ('cancelled_by_user','cancelled_admin','refunded')]
        if len(regs) >= 2:
            if len(cancelled) == len(regs):
                team_cancelled.add(tid)
            elif len(cancelled) == 1:
                team_incomplete.add(tid)
        else:
            if cancelled:
                team_cancelled.add(tid)
    # Flag groups
    for g in groups:
        g_issues: List[str] = []
        for tid in [g.get('host_team_id'), *(g.get('guest_team_ids') or [])]:
            if tid in team_cancelled:
                g_issues.append('faulty_team_cancelled')
            if tid in team_incomplete:
                g_issues.append('team_incomplete')
        if g_issues:
            issues.append({'group': g, 'issues': list(sorted(set(g_issues)))})
    return {'groups': groups, 'issues': issues}


async def refunds_overview(event_id: str) -> dict:
    ev = await _get_event(event_id)
    if not ev:
        return {'enabled': False, 'total_refund_cents': 0, 'items': []}
    enabled = bool(ev.get('refund_on_cancellation'))
    items: List[dict] = []
    total = 0
    if not enabled:
        return {'enabled': False, 'total_refund_cents': 0, 'items': []}
    fee = int(ev.get('fee_cents') or 0)
    async for r in db_mod.db.registrations.find({'event_id': ev['_id'], 'status': {'$in': ['cancelled_by_user','cancelled_admin']}}):
        amount = fee * int(r.get('team_size') or 1)
        # find payment
        p = await db_mod.db.payments.find_one({'registration_id': r.get('_id')})
        refunded = 0
        if p and (p.get('status') == 'refunded' or p.get('refunded') is True):
            refunded = amount
        due = max(0, amount - refunded)
        if due > 0:
            items.append({'registration_id': str(r.get('_id')), 'user_email': r.get('user_email_snapshot'), 'amount_cents': due})
            total += due
    return {'enabled': True, 'total_refund_cents': total, 'items': items}


async def _team_emails_map(event_id: str) -> Dict[str, List[str]]:
    """Return mapping team_id(str)->list of member emails; include pseudo-team for solo regs.
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


async def generate_plans_from_matches(event_id: str, version: int) -> int:
    """Generate per-user plans documents from a proposed/finalized match version.

    Overwrites existing plans for (event_id, user_email). Returns number of plans written.
    """
    m = await db_mod.db.matches.find_one({'event_id': event_id, 'version': int(version)})
    if not m:
        return 0
    team_to_emails = await _team_emails_map(event_id)
    groups = m.get('groups') or []
    # per user sections
    sections_by_email: Dict[str, List[dict]] = {}
    def _meal_time(meal: str) -> str:
        return '20:00' if meal=='main' else ('18:00' if meal=='appetizer' else '22:00')
    for g in groups:
        meal = g.get('phase')
        host = g.get('host_team_id')
        guests = g.get('guest_team_ids') or []
        host_emails = team_to_emails.get(str(host), [])
        guest_emails = []
        for tid in guests:
            guest_emails.extend(team_to_emails.get(str(tid), []))
        # For each participant (host or guest), add a section
        # Host section for all participants: identify host email of group (first host email or None)
        host_email = host_emails[0] if host_emails else None
        sec = {
            'meal': meal,
            'time': _meal_time(meal),
            'host': {'email': host_email},
            'guests': guest_emails,
        }
        for em in set(host_emails + guest_emails):
            sections_by_email.setdefault(em, []).append(sec)
    # write plans
    written = 0
    for em, secs in sections_by_email.items():
        await db_mod.db.plans.delete_many({'event_id': ObjectId(event_id), 'user_email': em})
        doc = {
            'event_id': ObjectId(event_id),
            'user_email': em,
            'sections': secs,
            'created_at': __import__('datetime').datetime.utcnow(),
        }
        await db_mod.db.plans.insert_one(doc)
        written += 1
    return written


async def finalize_and_generate_plans(event_id: str, version: int, finalized_by: Optional[str]) -> dict:
    rec = await mark_finalized(event_id, version, finalized_by)
    count = await generate_plans_from_matches(event_id, version)
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
            ok = await send_email(to=em, subject=title, body='\n'.join(lines), category='final_plan')
            sent += 1 if ok else 0
        except Exception:
            pass
    return {'finalized_version': rec.get('version'), 'plans_written': count, 'emails_attempted': sent}
