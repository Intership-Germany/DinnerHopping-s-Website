from fastapi import APIRouter, Depends, HTTPException
from ..auth import get_current_user, require_admin
from bson import ObjectId
from .. import db as db_mod
from pydantic import BaseModel
from typing import List
import datetime
from ..utils import require_event_published, require_user_registered_or_organizer
from .. import utils

######### Router / Endpoints #########

router = APIRouter()


class CreateGroupIn(BaseModel):
    event_id: str
    section_ref: str
    participant_emails: List[str]


class PostMessageIn(BaseModel):
    group_id: str
    body: str


@router.post('/groups')
async def create_group(payload: CreateGroupIn, current_user=Depends(get_current_user)):
    now = datetime.datetime.now(datetime.timezone.utc)
    # ensure event exists and is published; ensure user is registered or organizer
    ev = await require_event_published(payload.event_id)
    await require_user_registered_or_organizer(current_user, payload.event_id)

    # ensure creator is included
    participants = list(dict.fromkeys(payload.participant_emails or []))
    creator_email = current_user.get('email')
    if creator_email not in participants:
        participants.append(creator_email)

    doc = {
        'event_id': payload.event_id,
        'section_ref': payload.section_ref,
        'participant_emails': participants,
        'created_at': now,
        'created_by': creator_email
    }
    res = await db_mod.db.chat_groups.insert_one(doc)
    # enrich participant info for response
    participants_out = []
    for e in participants:
        u = await db_mod.db.users.find_one({'email': e})
        participants_out.append({'email': e, 'name': u.get('name') if u else None, 'address_public': u.get('address_public') if u else None})
    return {'group_id': str(res.inserted_id), 'event_id': payload.event_id, 'section_ref': payload.section_ref, 'participants': participants_out, 'created_at': now.isoformat(), 'created_by': creator_email}

@router.post('/cleanup')
async def cleanup_chat_groups(days: int = 7, user=Depends(require_admin)):
    """Admin endpoint to clean up chat groups for events older than `days` days.

    This calls a best-effort helper that deletes chat_groups for events whose
    start date is older than `days`.
    """
    res = await utils.cleanup_old_chat_groups(older_than_days=days)
    return res


@router.get('/groups/{group_id}/messages')
async def list_messages(group_id: str, current_user=Depends(get_current_user)):
    # ensure user is part of the group (or group is public in other implementations)
    try:
        oid = ObjectId(group_id)
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid group id')
    grp = await db_mod.db.chat_groups.find_one({'_id': oid})
    if not grp:
        raise HTTPException(status_code=404, detail='Group not found')
    user_email = current_user.get('email')
    # ensure user is member of the group and also registered for the underlying event
    if user_email not in (grp.get('participant_emails') or []) and grp.get('created_by') != user_email:
        raise HTTPException(status_code=403, detail='Not a member of this group')
    ev_id = grp.get('event_id')
    await require_event_published(ev_id)
    await require_user_registered_or_organizer(current_user, ev_id)

    msgs = []
    # fetch messages and enrich with sender public info
    async for m in db_mod.db.chat_messages.find({'group_id': group_id}).sort('created_at', 1):
        sender = None
        if m.get('sender_email'):
            sender = await db_mod.db.users.find_one({'email': m.get('sender_email')})
        msgs.append({
            'id': str(m.get('_id')),
            'group_id': m.get('group_id'),
            'body': m.get('body'),
            'created_at': m.get('created_at').isoformat() if m.get('created_at') else None,
            'sender': {
                'email': sender.get('email') if sender else m.get('sender_email'),
                'name': sender.get('name') if sender else None,
                'address_public': sender.get('address_public') if sender else None,
            }
        })
    return msgs


@router.post('/messages')
async def post_message(payload: PostMessageIn, current_user=Depends(get_current_user)):
    now = datetime.datetime.now(datetime.timezone.utc)
    doc = {
        'group_id': payload.group_id,
        'sender_email': current_user.get('email'),
        'body': payload.body,
        'created_at': now
    }
    res = await db_mod.db.chat_messages.insert_one(doc)
    # return enriched message
    sender = await db_mod.db.users.find_one({'email': current_user.get('email')})
    out = {
        'id': str(res.inserted_id),
        'group_id': payload.group_id,
        'body': payload.body,
        'created_at': now.isoformat(),
        'sender': {
            'email': sender.get('email') if sender else current_user.get('email'),
            'name': sender.get('name') if sender else None,
            'address_public': sender.get('address_public') if sender else None,
        }
    }
    return out


@router.get('/groups')
async def list_groups(current_user=Depends(get_current_user)):
    """Return chat groups the current user participates in."""
    user_email = current_user.get('email')
    groups = []
    async for g in db_mod.db.chat_groups.find({'participant_emails': user_email}).sort('created_at', 1):
        ev_id = g.get('event_id')
        # include only groups for events the user is registered for (or organizer)
        try:
            await require_event_published(ev_id)
        except HTTPException:
            # if event not found or not published, skip
            continue
        try:
            await require_user_registered_or_organizer(current_user, ev_id)
        except HTTPException:
            continue
        groups.append({'id': str(g.get('_id')), 'event_id': g.get('event_id'), 'section_ref': g.get('section_ref'), 'participant_emails': g.get('participant_emails', []), 'created_at': g.get('created_at').isoformat() if g.get('created_at') else None, 'created_by': g.get('created_by')})
    return groups


@router.get('/groups/{group_id}')
async def get_group(group_id: str, current_user=Depends(get_current_user)):
    try:
        oid = ObjectId(group_id)
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid group id')
    g = await db_mod.db.chat_groups.find_one({'_id': oid})
    if not g:
        raise HTTPException(status_code=404, detail='Group not found')
    user_email = current_user.get('email')
    if user_email not in (g.get('participant_emails') or []) and g.get('created_by') != user_email:
        raise HTTPException(status_code=403, detail='Not a member of this group')
    # ensure user is registered/organizer for the event
    await require_event_published(g.get('event_id'))
    await require_user_registered_or_organizer(current_user, g.get('event_id'))
    participants = []
    for e in g.get('participant_emails', []):
        u = await db_mod.db.users.find_one({'email': e})
        participants.append({'email': e, 'name': u.get('name') if u else None, 'address_public': u.get('address_public') if u else None})
    return {'id': str(g.get('_id')), 'event_id': g.get('event_id'), 'section_ref': g.get('section_ref'), 'participants': participants, 'created_at': g.get('created_at').isoformat() if g.get('created_at') else None, 'created_by': g.get('created_by')}
