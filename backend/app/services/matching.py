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

    If a user's coordinates are missing and we successfully geocode their address, persist the lat/lon back to the user document.

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
                glat, glon = latlon
                coords.append((glat, glon))
                # persist to user document for future runs
                try:
                    now = __import__('datetime').datetime.utcnow()
                    await db_mod.db.users.update_one({'_id': u['_id']}, {'$set': {'lat': float(glat), 'lon': float(glon), 'geocoded_at': now}})
                except Exception:
                    pass
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
        # broader kitchen capability (avoid hosting anyone without kitchen for any course)
        has_kitchen = team_doc.get('has_kitchen')
        if has_kitchen is None:
            # Fallback heuristic: if can main, assume kitchen exists
            has_kitchen = bool(can_main)
        t['can_host_any'] = bool(has_kitchen)
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
    # Host capability for any course (kitchen availability)
    if meal in ('appetizer', 'dessert') and not host.get('can_host_any', True):
        score -= weights.get('cap_penalty', W_PREF)
        warnings.append('host_no_kitchen')
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
    """Return all unordered pair keys for a group of size >= 2.
    For n<2, returns empty list.
    """
    n = len(triad)
    if n < 2:
        return []
    pairs: List[Tuple[str,str]] = []
    for i in range(n):
        for j in range(i+1, n):
            pairs.append(_pair_key(triad[i], triad[j]))
    return pairs


def _shuffle(seed: int, arr: List[Any]) -> List[Any]:
    rnd = random.Random(seed)
    out = arr[:]
    rnd.shuffle(out)
    return out


async def _member_emails_for_team(team: dict, members_regs: List[dict]) -> List[str]:
    emails: List[str] = []
    mlist = (team.get('members') if isinstance(team, dict) else None) or []
    for m in mlist:
        em = m.get('email')
        if em:
            emails.append(em)
    if not emails and members_regs:
        # fallback to registration snapshot
        for r in members_regs:
            em = r.get('user_email_snapshot')
            if em:
                emails.append(em)
    return list(dict.fromkeys(emails))  # dedupe, preserve order


def _splits_needed(unit_count: int) -> int:
    r = unit_count % 3
    if r == 0:
        return 0
    if r == 1:
        return 2
    return 1  # r == 2


async def _build_units_from_teams(teams: List[dict]) -> Tuple[List[dict], Dict[str, List[str]]]:
    """Construct unit list and mapping unit_id -> emails.

    - Duo team (size>=2) -> single unit: id=str(team_id), size=2
    - Solo team (size==1) -> single unit: id=str(team_id) or 'solo:regid', size=1
    Splitting is handled later.
    """
    units: List[dict] = []
    u2e: Dict[str, List[str]] = {}
    for t in teams:
        tid = str(t['team_id'])
        size = int(t.get('size') or 1)
        unit_id = tid
        unit = {
            'unit_id': unit_id,
            'origin_id': tid,   # used to prevent placing split siblings together (for non-split, origin=itself)
            'size': 2 if size >= 2 else 1,
            'lat': t.get('lat'),
            'lon': t.get('lon'),
            'team_diet': t.get('team_diet') or t.get('diet'),
            'course_preference': t.get('course_preference'),
            'can_host_main': t.get('can_host_main'),
            'can_host_any': t.get('can_host_any'),
            'members_regs': t.get('member_regs') or [],
            'team_doc': t.get('team_doc') or {},
        }
        units.append(unit)
        emails = await _member_emails_for_team(unit['team_doc'], unit['members_regs'])
        u2e[unit_id] = emails
    return units, u2e


async def _apply_minimal_splits(units: List[dict], unit_emails: Dict[str, List[str]]) -> Tuple[List[dict], Dict[str, List[str]]]:
    """If total units not divisible by 3, split minimal number of duo units.

    Splitting a duo unit produces two units with ids 'split:<email>' where email comes
    from the team's member list; both inherit lat/lon and attributes from the team, with size=1.

    Note: We only split as a last resort to reach a multiple of 3 teams, to keep solos rare.
    """
    need = _splits_needed(len(units))
    if need == 0:
        return units, unit_emails
    # candidates: duo units with at least 2 member emails
    cands = [u for u in units if u.get('size', 1) >= 2 and len(unit_emails.get(u['unit_id'], [])) >= 2]
    if len(cands) < need:
        # not enough duos to split; best-effort: no split
        return units, unit_emails
    rnd = random.Random(int(os.getenv('MATCH_SPLIT_SEED', '777')))
    # Prefer splitting duos that are geographically isolated (no coords) to keep good duos intact
    cands.sort(key=lambda u: (u.get('lat') is None or u.get('lon') is None))
    to_split = cands[:need]
    remaining: List[dict] = [u for u in units if u not in to_split]
    for u in to_split:
        emails = unit_emails.get(u['unit_id'], [])
        mems = emails[:2]
        if len(mems) < 2:
            # safety
            remaining.append(u)
            continue
        for em in mems:
            uid = f"split:{em.lower()}"
            remaining.append({
                'unit_id': uid,
                'origin_id': u['unit_id'],
                'size': 1,
                'lat': u.get('lat'),
                'lon': u.get('lon'),
                'team_diet': u.get('team_diet'),
                'course_preference': u.get('course_preference'),
                'can_host_main': u.get('can_host_main'),
                'can_host_any': u.get('can_host_any'),
                'members_regs': u.get('members_regs'),
                'team_doc': u.get('team_doc'),
            })
            unit_emails[uid] = [em]
        # remove original mapping (already removed from remaining)
        unit_emails.pop(u['unit_id'], None)
    return remaining, unit_emails


def _group_units_in_triads(units: List[dict]) -> List[List[dict]]:
    """Group units into triads of 3 TEAMS prioritizing duos, keeping solos rare.

    Strategy:
    - Prefer groups with 3 duos when possible.
    - Otherwise, form groups with 2 duos + 1 solo.
    - If necessary, form 1 duo + 2 solos, and as last resort 3 solos.
    - Avoid placing siblings (same origin_id) together when possible.
    Assumes len(units) % 3 == 0 ideally (minimal split performed upstream).
    """
    # Work on copies
    duos = [u for u in units if int(u.get('size') or 1) >= 2]
    solos = [u for u in units if int(u.get('size') or 1) == 1]

    # Preserve input relative order but we will pop from front
    def pop_first(pool: List[dict], cond) -> Optional[dict]:
        for i, x in enumerate(pool):
            if cond(x):
                return pool.pop(i)
        return None

    groups: List[List[dict]] = []

    def fill_with_any(grp: List[dict]):
        """Fill group up to 3 with any remaining units if constraints impossible."""
        nonlocal duos, solos
        while len(grp) < 3 and (duos or solos):
            if duos:
                grp.append(duos.pop(0))
            elif solos:
                grp.append(solos.pop(0))
        return grp

    # Phase 1: 3 duos groups
    while len(duos) >= 3:
        g: List[dict] = []
        a = duos.pop(0)
        g.append(a)
        b = pop_first(duos, lambda u: u['origin_id'] != a['origin_id']) or (duos.pop(0) if duos else None)
        if b:
            g.append(b)
        c = None
        if b:
            c = pop_first(duos, lambda u: u['origin_id'] not in (a['origin_id'], b['origin_id']))
        if not c:
            # try a solo instead to avoid siblings
            c = pop_first(solos, lambda u: u['origin_id'] not in (a['origin_id'], *( [b['origin_id']] if b else [])))
        if not c and duos:
            c = duos.pop(0)
        if c:
            g.append(c)
        g = fill_with_any(g)
        groups.append(g)

    # Phase 2: 2 duos + 1 solo
    while len(duos) >= 2 and solos:
        g: List[dict] = []
        a = duos.pop(0)
        g.append(a)
        b = pop_first(duos, lambda u: u['origin_id'] != a['origin_id']) or (duos.pop(0) if duos else None)
        if b:
            g.append(b)
        c = pop_first(solos, lambda u: u['origin_id'] not in (a['origin_id'], *( [b['origin_id']] if b else []))) or (solos.pop(0) if solos else None)
        if c:
            g.append(c)
        g = fill_with_any(g)
        groups.append(g)

    # Phase 3: 1 duo + 2 solos
    while duos and len(solos) >= 2:
        g: List[dict] = []
        a = duos.pop(0)
        g.append(a)
        s1 = pop_first(solos, lambda u: u['origin_id'] != a['origin_id']) or (solos.pop(0) if solos else None)
        if s1:
            g.append(s1)
        s2 = pop_first(solos, lambda u: u['origin_id'] not in (a['origin_id'], *( [s1['origin_id']] if s1 else []))) or (solos.pop(0) if solos else None)
        if s2:
            g.append(s2)
        g = fill_with_any(g)
        groups.append(g)

    # Phase 4: leftovers (all duos or all solos or mix) - just fill respecting size priority
    rest = duos + solos
    while rest:
        g: List[dict] = []
        while rest and len(g) < 3:
            g.append(rest.pop(0))
        groups.append(g)

    return groups


def _unit_ids(group: List[dict]) -> List[str]:
    return [u['unit_id'] for u in group]


async def _phase_groups(units: List[dict], phase: str, used_pairs: Set[Tuple[str,str]], weights: dict) -> List[dict]:
    # form triads (3 teams)
    triads = _group_units_in_triads(units)
    groups: List[dict] = []
    for tri in triads:
        if len(tri) < 2:
            continue
        tri_ids = _unit_ids(tri)
        # avoid duplicate pairs by rotation if possible
        rot_attempts = 0
        while any(pk in used_pairs for pk in _triad_pairs(tri_ids)) and rot_attempts < 3:
            tri = tri[1:] + tri[:1]
            tri_ids = _unit_ids(tri)
            rot_attempts += 1
        # host selection
        candidates = tri[:]
        # Prefer capability first, then preference match
        def host_sort_key(t: dict) -> Tuple[int, int]:
            pref = -1 if (str(t.get('course_preference') or '').lower() == phase) else 0
            if phase == 'main':
                cap = -1 if t.get('can_host_main') else 1
            else:
                cap = -1 if t.get('can_host_any', True) else 1
            return (cap, pref)
        candidates.sort(key=host_sort_key)
        host = candidates[0]
        if phase == 'main' and not host.get('can_host_main'):
            for c in candidates[1:]:
                if c.get('can_host_main'):
                    host = c
                    break
        if phase in ('appetizer','dessert') and not host.get('can_host_any', True):
            for c in candidates[1:]:
                if c.get('can_host_any', True):
                    host = c
                    break
        guests = [t for t in tri if t['unit_id'] != host['unit_id']]
        base_score, warns = _score_group_phase(host, guests, phase, weights)
        travel = await _travel_time_for_phase(host, guests)
        rec = {
            'phase': phase,
            'host_team_id': host['unit_id'],
            'guest_team_ids': [g['unit_id'] for g in guests],
            'score': base_score - weights.get('dist', W_DIST) * (travel or 0.0),
            'travel_seconds': travel,
            'warnings': warns,
        }
        groups.append(rec)
        for pk in _triad_pairs([host['unit_id'], *[g['unit_id'] for g in guests]]):
            used_pairs.add(pk)
    return groups


async def algo_greedy(event_oid, weights: dict, seed: int = 42) -> dict:
    teams = await _build_teams(event_oid)
    # Convert to units and split if needed to make count divisible by 3
    units, unit_emails = await _build_units_from_teams(teams)
    units, unit_emails = await _apply_minimal_splits(units, unit_emails)
    # Shuffle base ordering
    rnd = random.Random(seed)
    rnd.shuffle(units)
    phases = ['appetizer','main','dessert']
    used_pairs: Set[Tuple[str,str]] = set()
    all_groups: List[dict] = []
    for idx, phase in enumerate(phases):
        # rotate units between phases to diversify
        if idx > 0:
            units = units[1:] + units[:1]
        groups = await _phase_groups(units, phase, used_pairs, weights)
        all_groups.extend(groups)
    metrics = _compute_metrics(all_groups, weights)
    return { 'algorithm': 'greedy', 'groups': all_groups, 'metrics': metrics }


async def algo_random(event_oid, weights: dict, seed: int = 99) -> dict:
    teams = await _build_teams(event_oid)
    units, unit_emails = await _build_units_from_teams(teams)
    units, unit_emails = await _apply_minimal_splits(units, unit_emails)
    rnd = random.Random(seed)
    rnd.shuffle(units)
    phases = ['appetizer','main','dessert']
    used_pairs: Set[Tuple[str,str]] = set()
    all_groups: List[dict] = []
    for phase in phases:
        rnd.shuffle(units)
        groups = await _phase_groups(units, phase, used_pairs, weights)
        all_groups.extend(groups)
    metrics = _compute_metrics(all_groups, weights)
    return { 'algorithm': 'random', 'groups': all_groups, 'metrics': metrics }


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
    """Extend mapping with any split:<email> ids seen in groups."""
    out = dict(base)
    for g in groups:
        ids = [g.get('host_team_id'), *(g.get('guest_team_ids') or [])]
        for tid in ids:
            if isinstance(tid, str) and tid.startswith('split:'):
                email = tid.split(':', 1)[1]
                out.setdefault(tid, []).append(email)
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
    # per user sections
    sections_by_email: Dict[str, List[dict]] = {}
    def _meal_time(meal: str) -> str:
        return '20:00' if meal=='main' else ('18:00' if meal=='appetizer' else '22:00')
    for g in groups:
        meal = g.get('phase')
        host = g.get('host_team_id')
        guests = g.get('guest_team_ids') or []
        host_emails = team_to_emails.get(str(host), [])
        guest_emails: List[str] = []
        for tid in guests:
            guest_emails.extend(team_to_emails.get(str(tid), []))
        host_email = host_emails[0] if host_emails else None
        sec = {
            'meal': meal,
            'time': _meal_time(meal),
            'host': {'email': host_email, 'emails': host_emails},  # include all host emails for duo transparency
            'guests': guest_emails,
        }
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
