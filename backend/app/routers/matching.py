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
import os

######### Router / Endpoints #########

router = APIRouter()


@router.post('/{event_id}/start')
async def start_matching(event_id: str, _=Depends(require_admin)):
    # ensure event exists and is published
    await require_event_published(event_id)
    # TODO: implement multi-phase matching algorithm
    # For now return 202 accepted and schedule background job (not implemented)
    return {"status": "accepted", "message": "Matching job enqueued (stub)"}


@router.get('/{event_id}/matches')
async def get_matches(event_id: str, _=Depends(require_admin)):
    await require_event_published(event_id)
    # TODO: return proposed matches for admin review
    matches = []
    async for m in db_mod.db.matches.find({"event_id": event_id}):
        matches.append(m)
    return matches


@router.post('/{event_id}/finalize')
async def finalize_matches(event_id: str, _=Depends(require_admin)):
    await require_event_published(event_id)
    # TODO: mark matches finalized and trigger notifications
    return {"status": "finalized (stub)"}
