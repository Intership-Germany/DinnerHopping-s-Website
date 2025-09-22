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
