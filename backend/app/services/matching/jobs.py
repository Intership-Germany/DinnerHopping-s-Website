from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid
from typing import Any, Dict, List, Optional

from bson import ObjectId

from ... import db as db_mod
from .algorithms import run_algorithms
from .operations import persist_match_proposal

logger = logging.getLogger(__name__)

_ACTIVE_JOBS: Dict[str, asyncio.Task[Any]] = {}
_STATUS_IN_PROGRESS = {'queued', 'running'}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _serialize_job(doc: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        'id': str(doc.get('_id')),
        'event_id': doc.get('event_id'),
        'status': doc.get('status'),
        'progress': float(doc.get('progress', 0.0)),
        'message': doc.get('message'),
        'algorithms': doc.get('algorithms', []),
        'weights': doc.get('weights', {}),
        'dry_run': bool(doc.get('dry_run', False)),
        'proposals': doc.get('proposals', []),
        'requested_by': str(doc.get('requested_by')) if doc.get('requested_by') is not None else None,
        'error': doc.get('error'),
    }
    for key in ('created_at', 'started_at', 'completed_at', 'updated_at'):
        value = doc.get(key)
        if isinstance(value, dt.datetime):
            out[key] = value.isoformat()
        elif value is not None:
            out[key] = str(value)
        else:
            out[key] = None
    return out


async def enqueue_matching_job(
    event_id: str,
    *,
    algorithms: List[str],
    weights: Dict[str, float],
    dry_run: bool,
    requested_by: Optional[str] = None,
) -> Dict[str, Any]:
    existing = await db_mod.db.matching_jobs.find_one({'event_id': event_id, 'status': {'$in': list(_STATUS_IN_PROGRESS)}})
    if existing:
        return {
            'was_enqueued': False,
            'job': _serialize_job(existing),
        }

    job_id = uuid.uuid4().hex
    now = _now()
    doc: Dict[str, Any] = {
        '_id': job_id,
        'event_id': event_id,
        'status': 'queued',
        'progress': 0.0,
        'message': 'Waiting to start',
        'algorithms': algorithms,
        'weights': weights,
        'dry_run': dry_run,
        'proposals': [],
        'error': None,
        'requested_by': requested_by,
        'created_at': now,
        'updated_at': now,
        'started_at': None,
        'completed_at': None,
    }
    await db_mod.db.matching_jobs.insert_one(doc)

    loop = asyncio.get_running_loop()
    task = loop.create_task(_run_matching_job(job_id, event_id, algorithms, weights, dry_run))
    _ACTIVE_JOBS[job_id] = task

    def _cleanup(_task: asyncio.Task[Any]) -> None:
        _ACTIVE_JOBS.pop(job_id, None)

    task.add_done_callback(_cleanup)

    return {
        'was_enqueued': True,
        'job': _serialize_job(doc),
    }


async def get_matching_job(job_id: str) -> Optional[Dict[str, Any]]:
    doc = await db_mod.db.matching_jobs.find_one({'_id': job_id})
    if not doc:
        return None
    return _serialize_job(doc)


async def list_matching_jobs(event_id: str, *, limit: int = 10) -> List[Dict[str, Any]]:
    cursor = db_mod.db.matching_jobs.find({'event_id': event_id}).sort([('created_at', -1)]).limit(limit)
    items: List[Dict[str, Any]] = []
    async for doc in cursor:
        items.append(_serialize_job(doc))
    return items


async def _update_job(job_id: str, *, unset: Optional[List[str]] = None, **fields: Any) -> None:
    update_doc: Dict[str, Any] = {'$set': {**fields, 'updated_at': _now()}}
    if unset:
        update_doc['$unset'] = {key: '' for key in unset}
    await db_mod.db.matching_jobs.update_one({'_id': job_id}, update_doc)


async def _run_matching_job(job_id: str, event_id: str, algorithms: List[str], weights: Dict[str, float], dry_run: bool) -> None:
    try:
        await _update_job(job_id, status='running', progress=0.05, message='Initializing...', started_at=_now())

        if not dry_run:
            try:
                await db_mod.db.events.update_one(
                    {'_id': ObjectId(event_id)},
                    {'$set': {'matching_status': 'in_progress', 'updated_at': _now()}},
                )
            except Exception:
                logger.exception('Failed to mark event %s as in_progress', event_id)

        total = max(1, len(algorithms))

        async def _progress_callback(payload: Dict[str, Any]) -> None:
            stage = payload.get('stage')
            algorithm = payload.get('algorithm', 'unknown')
            index = int(payload.get('index', 1))
            span = 0.65
            base = 0.1
            total_local = int(payload.get('total') or total)
            total_local = max(1, total_local)
            ratio = float(payload.get('ratio') or 0.0)
            ratio = max(0.0, min(1.0, ratio))
            custom_message = payload.get('message')
            if stage == 'start':
                progress = base + span * ((index - 1) / total_local)
                message = custom_message or f"Starting {algorithm}..."
            elif stage == 'step':
                progress = base + span * ((index - 1 + ratio) / total_local)
                if custom_message:
                    message = custom_message
                else:
                    percent = int(round(ratio * 100))
                    message = f"{algorithm} {percent}% complete"
            else:
                progress = base + span * (index / total_local)
                message = custom_message or f"Finished {algorithm}"
            await _update_job(job_id, progress=min(progress, 0.95), message=message)

        await _update_job(job_id, message='Loading data...')
        results = await run_algorithms(event_id, algorithms=algorithms, weights=weights, progress_cb=_progress_callback)

        proposals: List[Dict[str, Any]] = []
        await _update_job(job_id, progress=0.96, message='Processing results...')
        for res in results:
            algo_name = res.get('algorithm')
            if dry_run:
                proposals.append({
                    'algorithm': algo_name,
                    'metrics': res.get('metrics'),
                    'preview_groups': (res.get('groups') or [])[:6],
                    'unmatched_units': res.get('unmatched_units') or [],
                })
            else:
                saved = await persist_match_proposal(event_id, res)
                proposals.append({
                    'algorithm': algo_name,
                    'version': saved.get('version'),
                    'metrics': saved.get('metrics'),
                    'unmatched_units': res.get('unmatched_units') or [],
                })
        update_fields: Dict[str, Any] = {
            'status': 'completed',
            'progress': 1.0,
            'message': 'Completed',
            'proposals': proposals,
            'completed_at': _now(),
        }
        await _update_job(job_id, **update_fields)

        if not dry_run:
            try:
                await db_mod.db.events.update_one(
                    {'_id': ObjectId(event_id)},
                    {'$set': {'matching_status': 'proposed', 'updated_at': _now()}},
                )
            except Exception:
                logger.exception('Failed to mark event %s as proposed', event_id)
    except asyncio.CancelledError:
        await _update_job(job_id, status='cancelled', progress=1.0, message='Cancelled', completed_at=_now())
        raise
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception('Matching job %s failed: %s', job_id, exc)
        error_message = str(exc)
        await _update_job(
            job_id,
            status='failed',
            progress=1.0,
            message='Matching failed',
            error=error_message,
            completed_at=_now(),
        )
        if not dry_run:
            try:
                await db_mod.db.events.update_one(
                    {'_id': ObjectId(event_id)},
                    {'$set': {'matching_status': 'not_started', 'updated_at': _now()}},
                )
            except Exception:
                logger.exception('Failed to reset matching_status after job %s failure', job_id)