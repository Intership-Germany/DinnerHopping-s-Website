from fastapi import APIRouter, Depends, HTTPException
from ..auth import get_current_user
from .. import db as db_mod
from pydantic import BaseModel
from typing import List
import datetime

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
    now = datetime.datetime.utcnow()
    doc = {
        'event_id': payload.event_id,
        'section_ref': payload.section_ref,
        'participant_emails': payload.participant_emails,
        'created_at': now,
        'created_by': current_user.get('email')
    }
    res = await db_mod.db.chat_groups.insert_one(doc)
    return {'group_id': str(res.inserted_id)}


@router.get('/groups/{group_id}/messages')
async def list_messages(group_id: str, current_user=Depends(get_current_user)):
    msgs = []
    async for m in db_mod.db.chat_messages.find({'group_id': group_id}).sort('created_at', 1):
        msgs.append(m)
    return msgs


@router.post('/messages')
async def post_message(payload: PostMessageIn, current_user=Depends(get_current_user)):
    now = datetime.datetime.utcnow()
    doc = {
        'group_id': payload.group_id,
        'sender_email': current_user.get('email'),
        'body': payload.body,
        'created_at': now
    }
    res = await db_mod.db.chat_messages.insert_one(doc)
    return {'message_id': str(res.inserted_id)}
