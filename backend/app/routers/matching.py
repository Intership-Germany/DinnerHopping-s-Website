from fastapi import APIRouter, HTTPException, Depends
from .. import db as db_mod
from bson.objectid import ObjectId
from ..auth import get_current_user, require_admin
from ..utils import require_event_published
from typing import Optional, List, Dict, Any, Tuple
from app.services.matching import run_algorithms, persist_match_proposal, mark_finalized, list_issues, refunds_overview, finalize_and_generate_plans, _build_teams, _score_group_phase, _travel_time_for_phase, _compute_metrics, _team_emails_map  # reuse internal helpers
import datetime

######### Router / Endpoints #########

# Main matching router (mounted under /matching in main.py)
router = APIRouter()


def _pair_key(a: str, b: str) -> Tuple[str, str]:
    x, y = sorted([a, b])
    return (x, y)


def _collect_pairs(groups: List[dict]) -> Dict[Tuple[str,str], int]:
    counts: Dict[Tuple[str,str], int] = {}
    for g in groups:
        host = g.get('host_team_id')
        guests = g.get('guest_team_ids') or []
        # host meets each guest
        for t in guests:
            pk = _pair_key(host, t)
            counts[pk] = counts.get(pk, 0) + 1
        # guests also meet each other in same group
        for i in range(len(guests)):
            for j in range(i+1, len(guests)):
                pk = _pair_key(guests[i], guests[j])
                counts[pk] = counts.get(pk, 0) + 1
    return counts


@router.post('/{event_id}/start')
async def start_matching(event_id: str, payload: dict | None = None, current_admin=Depends(require_admin)):
    # ensure event exists and is published/open
    ev = await require_event_published(event_id)
    # enforce registration deadline passed if set
    ddl = ev.get('registration_deadline')
    now = datetime.datetime.utcnow()
    if ddl and isinstance(ddl, datetime.datetime) and now < ddl:
        raise HTTPException(status_code=400, detail='Registration deadline has not passed yet')
    payload = payload or {}
    algorithms: List[str] = payload.get('algorithms') or ['greedy', 'random']
    weights: Dict[str, float] = payload.get('weights') or {}
    dry_run: bool = bool(payload.get('dry_run', False))

    results = await run_algorithms(event_id, algorithms=algorithms, weights=weights)
    proposals: List[Dict[str, Any]] = []
    for res in results:
        if dry_run:
            proposals.append({'algorithm': res.get('algorithm'), 'metrics': res.get('metrics'), 'preview_groups': res.get('groups')[:6]})
        else:
            saved = await persist_match_proposal(event_id, res)
            proposals.append({'algorithm': res.get('algorithm'), 'version': saved.get('version'), 'metrics': saved.get('metrics')})
    # update event matching_status when not dry-run
    if not dry_run:
        await db_mod.db.events.update_one({'_id': ObjectId(event_id)}, {'$set': {'matching_status': 'proposed', 'updated_at': datetime.datetime.utcnow()}})
    return {'status': 'ok', 'dry_run': dry_run, 'proposals': proposals}


@router.get('/{event_id}/matches')
async def get_matches(event_id: str, _=Depends(require_admin)):
    await require_event_published(event_id)

    def _serialize(obj):
        if isinstance(obj, list):
            return [_serialize(v) for v in obj]
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k == '_id':
                    # drop raw _id (converted to id separately)
                    continue
                out[k] = _serialize(v)
            return out
        if isinstance(obj, ObjectId):
            return str(obj)
        if isinstance(obj, (datetime.datetime, datetime.date)):
            try:
                return obj.isoformat()
            except Exception:
                return str(obj)
        return obj

    out: List[dict] = []
    async for m in db_mod.db.matches.find({"event_id": event_id}).sort([('version', -1)]):
        m['id'] = str(m.get('_id')) if m.get('_id') is not None else None
        # ensure event_id is a string
        if isinstance(m.get('event_id'), ObjectId):
            m['event_id'] = str(m['event_id'])
        serialized = _serialize(m)
        out.append(serialized)
    return out


@router.get('/{event_id}/issues')
async def get_issues(event_id: str, version: Optional[int] = None, _=Depends(require_admin)):
    await require_event_published(event_id)
    return await list_issues(event_id, version)


@router.post('/{event_id}/finalize')
async def finalize_matches(event_id: str, version: int, current_admin=Depends(require_admin)):
    await require_event_published(event_id)
    result = await finalize_and_generate_plans(event_id, int(version), str(current_admin.get('_id')))
    return {'status': 'finalized', **result}


@router.post('/{event_id}/move')
async def move_team(event_id: str, payload: dict, _=Depends(require_admin)):
    """Move a team between groups in the same phase for a given match version.

    Payload: { version:int, phase:str, from_group_idx:int, to_group_idx:int, team_id:str, force?:bool }
    """
    await require_event_published(event_id)
    version = int(payload.get('version'))
    phase = str(payload.get('phase'))
    team_id = str(payload.get('team_id'))
    from_idx = int(payload.get('from_group_idx'))
    to_idx = int(payload.get('to_group_idx'))
    force = bool(payload.get('force', False))

    m = await db_mod.db.matches.find_one({'event_id': event_id, 'version': version})
    if not m:
        raise HTTPException(status_code=404, detail='Match version not found')
    groups = m.get('groups') or []
    phase_groups_idx = [i for i,g in enumerate(groups) if g.get('phase') == phase]
    if from_idx >= len(phase_groups_idx) or to_idx >= len(phase_groups_idx):
        raise HTTPException(status_code=400, detail='Invalid group indices')
    g_from = groups[phase_groups_idx[from_idx]]
    g_to = groups[phase_groups_idx[to_idx]]

    # Move only if team is guest in from-group and not already present in to-group
    if team_id in g_from.get('guest_team_ids', []) and team_id not in (g_to.get('guest_team_ids', []) + [g_to.get('host_team_id')]):
        g_from['guest_team_ids'] = [t for t in g_from.get('guest_team_ids', []) if t != team_id]
        g_to.setdefault('guest_team_ids', []).append(team_id)
        new_groups = groups[:]
        new_groups[phase_groups_idx[from_idx]] = g_from
        new_groups[phase_groups_idx[to_idx]] = g_to
        pair_counts = _collect_pairs(new_groups)
        violations = [ { 'pair': list(pk), 'count': c } for pk, c in pair_counts.items() if c > 1 ]
        if violations and not force:
            return { 'status': 'warning', 'violations': violations }
        await db_mod.db.matches.update_one({'_id': m['_id']}, {'$set': {'groups': new_groups, 'updated_at': datetime.datetime.utcnow()}})
        return {'status': 'moved', 'violations': violations if violations else []}
    return {'status': 'noop', 'reason': 'team_not_guest_or_already_present'}


@router.get('/{event_id}/refunds')
async def refunds(event_id: str, _=Depends(require_admin)):
    await require_event_published(event_id)
    return await refunds_overview(event_id)


@router.get('/{event_id}/details')
async def match_details(event_id: str, version: Optional[int] = None, _=Depends(require_admin)):
    """Return enriched details for a match proposal: groups, metrics, and team_details.

    team_details: { team_id: { size, team_diet, course_preference, can_host_main, lat, lon, members: [ {email, first_name, last_name, display_name} ] } }
    """
    # Find match doc
    q: Dict[str, Any] = {'event_id': event_id}
    if version is not None:
        q['version'] = int(version)
    m = await db_mod.db.matches.find_one(q, sort=[('version', -1)])
    if not m:
        raise HTTPException(status_code=404, detail='No match found')
    # Build team details map
    ev = await db_mod.db.events.find_one({'_id': ObjectId(event_id)})
    if not ev:
        raise HTTPException(status_code=404, detail='Event not found')
    teams = await _build_teams(ev['_id'])
    team_map: Dict[str, dict] = {}
    for t in teams:
        team_map[str(t['team_id'])] = {
            'size': t.get('size'),
            'team_diet': t.get('team_diet'),
            'course_preference': t.get('course_preference'),
            'can_host_main': t.get('can_host_main'),
            'lat': t.get('lat'),
            'lon': t.get('lon'),
        }
    # Attach members (names) using team->emails mapping
    emails_map = await _team_emails_map(event_id)
    # Gather all emails to bulk fetch names
    all_emails = set()
    for ems in emails_map.values():
        for em in ems:
            all_emails.add(em)
    users_by_email: Dict[str, dict] = {}
    if all_emails:
        async for u in db_mod.db.users.find({'email': {'$in': list(all_emails)}}):
            users_by_email[u.get('email')] = u
    for tid, ems in emails_map.items():
        members = []
        for em in ems:
            u = users_by_email.get(em) or {}
            fn = (u.get('first_name') or u.get('firstname') or '').strip()
            ln = (u.get('last_name') or u.get('lastname') or '').strip()
            disp = (f"{fn} {ln}" if (fn or ln) else em).strip()
            members.append({'email': em, 'first_name': fn or None, 'last_name': ln or None, 'display_name': disp})
        team_map.setdefault(tid, {})['members'] = members
    # Compose output
    out = {
        'version': m.get('version'),
        'metrics': m.get('metrics') or {},
        'algorithm': m.get('algorithm') or 'unknown',
        'groups': m.get('groups') or [],
        'team_details': team_map,
    }
    return out


@router.post('/{event_id}/recompute')
async def recompute_metrics(event_id: str, version: int, _=Depends(require_admin)):
    """Recompute per-group travel_seconds and score and aggregate metrics for the given version, update the stored match doc, and return the updated metrics.
    """
    m = await db_mod.db.matches.find_one({'event_id': event_id, 'version': int(version)})
    if not m:
        raise HTTPException(status_code=404, detail='Match version not found')
    ev = await db_mod.db.events.find_one({'_id': ObjectId(event_id)})
    if not ev:
        raise HTTPException(status_code=404, detail='Event not found')
    # Build team mapping with coordinates and attributes
    teams = await _build_teams(ev['_id'])
    tmap: Dict[str, dict] = { str(t['team_id']): t for t in teams }
    groups = m.get('groups') or []
    new_groups: List[dict] = []
    for g in groups:
        phase = g.get('phase')
        host_id = str(g.get('host_team_id'))
        guest_ids = [str(x) for x in (g.get('guest_team_ids') or [])]
        host = tmap.get(host_id, {})
        guests = [tmap.get(tid, {}) for tid in guest_ids]
        base_score, warns = _score_group_phase(host, guests, phase, {})
        travel = await _travel_time_for_phase(host, guests)
        new_groups.append({
            **g,
            'score': base_score - 1.0 * (travel or 0.0),  # default W_DIST=1
            'travel_seconds': travel,
            'warnings': warns,
        })
    metrics = _compute_metrics(new_groups, {})
    await db_mod.db.matches.update_one({'_id': m['_id']}, {'$set': {'groups': new_groups, 'metrics': metrics, 'updated_at': datetime.datetime.utcnow()}})
    return {'version': m.get('version'), 'metrics': metrics}


@router.post('/{event_id}/validate')
async def validate_groups(event_id: str, payload: dict, _=Depends(require_admin)):
    """Validate a provided groups array: detect duplicate pair meetings and phase membership conflicts.

    Payload: { groups: [ { phase, host_team_id, guest_team_ids: [] }, ... ] }
    Returns: { violations: [ { pair:[a,b], count:int } ], phase_issues: [ { phase, team_id, issue } ], group_issues: [ { phase, group_idx, issue } ] }
    """
    groups = payload.get('groups') or []
    # Duplicate pair detection
    pair_counts = _collect_pairs(groups)
    violations = [ { 'pair': list(pk), 'count': c } for pk, c in pair_counts.items() if c > 1 ]
    # Phase membership conflicts and basic group issues
    phase_team_seen: Dict[str, Dict[str,int]] = {}
    group_issues: List[dict] = []
    for idx, g in enumerate(groups):
        phase = g.get('phase')
        if not phase:
            group_issues.append({'phase': None, 'group_idx': idx, 'issue': 'missing_phase'})
            continue
        phase_team_seen.setdefault(phase, {})
        host = str(g.get('host_team_id')) if g.get('host_team_id') is not None else None
        guests = [str(t) for t in (g.get('guest_team_ids') or [])]
        # Basic issues
        if host and host in guests:
            group_issues.append({'phase': phase, 'group_idx': idx, 'issue': 'host_in_guests'})
        if len(set(guests)) != len(guests):
            group_issues.append({'phase': phase, 'group_idx': idx, 'issue': 'duplicate_guest_in_group'})
        # Track membership per phase
        all_members = [host] if host else []
        all_members += guests
        for tid in all_members:
            if not tid:
                continue
            phase_team_seen[phase][tid] = phase_team_seen[phase].get(tid, 0) + 1
    phase_issues: List[dict] = []
    for phase, seen in phase_team_seen.items():
        for tid, cnt in seen.items():
            if cnt > 1:
                phase_issues.append({'phase': phase, 'team_id': tid, 'issue': 'team_appears_multiple_times'})
    return { 'violations': violations, 'phase_issues': phase_issues, 'group_issues': group_issues }


@router.post('/{event_id}/set_groups')
async def set_groups(event_id: str, payload: dict, _=Depends(require_admin)):
    """Replace the groups array for a given match version.

    Payload: { version:int, groups:[...], force?:bool }
    If duplicate pair meetings are detected and force is not true, returns { status:'warning', violations:[...] }.
    On success, persists groups, recomputes metrics and returns { status:'ok', metrics }.
    """
    version = int(payload.get('version'))
    groups = payload.get('groups') or []
    force = bool(payload.get('force', False))
    m = await db_mod.db.matches.find_one({'event_id': event_id, 'version': version})
    if not m:
        raise HTTPException(status_code=404, detail='Match version not found')
    # Validate
    check = await validate_groups(event_id, {'groups': groups})
    violations = check.get('violations') or []
    phase_issues = check.get('phase_issues') or []
    if (violations or phase_issues) and not force:
        return { 'status': 'warning', 'violations': violations, 'phase_issues': phase_issues }
    # Persist and recompute
    await db_mod.db.matches.update_one({'_id': m['_id']}, {'$set': {'groups': groups, 'updated_at': datetime.datetime.utcnow()}})
    # Recompute metrics
    rec = await recompute_metrics(event_id, version)  # type: ignore
    return { 'status': 'ok', **rec }


@router.delete('/{event_id}/matches')
async def delete_matches(event_id: str, version: Optional[int] = None, _=Depends(require_admin)):
    """Delete match proposals for an event. If version is provided, delete only that version; otherwise delete all.
    Returns { deleted_count, scope: 'single'|'all' }.
    """
    if version is not None:
        res = await db_mod.db.matches.delete_many({'event_id': event_id, 'version': int(version)})
        return {'deleted_count': getattr(res, 'deleted_count', 0), 'scope': 'single', 'version': int(version)}
    res = await db_mod.db.matches.delete_many({'event_id': event_id})
    # Optionally reset event matching_status if all deleted
    await db_mod.db.events.update_one({'_id': ObjectId(event_id)}, {'$set': {'matching_status': 'not_started', 'updated_at': datetime.datetime.utcnow()}})
    return {'deleted_count': getattr(res, 'deleted_count', 0), 'scope': 'all'}
