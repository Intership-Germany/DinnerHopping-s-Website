from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, List, Dict, Any
from bson.objectid import ObjectId
from bson.errors import InvalidId
import datetime
import random

from app.auth import require_admin, get_current_user
from app import db as db_mod
from app.utils import haversine_m

router = APIRouter(prefix="/admin/matching", tags=["matching"])


async def _load_event_or_404(event_id: str) -> dict:
    try:
        eid = ObjectId(event_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="invalid event id")
    ev = await db_mod.db.events.find_one({'_id': eid})
    if not ev:
        raise HTTPException(status_code=404, detail='event not found')
    return ev


def _is_active_registration(reg: dict) -> bool:
    if reg.get('status') in ('cancelled_by_user','cancelled_admin','refunded','expired'):
        return False
    return True


async def _fetch_match_units(event_id: ObjectId) -> List[Dict[str, Any]]:
    """Collect units (solo or team) for matching.

    Each unit contains:
      unit_id: str (registration_id for solo, team_id for team)
      type: 'solo' | 'team'
      members: list of {user_id, registration_id, diet}
      kitchen_available: bool
      main_course_possible: bool
      preferred_course: optional str (appetizer|main|dessert|any)
      location: {lat, lon} or None
      diet: aggregated diet (vegan > vegetarian > omnivore)
    """
    units: Dict[str, Dict[str, Any]] = {}
    async for reg in db_mod.db.registrations.find({'event_id': event_id}):
        if not _is_active_registration(reg):
            continue
        team_id = reg.get('team_id')
        if team_id:
            tid = str(team_id)
            u = units.get(tid)
            if not u:
                # fetch team doc for meta (members snapshots & cooking location)
                team_doc = await db_mod.db.teams.find_one({'_id': team_id})
                members_snapshot = team_doc.get('members') if team_doc else []
                u = {
                    'unit_id': tid,
                    'type': 'team',
                    'members': [],
                    'kitchen_available': any(m.get('kitchen_available') for m in members_snapshot),
                    'main_course_possible': any(m.get('main_course_possible') for m in members_snapshot),
                    'preferred_course': (team_doc.get('course_preference') if team_doc else None) or 'any',
                    'location': None,
                    'diet': (team_doc.get('team_diet') if team_doc else reg.get('diet')) or 'omnivore',
                }
                units[tid] = u
            u['members'].append({
                'user_id': reg.get('user_id'),
                'registration_id': reg.get('_id'),
                'diet': reg.get('diet', 'omnivore')
            })
        else:
            units[str(reg['_id'])] = {
                'unit_id': str(reg['_id']),
                'type': 'solo',
                'members': [{
                    'user_id': reg.get('user_id'),
                    'registration_id': reg.get('_id'),
                    'diet': reg.get('diet', 'omnivore')
                }],
                'kitchen_available': reg.get('preferences', {}).get('kitchen_available', reg.get('kitchen_available', False)),
                'main_course_possible': reg.get('preferences', {}).get('main_course_possible', False),
                'preferred_course': reg.get('preferences', {}).get('course_preference', 'any'),
                'location': None,
                'diet': reg.get('diet', 'omnivore'),
            }
    return list(units.values())


def _simple_grouping(units: List[Dict[str, Any]], seed: Optional[int] = None) -> List[List[Dict[str, Any]]]:
    """Naive grouping into triplets of units.

    This placeholder ignores most constraints besides grouping size.
    """
    shuffled = list(units)
    rnd = random.Random(seed)
    rnd.shuffle(shuffled)
    groups = []
    for i in range(0, len(shuffled), 3):
        chunk = shuffled[i:i+3]
        if len(chunk) == 3:
            groups.append(chunk)
    return groups


def _score_groups(groups: List[List[Dict[str, Any]]]) -> Dict[str, float]:
    """Compute simple metrics for a grouping.

    Metrics:
      total_units: number of units placed
      group_count: number of groups
    (Travel/diet satisfaction placeholders can be extended later.)
    """
    placed = sum(len(g) for g in groups)
    return {
        'total_units': float(placed),
        'group_count': float(len(groups)),
    }


@router.post('/events/{event_id}/run')
async def run_matching(event_id: str, weights: Optional[Dict[str, float]] = None, seed: Optional[int] = None, _=Depends(require_admin)):
    ev = await _load_event_or_404(event_id)
    if ev.get('status') not in ('open','closed','matched'):
        raise HTTPException(status_code=400, detail='event not in matchable phase')
    units = await _fetch_match_units(ev['_id'])
    if len(units) < 3:
        raise HTTPException(status_code=400, detail='not enough units to match (need >=3)')
    groups = _simple_grouping(units, seed=seed)
    metrics = _score_groups(groups)
    now = datetime.datetime.utcnow()
    doc = {
        'event_id': ev['_id'],
        'created_at': now,
        'updated_at': now,
        'groups': [
            {'units': [{'unit_id': u['unit_id'], 'type': u['type']} for u in grp]} for grp in groups
        ],
        'metrics': metrics,
        'weights': weights or {},
        'status': 'proposed',
        'version': int(ev.get('matching_version', 0)) + 1,
    }
    res = await db_mod.db.match_runs.insert_one(doc)
    # update event matching status
    await db_mod.db.events.update_one({'_id': ev['_id']}, {'$set': {'matching_status': 'proposed', 'matching_version': doc['version']}})
    doc['id'] = str(res.inserted_id)
    doc['event_id'] = str(ev['_id'])
    return doc


@router.get('/events/{event_id}/runs')
async def list_match_runs(event_id: str, _=Depends(require_admin)):
    ev = await _load_event_or_404(event_id)
    out = []
    async for r in db_mod.db.match_runs.find({'event_id': ev['_id']}).sort([('created_at', -1)]):
        r['id'] = str(r['_id'])
        r['event_id'] = str(r['event_id'])
        out.append({k: v for k, v in r.items() if k not in ('_id',)})
    return out


@router.post('/runs/{run_id}/select')
async def select_match_run(run_id: str, _=Depends(require_admin)):
    try:
        rid = ObjectId(run_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail='invalid run id')
    run_doc = await db_mod.db.match_runs.find_one({'_id': rid})
    if not run_doc:
        raise HTTPException(status_code=404, detail='match run not found')
    # mark selected and update event
    await db_mod.db.match_runs.update_one({'_id': rid}, {'$set': {'status': 'selected', 'updated_at': datetime.datetime.utcnow()}})
    await db_mod.db.events.update_one({'_id': run_doc['event_id']}, {'$set': {'matching_status': 'finalized'}})
    return {'status': 'selected'}


@router.get('/runs/{run_id}')
async def get_match_run(run_id: str, _=Depends(require_admin)):
    try:
        rid = ObjectId(run_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail='invalid run id')
    run_doc = await db_mod.db.match_runs.find_one({'_id': rid})
    if not run_doc:
        raise HTTPException(status_code=404, detail='match run not found')
    run_doc['id'] = str(run_doc['_id'])
    run_doc['event_id'] = str(run_doc['event_id'])
    del run_doc['_id']
    return run_doc
from fastapi import APIRouter, HTTPException, Depends
from .. import db as db_mod
from bson.objectid import ObjectId
from ..auth import get_current_user, require_admin
from ..utils import require_event_published
from typing import Optional, List, Dict, Any, Tuple
from app.services.matching import run_algorithms, persist_match_proposal, mark_finalized, list_issues, refunds_overview
from app.services.matching import finalize_and_generate_plans

######### Router / Endpoints #########

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
    # ensure event exists and is published
    ev = await require_event_published(event_id)
    # enforce registration deadline passed if set
    ddl = ev.get('registration_deadline')
    now = __import__('datetime').datetime.utcnow()
    if ddl and isinstance(ddl, __import__('datetime').datetime) and now < ddl:
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
    # also update event matching_status to in_progress when not dry-run
    if not dry_run:
        await db_mod.db.events.update_one({'_id': ObjectId(event_id)}, {'$set': {'matching_status': 'proposed', 'updated_at': __import__('datetime').datetime.utcnow()}})
    return {'status': 'ok', 'dry_run': dry_run, 'proposals': proposals}


@router.get('/{event_id}/matches')
async def get_matches(event_id: str, _=Depends(require_admin)):
    await require_event_published(event_id)
    out = []
    async for m in db_mod.db.matches.find({"event_id": event_id}).sort([('version', -1)]):
        m['id'] = str(m.get('_id'))
        out.append(m)
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
    """Basic manual team move between groups in the same phase.

    Payload: { version:int, phase:str, from_group_idx:int, to_group_idx:int, team_id:str, force?:bool }
    Returns { status: 'moved' | 'noop' | 'warning', violations?: [ {pair:[a,b], count:int} ] }
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
        # simulate new groups for violation analysis
        new_groups = groups[:]
        new_groups[phase_groups_idx[from_idx]] = g_from
        new_groups[phase_groups_idx[to_idx]] = g_to
        # compute pair counts
        pair_counts = _collect_pairs(new_groups)
        violations = [ { 'pair': list(pk), 'count': c } for pk, c in pair_counts.items() if c > 1 ]
        if violations and not force:
            # don't persist; return warning
            return { 'status': 'warning', 'violations': violations }
        # persist move
        await db_mod.db.matches.update_one({'_id': m['_id']}, {'$set': {'groups': new_groups, 'updated_at': __import__('datetime').datetime.utcnow()}})
        return {'status': 'moved', 'violations': violations if violations else []}
    else:
        return {'status': 'noop', 'reason': 'team_not_guest_or_already_present'}


@router.get('/{event_id}/refunds')
async def refunds(event_id: str, _=Depends(require_admin)):
    await require_event_published(event_id)
    return await refunds_overview(event_id)
