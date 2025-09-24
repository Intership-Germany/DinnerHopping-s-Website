from fastapi import APIRouter, HTTPException, Depends
from .. import db as db_mod
from bson.objectid import ObjectId
from ..auth import get_current_user, require_admin
from ..utils import require_event_published
from typing import Optional, List, Dict, Any, Tuple
from app.services.matching import run_algorithms, persist_match_proposal, mark_finalized, list_issues, refunds_overview, finalize_and_generate_plans
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
