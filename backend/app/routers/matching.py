from fastapi import APIRouter, HTTPException, Depends
from .. import db as db_mod
from ..auth import get_current_user
import os

router = APIRouter()


@router.post('/{event_id}/start')
async def start_matching(event_id: str, current_user=Depends(get_current_user)):
    roles = current_user.get('roles') or []
    if 'admin' not in roles:
        raise HTTPException(status_code=403, detail='Forbidden')
    # TODO: implement multi-phase matching algorithm
    # For now return 202 accepted and schedule background job (not implemented)
    return {"status": "accepted", "message": "Matching job enqueued (stub)"}


@router.get('/{event_id}/matches')
async def get_matches(event_id: str, current_user=Depends(get_current_user)):
    roles = current_user.get('roles') or []
    if 'admin' not in roles:
        raise HTTPException(status_code=403, detail='Forbidden')
    # TODO: return proposed matches for admin review
    matches = []
    async for m in db_mod.db.matches.find({"event_id": event_id}):
        matches.append(m)
    return matches


@router.post('/{event_id}/finalize')
async def finalize_matches(event_id: str, current_user=Depends(get_current_user)):
    roles = current_user.get('roles') or []
    if 'admin' not in roles:
        raise HTTPException(status_code=403, detail='Forbidden')
    # TODO: mark matches finalized and trigger notifications
    return {"status": "finalized (stub)"}
