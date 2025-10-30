from fastapi import APIRouter, HTTPException, Depends, Response
from .. import db as db_mod
from bson.objectid import ObjectId
from bson.errors import InvalidId
from ..auth import require_admin
from ..utils import require_event_published
from typing import Optional, List, Dict, Any, Tuple, Set
from ..services.matching import (
    list_issues,
    finalize_and_generate_plans,
    _build_teams,
    _score_group_phase,
    _travel_time_for_phase,
    _compute_metrics,
    _team_emails_map,
    compute_team_paths,
    enqueue_matching_job,
    get_matching_job,
    list_matching_jobs,
    optimize_match_result,
)
from ..services.matching import jobs as _matching_jobs_module
from ..services.matching.config import geocode_missing_enabled
from ..services.routing import route_polyline
import datetime

######### Router / Endpoints #########

# Main matching router (mounted under /matching in main.py)
router = APIRouter()


def _compose_address(address_struct: Optional[dict]) -> Optional[str]:
    if not isinstance(address_struct, dict):
        return None
    street = str(address_struct.get('street') or '').strip()
    street_no = str(address_struct.get('street_no') or '').strip()
    city = str(address_struct.get('city') or '').strip()
    postal = str(address_struct.get('postal_code') or '').strip()
    parts = []
    street_part = " ".join([street, street_no]).strip()
    if street_part:
        parts.append(street_part)
    city_part = " ".join([postal, city]).strip()
    if city_part:
        parts.append(city_part)
    if not parts:
        return None
    return ", ".join(parts)


async def _fetch_user_with_geocode(email: str, cache: Dict[str, Optional[dict]]) -> Optional[dict]:
    key = (email or '').strip().lower()
    if not key:
        return None
    if key in cache:
        return cache[key]
    user = await db_mod.db.users.find_one({'email': email})
    if not user and key != email:
        user = await db_mod.db.users.find_one({'email': key})
    if not user:
        cache[key] = None  # type: ignore[assignment]
        return None
    lat = user.get('lat')
    lon = user.get('lon')
    if geocode_missing_enabled() and (lat is None or lon is None):
        address = _compose_address(user.get('address_struct'))
        if not address:
            raw_addr = user.get('address')
            if isinstance(raw_addr, str) and raw_addr.strip():
                address = raw_addr.strip()
        if address:
            try:
                from ..services.geocoding import geocode_address  # local import to avoid cycles at module load

                coords = await geocode_address(address)
            except Exception:
                coords = None
            if coords:
                lat, lon = coords
                user['lat'] = float(lat)
                user['lon'] = float(lon)
                try:
                    await db_mod.db.users.update_one(
                        {'_id': user['_id']},
                        {'$set': {'lat': float(lat), 'lon': float(lon), 'geocoded_at': datetime.datetime.now(datetime.timezone.utc)}},
                    )
                except Exception:
                    pass
    cache[key] = user
    return user


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
async def start_matching(event_id: str, response: Response, payload: Optional[dict] = None, current_admin=Depends(require_admin)):
    # ensure event exists and is published/open
    ev = await require_event_published(event_id)
    # enforce registration deadline passed if set
    ddl = ev.get('registration_deadline')
    deadline_dt = None
    if ddl:
        if isinstance(ddl, datetime.datetime):
            deadline_dt = ddl if ddl.tzinfo is not None else ddl.replace(tzinfo=datetime.timezone.utc)
        elif isinstance(ddl, str):
            try:
                from app import datetime_utils
                deadline_dt = datetime_utils.parse_iso(ddl)
            except Exception:
                # If parsing fails, be conservative and skip the deadline check
                deadline_dt = None
    now = datetime.datetime.now(datetime.timezone.utc)
    if deadline_dt and now < deadline_dt:
        raise HTTPException(status_code=400, detail='Registration deadline has not passed yet')
    payload = payload or {}
    algorithms: List[str] = payload.get('algorithms') or ['greedy', 'random']
    weights: Dict[str, float] = payload.get('weights') or {}
    dry_run: bool = bool(payload.get('dry_run', False))

    job_info = await enqueue_matching_job(
        event_id,
        algorithms=algorithms,
        weights=weights,
        dry_run=dry_run,
        requested_by=str(current_admin.get('_id')) if current_admin.get('_id') is not None else None,
    )
    job = job_info['job']
    poll_url = f"/matching/{event_id}/jobs/{job['id']}"
    response.status_code = 202 if job_info['was_enqueued'] else 200
    status_label = 'accepted' if job_info['was_enqueued'] else 'already_running'
    return {
        'status': status_label,
        'job_id': job['id'],
        'poll_url': poll_url,
        'job': job,
    }


@router.get('/{event_id}/jobs')
async def list_jobs(event_id: str, limit: int = 10, _=Depends(require_admin)):
    try:
        event_oid = ObjectId(event_id)
    except InvalidId as exc:
        raise HTTPException(status_code=404, detail='Event not found') from exc
    event_exists = await db_mod.db.events.find_one({'_id': event_oid})
    if not event_exists:
        raise HTTPException(status_code=404, detail='Event not found')
    limit = max(1, min(limit, 50))
    jobs = await list_matching_jobs(event_id, limit=limit)
    return jobs


@router.get('/{event_id}/jobs/{job_id}')
async def get_job_status(event_id: str, job_id: str, _=Depends(require_admin)):
    try:
        event_oid = ObjectId(event_id)
    except InvalidId as exc:
        raise HTTPException(status_code=404, detail='Event not found') from exc
    event_exists = await db_mod.db.events.find_one({'_id': event_oid})
    if not event_exists:
        raise HTTPException(status_code=404, detail='Event not found')
    job = await get_matching_job(job_id)
    if not job or job.get('event_id') != event_id:
        raise HTTPException(status_code=404, detail='Job introuvable')
    return job


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
    await _build_synthetic_entries(tmap, teams, groups, m.get('unmatched_units'))
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
        await db_mod.db.matches.update_one({'_id': m['_id']}, {'$set': {'groups': new_groups, 'updated_at': datetime.datetime.now(datetime.timezone.utc)}})
        return {'status': 'moved', 'violations': violations if violations else []}
    return {'status': 'noop', 'reason': 'team_not_guest_or_already_present'}



@router.get('/{event_id}/details')
async def match_details(event_id: str, version: Optional[int] = None, _=Depends(require_admin)):
    """Return enriched details for a match proposal: groups, metrics, and team_details.

    team_details now also includes a payment summary: payment: { status: 'paid'|'partial'|'unpaid'|'n/a', paid_count:int, active_reg_count:int }
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
    # Precollect registration ids for payment lookup & active filtering
    reg_ids: List[ObjectId] = []
    reg_status_cancelled = {'cancelled_by_user','cancelled_admin','refunded','expired'}
    team_active_regs: Dict[str, List[ObjectId]] = {}
    for t in teams:
        tid = str(t['team_id'])
        active_regs: List[ObjectId] = []
        for r in t.get('member_regs') or []:
            rid = r.get('_id')
            if rid is None:
                continue
            reg_ids.append(rid)
            if r.get('status') not in reg_status_cancelled:
                active_regs.append(rid)
        team_active_regs[tid] = active_regs
    payments_by_reg: Dict[str, dict] = {}
    if reg_ids:
        async for p in db_mod.db.payments.find({'registration_id': {'$in': reg_ids}}):
            rid = p.get('registration_id')
            if rid is not None:
                payments_by_reg[str(rid)] = p
    for t in teams:
        tid = str(t['team_id'])
        # Extract emails for solo teams (needed for metrics calculation)
        emails = []
        for r in t.get('member_regs') or []:
            em = r.get('user_email_snapshot')
            if em:
                emails.append(em.lower())
        
        team_map[tid] = {
            'size': t.get('size'),
            'team_diet': t.get('team_diet'),
            'course_preference': t.get('course_preference'),
            'can_host_main': t.get('can_host_main'),
            'can_host_any': t.get('can_host_any'),
            'lat': t.get('lat'),
            'lon': t.get('lon'),
            'allergies': list(t.get('allergies') or []),
            'host_allergies': list(t.get('host_allergies') or []),
            'emails': emails,  # Add emails for synthetic team extraction
        }
        active_ids = team_active_regs.get(tid) or []
        paid_count = 0
        for rid in active_ids:
            pr = payments_by_reg.get(str(rid))
            if pr and pr.get('status') in ('paid', 'succeeded'):
                paid_count += 1
        if not active_ids:
            payment_status = 'n/a'
        elif paid_count == 0:
            payment_status = 'unpaid'
        elif paid_count < len(active_ids):
            payment_status = 'partial'
        else:
            payment_status = 'paid'
        team_map[tid]['payment'] = {
            'status': payment_status,
            'paid_count': paid_count,
            'active_reg_count': len(active_ids),
        }
    # Enrich groups with host public address if missing (best-effort)
    groups_in = m.get('groups') or []
    # Build helper to determine preferred host email based on team_doc/cooking_location
    team_by_id: Dict[str, dict] = { str(t['team_id']): t for t in teams }
    from ..services.matching import _user_address_string as _host_addr  # lazy import to avoid cycle issues
    groups_out: List[dict] = []
    for g in groups_in:
        gg = dict(g)
        if 'host_address_public' not in gg or gg.get('host_address_public') is None:
            try:
                tid = str(gg.get('host_team_id')) if gg.get('host_team_id') is not None else None
                t = team_by_id.get(tid or '') if tid else None
                host_email = _get_host_email(t) if t else None
                addr = await _host_addr(host_email) if host_email else None
                if addr:
                    gg['host_address'] = addr[0]
                    gg['host_address_public'] = addr[1]
            except Exception:
                pass
        groups_out.append(gg)
    # Prepare unmatched metadata for synthetic units that might not appear in groups
    unmatched_units: List[dict] = m.get('unmatched_units') or []
    unmatched_by_id: Dict[str, dict] = {str(entry.get('team_id')): entry for entry in unmatched_units if entry.get('team_id')}

    # Add details for synthetic units (pair:, split:) by finding their original solo teams
    solo_teams_by_email: Dict[str, dict] = {}
    for t in teams:
        tid = str(t['team_id'])
        if tid.startswith('solo:'):
            # Get email from first member or registration
            emails = []
            for r in t.get('member_regs') or []:
                em = r.get('user_email_snapshot')
                if em:
                    emails.append(em.lower())
            if emails:
                solo_teams_by_email[emails[0]] = t
    
    # Now for each pair/split ID in groups, build team_map entry
    all_synthetic_ids = set()
    for g in groups_out:
        for tid in [g.get('host_team_id')] + (g.get('guest_team_ids') or []):
            if tid and isinstance(tid, str) and (tid.startswith('pair:') or tid.startswith('split:')):
                all_synthetic_ids.add(tid)

    for entry in unmatched_units:
        tid = entry.get('team_id')
        if isinstance(tid, str) and (tid.startswith('pair:') or tid.startswith('split:')):
            all_synthetic_ids.add(tid)
    
    for uid in all_synthetic_ids:
        if uid not in team_map:
            # Extract emails from the ID
            if uid.startswith('pair:'):
                part = uid.split(':', 1)[1]
                pair_emails = [e.lower() for e in part.split('+') if e] if '+' in part else ([part.lower()] if part else [])
            elif uid.startswith('split:'):
                email = uid.split(':', 1)[1]
                pair_emails = [email.lower()] if email else []
            else:
                pair_emails = []
            
            # Merge info from solo teams
            diet_list = []
            prefs = []
            can_host_main = False
            can_host_any = False
            allergies_set = set()
            host_allergies_set = set()
            lat_vals = []
            lon_vals = []
            active_reg_ids = []
            
            for em in pair_emails:
                solo_t = solo_teams_by_email.get(em)
                if solo_t:
                    if solo_t.get('team_diet'):
                        diet_list.append(solo_t.get('team_diet'))
                    if solo_t.get('course_preference'):
                        prefs.append(solo_t.get('course_preference'))
                    if solo_t.get('can_host_main'):
                        can_host_main = True
                    if solo_t.get('can_host_any'):
                        can_host_any = True
                    for a in (solo_t.get('allergies') or []):
                        allergies_set.add(a)
                    for a in (solo_t.get('host_allergies') or []):
                        host_allergies_set.add(a)
                    if solo_t.get('lat') is not None:
                        lat_vals.append(solo_t.get('lat'))
                    if solo_t.get('lon') is not None:
                        lon_vals.append(solo_t.get('lon'))
                    # Collect active registration IDs
                    solo_tid = str(solo_t.get('team_id', ''))
                    active_reg_ids.extend(team_active_regs.get(solo_tid, []))
            
            # Merge diet (prioritize restrictive)
            team_diet = 'omnivore'
            if 'vegan' in diet_list:
                team_diet = 'vegan'
            elif 'vegetarian' in diet_list:
                team_diet = 'vegetarian'
            elif diet_list:
                team_diet = diet_list[0]
            
            # Calculate payment status
            paid_count = 0
            for rid in active_reg_ids:
                pr = payments_by_reg.get(str(rid))
                if pr and pr.get('status') in ('paid', 'succeeded'):
                    paid_count += 1
            
            if not active_reg_ids:
                payment_status = 'n/a'
            elif paid_count == 0:
                payment_status = 'unpaid'
            elif paid_count < len(active_reg_ids):
                payment_status = 'partial'
            else:
                payment_status = 'paid'
            
            base_size = len(pair_emails)
            unmatched_meta = unmatched_by_id.get(uid) or {}
            if isinstance(unmatched_meta.get('size'), int) and unmatched_meta.get('size') > 0:
                base_size = int(unmatched_meta.get('size'))

            team_map[uid] = {
                'size': base_size if base_size > 0 else max(len(pair_emails), 1),
                'team_diet': team_diet,
                'course_preference': prefs[0] if prefs else None,
                'can_host_main': can_host_main,
                'can_host_any': can_host_any,
                'lat': lat_vals[0] if lat_vals else None,
                'lon': lon_vals[0] if lon_vals else None,
                'allergies': sorted(list(allergies_set)),
                'host_allergies': sorted(list(host_allergies_set)),
                'emails': pair_emails,  # Add emails for synthetic team extraction
                'payment': {
                    'status': payment_status,
                    'paid_count': paid_count,
                    'active_reg_count': len(active_reg_ids),
                },
            }
    
    # Attach members (names) using team->emails mapping
    base_emails_map = await _team_emails_map(event_id)
    # Augment with split: and pair: IDs from groups
    from ..services.matching.operations import _augment_emails_map_with_splits
    emails_map = _augment_emails_map_with_splits(base_emails_map, groups_out)
    # Ensure unmatched synthetic units also have email entries so UI can render them
    for uid in all_synthetic_ids:
        if uid not in emails_map:
            if uid.startswith('split:'):
                email = uid.split(':', 1)[1]
                emails_map[uid] = [email] if email else []
            elif uid.startswith('pair:'):
                segment = uid.split(':', 1)[1] if ':' in uid else ''
                emails = [e for e in segment.split('+') if e]
                emails_map[uid] = emails
    # Gather all emails to bulk fetch names (normalize to lowercase)
    all_emails = set()
    for ems in emails_map.values():
        for em in ems:
            if em:
                all_emails.add(em.lower())
    users_by_email: Dict[str, dict] = {}
    if all_emails:
        async for u in db_mod.db.users.find({'email': {'$in': list(all_emails)}}):
            email_lower = (u.get('email') or '').lower()
            if email_lower:
                users_by_email[email_lower] = u
    for tid, ems in emails_map.items():
        members = []
        # Deduplicate emails while preserving order
        seen_emails = set()
        unique_emails = []
        for em in ems:
            if em:
                em_lower = em.lower()
                if em_lower not in seen_emails:
                    seen_emails.add(em_lower)
                    unique_emails.append(em)
        
        for em in unique_emails:
            em_lower = em.lower()
            u = users_by_email.get(em_lower) or {}
            fn = (u.get('first_name') or u.get('firstname') or '').strip()
            ln = (u.get('last_name') or u.get('lastname') or '').strip()
            # Use local part of email as fallback instead of full email for cleaner display
            if fn or ln:
                disp = f"{fn} {ln}".strip()
            else:
                disp = em.split('@')[0] if '@' in em else em
            members.append({'email': em, 'first_name': fn or None, 'last_name': ln or None, 'display_name': disp})
        team_map.setdefault(tid, {})['members'] = members
    # Compose output
    out = {
        'version': m.get('version'),
        'metrics': m.get('metrics') or {},
        'algorithm': m.get('algorithm') or 'unknown',
        'groups': groups_out,
        'team_details': team_map,
        'unmatched_units': m.get('unmatched_units') or [],
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
    tmap = _build_team_map_with_emails(teams)
    await _build_synthetic_entries(tmap, teams, groups_in)
    groups = m.get('groups') or []
    new_groups: List[dict] = []
    # helper for host address
    from ..services.matching import _user_address_string as _host_addr
    for g in groups:
        phase = g.get('phase')
        host_id = str(g.get('host_team_id'))
        guest_ids = [str(x) for x in (g.get('guest_team_ids') or [])]
        host = tmap.get(host_id, {})
        guests = [tmap.get(tid, {}) for tid in guest_ids]
        base_score, warns, allergy_details = _score_group_phase(host, guests, phase, {})
        travel = await _travel_time_for_phase(host, guests)
        if (not travel) and isinstance(g.get('travel_seconds'), (int, float)):
            travel = float(g.get('travel_seconds'))
        host_email = _get_host_email(host)
        addr_full = addr_pub = None
        if host_email:
            try:
                addr = await _host_addr(host_email)
                if addr:
                    addr_full, addr_pub = addr
            except Exception:
                pass
        new_groups.append({
            **g,
            'score': base_score - 1.0 * (travel or 0.0),  # default W_DIST=1
            'travel_seconds': travel,
            'warnings': warns,
            'host_address': addr_full if addr_full is not None else g.get('host_address'),
            'host_address_public': addr_pub if addr_pub is not None else g.get('host_address_public'),
            'host_allergies': allergy_details.get('host_allergies', []),
            'guest_allergies': allergy_details.get('guest_allergies', {}),
            'guest_allergies_union': allergy_details.get('guest_allergies_union', []),
            'uncovered_allergies': allergy_details.get('uncovered_allergies', []),
        })
    metrics = _compute_metrics(new_groups, {}, team_details=tmap)
    await db_mod.db.matches.update_one({'_id': m['_id']}, {'$set': {'groups': new_groups, 'metrics': metrics, 'updated_at': datetime.datetime.now(datetime.timezone.utc)}})
    return {'version': m.get('version'), 'metrics': metrics}


@router.post('/{event_id}/preview')
async def preview_groups(event_id: str, payload: dict, _=Depends(require_admin)):
    """Compute metrics and annotate provided groups without persisting.

    Payload: { groups: [ { phase, host_team_id, guest_team_ids }... ] }
    Returns: { groups: [ with score, travel_seconds, warnings ], metrics: {..} }
    """
    groups_in = payload.get('groups') or []
    # Load event and build team map (lat/lon, capabilities)
    ev = await db_mod.db.events.find_one({'_id': ObjectId(event_id)})
    if not ev:
        raise HTTPException(status_code=404, detail='Event not found')
    teams = await _build_teams(ev['_id'])
    tmap = _build_team_map_with_emails(teams)
    # helper for host address
    from ..services.matching import _user_address_string as _host_addr
    new_groups: List[dict] = []
    for g in groups_in:
        phase = g.get('phase')
        host_id = str(g.get('host_team_id')) if g.get('host_team_id') is not None else None
        guest_ids = [str(x) for x in (g.get('guest_team_ids') or [])]
        host = tmap.get(host_id, {}) if host_id else {}
        guests = [tmap.get(tid, {}) for tid in guest_ids]
        base_score, warns, allergy_details = _score_group_phase(host, guests, phase, {})
        travel = await _travel_time_for_phase(host, guests)
        if (not travel) and isinstance(g.get('travel_seconds'), (int, float)):
            travel = float(g.get('travel_seconds'))
        host_email = _get_host_email(host)
        addr_full = addr_pub = None
        if host_email:
            try:
                addr = await _host_addr(host_email)
                if addr:
                    addr_full, addr_pub = addr
            except Exception:
                pass
        new_groups.append({
            **g,
            'score': base_score - 1.0 * (travel or 0.0),  # W_DIST defaults to 1 in recompute
            'travel_seconds': travel,
            'warnings': warns,
            'host_address': addr_full if addr_full is not None else g.get('host_address'),
            'host_address_public': addr_pub if addr_pub is not None else g.get('host_address_public'),
            'host_allergies': allergy_details.get('host_allergies', []),
            'guest_allergies': allergy_details.get('guest_allergies', {}),
            'guest_allergies_union': allergy_details.get('guest_allergies_union', []),
            'uncovered_allergies': allergy_details.get('uncovered_allergies', []),
        })
    metrics = _compute_metrics(new_groups, {}, team_details=tmap)
    return { 'groups': new_groups, 'metrics': metrics }


# ---------- Constraints management (admin) ----------
from pydantic import BaseModel, EmailStr

class PairIn(BaseModel):
    a_email: EmailStr
    b_email: EmailStr

class SplitIn(BaseModel):
    team_id: str


def _norm_email(e: str) -> str:
    return (e or '').strip().lower()


def _get_host_email(host: dict) -> Optional[str]:
    """Extract host email from team data based on cooking location."""
    host_email = None
    try:
        team_doc = host.get('team_doc') or {}
        members = team_doc.get('members') or []
        cooking_loc = (host.get('cooking_location') or 'creator')
        if members:
            if cooking_loc == 'creator':
                host_email = (members[0] or {}).get('email')
            elif len(members) > 1:
                host_email = (members[1] or {}).get('email')
        if not host_email and members:
            host_email = (members[0] or {}).get('email')
    except Exception:
        host_email = None
    return host_email


async def _build_synthetic_entries(
    team_map: Dict[str, dict],
    teams: List[dict],
    groups: List[dict],
    unmatched_units: Optional[List[dict]] = None,
) -> None:
    """Ensure synthetic (pair:, split:) units used in groups have basic team entries.

    Provides enough data (lat/lon, dietary info, emails) so travel/score calculations
    remain stable during preview/recompute flows.
    """
    if not groups:
        return
    needed_ids: Set[str] = set()
    for g in groups:
        if not isinstance(g, dict):
            continue
        ids = []
        host_id = g.get('host_team_id')
        if host_id is not None:
            ids.append(str(host_id))
        ids.extend([str(tid) for tid in (g.get('guest_team_ids') or []) if tid is not None])
        for tid in ids:
            if not isinstance(tid, str):
                continue
            if tid in team_map:
                continue
            if tid.startswith('pair:') or tid.startswith('split:'):
                needed_ids.add(tid)
    if unmatched_units:
        for entry in unmatched_units:
            tid = entry.get('team_id')
            if isinstance(tid, str) and tid not in team_map and (tid.startswith('pair:') or tid.startswith('split:')):
                needed_ids.add(tid)
    if not needed_ids:
        return

    solo_by_email: Dict[str, dict] = {}
    for entry in teams:
        tid = str(entry.get('team_id'))
        if not tid.startswith('solo:'):
            continue
        raw_emails: List[str] = []
        for reg in entry.get('member_regs') or []:
            em = reg.get('user_email_snapshot')
            if em:
                raw_emails.append(str(em).strip().lower())
        if not raw_emails:
            team_doc = entry.get('team_doc') or {}
            for member in team_doc.get('members') or []:
                em = member.get('email')
                if em:
                    raw_emails.append(str(em).strip().lower())
        for em in raw_emails:
            if not em:
                continue
            solo_by_email.setdefault(em, entry)

    unmatched_lookup: Dict[str, dict] = {}
    if unmatched_units:
        unmatched_lookup = {
            str(entry.get('team_id')): entry
            for entry in unmatched_units
            if isinstance(entry.get('team_id'), str)
        }

    user_cache: Dict[str, Optional[dict]] = {}

    for sid in needed_ids:
        if sid in team_map:
            continue
        if sid.startswith('pair:'):
            payload = sid.split(':', 1)[1] if ':' in sid else ''
            emails_raw = [p for p in payload.split('+') if p]
        elif sid.startswith('split:'):
            segment = sid.split(':', 1)[1] if ':' in sid else ''
            emails_raw = [segment] if segment else []
        else:
            emails_raw = []
        normalized_emails = [e.strip().lower() for e in emails_raw if e]

        lat_values: List[float] = []
        lon_values: List[float] = []
        diet_candidates: List[str] = []
        course_candidates: List[str] = []
        host_allergies: Set[str] = set()
        allergies: Set[str] = set()
        can_host_main = False
        can_host_any = False
        for em in normalized_emails:
            solo_entry = solo_by_email.get(em)
            if solo_entry:
                if solo_entry.get('team_diet'):
                    diet_candidates.append(str(solo_entry.get('team_diet')))
                if solo_entry.get('course_preference'):
                    course_candidates.append(str(solo_entry.get('course_preference')))
                if solo_entry.get('can_host_main'):
                    can_host_main = True
                if solo_entry.get('can_host_any'):
                    can_host_any = True
                for item in solo_entry.get('host_allergies') or []:
                    if item is not None:
                        host_allergies.add(str(item))
                for item in solo_entry.get('allergies') or []:
                    if item is not None:
                        allergies.add(str(item))
        for em in normalized_emails:
            user = await _fetch_user_with_geocode(em, user_cache)
            if not user:
                continue
            lat = user.get('lat')
            lon = user.get('lon')
            if isinstance(lat, (int, float)):
                lat_values.append(float(lat))
            if isinstance(lon, (int, float)):
                lon_values.append(float(lon))
            prefs = user.get('preferences') or {}
            if prefs.get('main_course_possible') is True:
                can_host_main = True
            if prefs.get('kitchen_available') is True:
                can_host_any = True
            for key in ('allergies', 'host_allergies'):
                values = user.get(key)
                if not isinstance(values, list):
                    continue
                target = host_allergies if key == 'host_allergies' else allergies
                for item in values:
                    if item is not None:
                        target.add(str(item))

        if 'vegan' in [d.lower() for d in diet_candidates]:
            team_diet = 'vegan'
        elif 'vegetarian' in [d.lower() for d in diet_candidates]:
            team_diet = 'vegetarian'
        elif diet_candidates:
            team_diet = diet_candidates[0]
        else:
            team_diet = 'omnivore'

        course_preference = course_candidates[0] if course_candidates else None
        lat = sum(lat_values) / len(lat_values) if lat_values else None
        lon = sum(lon_values) / len(lon_values) if lon_values else None
        emails_original = [e for e in emails_raw if e]
        unmatched_meta = unmatched_lookup.get(sid) or {}
        size = unmatched_meta.get('size') if isinstance(unmatched_meta.get('size'), int) and unmatched_meta.get('size') > 0 else (len(emails_original) or 1)

        team_map[sid] = {
            'team_id': sid,
            'size': size,
            'team_diet': team_diet,
            'course_preference': course_preference,
            'can_host_main': can_host_main,
            'can_host_any': can_host_any,
            'lat': lat,
            'lon': lon,
            'allergies': sorted(allergies),
            'host_allergies': sorted(host_allergies),
            'emails': [e.lower() for e in emails_original],
            'member_regs': [],
            'team_doc': {'members': [{'email': e} for e in emails_original]},
            'cooking_location': 'creator',
        }


def _build_team_map_with_emails(teams: List[dict]) -> Dict[str, dict]:
    """Create a team_id -> team entry map including normalized email lists for metrics."""
    team_map: Dict[str, dict] = {}
    for team in teams:
        team_id = team.get('team_id')
        if team_id is None:
            continue
        entry = dict(team)
        emails: List[str] = []
        seen: Set[str] = set()

        members = (entry.get('team_doc') or {}).get('members') or []
        for member in members:
            email = (member or {}).get('email')
            if not email:
                continue
            lowered = str(email).strip().lower()
            if not lowered or lowered in seen:
                continue
            seen.add(lowered)
            emails.append(lowered)

        for registration in entry.get('member_regs') or []:
            email = (registration or {}).get('user_email_snapshot')
            if not email:
                continue
            lowered = str(email).strip().lower()
            if not lowered or lowered in seen:
                continue
            seen.add(lowered)
            emails.append(lowered)

        entry['emails'] = emails
        team_map[str(team_id)] = entry
    return team_map


@router.post('/{event_id}/constraints/pair')
async def add_forced_pair(event_id: str, payload: 'PairIn', _=Depends(require_admin)):
    await require_event_published(event_id)
    a = _norm_email(str(payload.a_email)); b = _norm_email(str(payload.b_email))
    if a == b:
        raise HTTPException(status_code=400, detail='emails must differ')
    # store ordered pair (sorted) to dedupe easily
    x, y = sorted([a, b])
    doc = await db_mod.db.matching_constraints.find_one({'event_id': event_id})
    if not doc:
        doc = {'event_id': event_id, 'forced_pairs': [], 'split_team_ids': []}
        await db_mod.db.matching_constraints.insert_one(doc)
    # ensure not already present
    pairs = [p for p in (doc.get('forced_pairs') or []) if isinstance(p, dict)]
    if not any({p.get('a_email'), p.get('b_email')} == {x, y} for p in pairs):
        pairs.append({'a_email': x, 'b_email': y})
        await db_mod.db.matching_constraints.update_one({'event_id': event_id}, {'$set': {'forced_pairs': pairs}})
    return {'forced_pairs': pairs, 'split_team_ids': doc.get('split_team_ids') or []}


@router.delete('/{event_id}/constraints/pair')
async def remove_forced_pair(event_id: str, payload: 'PairIn', _=Depends(require_admin)):
    await require_event_published(event_id)
    a = _norm_email(str(payload.a_email)); b = _norm_email(str(payload.b_email))
    x, y = sorted([a, b])
    doc = await db_mod.db.matching_constraints.find_one({'event_id': event_id})
    if not doc:
        return {'forced_pairs': [], 'split_team_ids': []}
    pairs = [p for p in (doc.get('forced_pairs') or []) if isinstance(p, dict)]
    new_pairs = [p for p in pairs if {p.get('a_email'), p.get('b_email')} != {x, y}]
    if len(new_pairs) != len(pairs):
        await db_mod.db.matching_constraints.update_one({'event_id': event_id}, {'$set': {'forced_pairs': new_pairs}})
    return {'forced_pairs': new_pairs, 'split_team_ids': doc.get('split_team_ids') or []}


@router.post('/{event_id}/constraints/split')
async def add_split(event_id: str, payload: SplitIn, _=Depends(require_admin)):
    await require_event_published(event_id)
    tid = str(payload.team_id)
    doc = await db_mod.db.matching_constraints.find_one({'event_id': event_id})
    if not doc:
        doc = {'event_id': event_id, 'forced_pairs': [], 'split_team_ids': []}
        await db_mod.db.matching_constraints.insert_one(doc)
    ids = [str(x) for x in (doc.get('split_team_ids') or [])]
    if tid not in ids:
        ids.append(tid)
        await db_mod.db.matching_constraints.update_one({'event_id': event_id}, {'$set': {'split_team_ids': ids}})
    return {'forced_pairs': doc.get('forced_pairs') or [], 'split_team_ids': ids}


@router.delete('/{event_id}/constraints/split')
async def remove_split(event_id: str, payload: SplitIn, _=Depends(require_admin)):
    await require_event_published(event_id)
    tid = str(payload.team_id)
    doc = await db_mod.db.matching_constraints.find_one({'event_id': event_id})
    if not doc:
        return {'forced_pairs': [], 'split_team_ids': []}
    ids = [str(x) for x in (doc.get('split_team_ids') or [])]
    new_ids = [x for x in ids if x != tid]
    if len(new_ids) != len(ids):
        await db_mod.db.matching_constraints.update_one({'event_id': event_id}, {'$set': {'split_team_ids': new_ids}})
    return {'forced_pairs': doc.get('forced_pairs') or [], 'split_team_ids': new_ids}


@router.get('/{event_id}/units')
async def list_units(event_id: str, _=Depends(require_admin)):
    await require_event_published(event_id)
    ev = await db_mod.db.events.find_one({'_id': ObjectId(event_id)})
    if not ev:
        raise HTTPException(status_code=404, detail='Event not found')
    teams = await _build_teams(ev['_id'])
    # map team_id -> emails
    email_map = await _team_emails_map(event_id)
    solos: List[dict] = []
    duos: List[dict] = []
    # build user names by email
    all_emails: Set[str] = set()
    for ems in email_map.values():
        for em in ems:
            all_emails.add(em)
    users_by_email: Dict[str, dict] = {}
    if all_emails:
        async for u in db_mod.db.users.find({'email': {'$in': list(all_emails)}}):
            users_by_email[u.get('email')] = u
    def _name_for(em: str) -> str:
        u = users_by_email.get(em) or {}
        fn = (u.get('first_name') or u.get('firstname') or '').strip()
        ln = (u.get('last_name') or u.get('lastname') or '').strip()
        return (f"{fn} {ln}" if (fn or ln) else em).strip()
    for t in teams:
        tid = str(t['team_id'])
        ems = email_map.get(tid, [])
        if int(t.get('size') or 1) >= 2:
            duos.append({'team_id': tid, 'emails': ems, 'names': [_name_for(e) for e in ems]})
        else:
            # solo units are represented with their pseudo team_id already
            solos.append({'unit_id': tid, 'email': ems[0] if ems else None, 'name': _name_for(ems[0]) if ems else None})
    return {'solos': solos, 'duos': duos}


@router.get('/{event_id}/paths')
async def get_paths(event_id: str, version: Optional[int] = None, ids: Optional[str] = None, fast: Optional[int] = 1, _=Depends(require_admin)):
    # removed strict require_event_published to allow admin to view draft events as well
    idset: Optional[Set[str]] = None
    if ids:
        idset = set([s.strip() for s in ids.split(',') if s.strip()])
    fast_mode = (fast is None) or (int(fast) != 0)
    data = await compute_team_paths(event_id, version, idset, fast=fast_mode)
    return data


@router.get('/{event_id}/paths/geometry')
async def get_paths_geometry(event_id: str, version: Optional[int] = None, ids: Optional[str] = None, engine: Optional[str] = None, _=Depends(require_admin)):
    """Return OSRM geometry polylines for legs between phases for the requested teams when possible.

    Falls back to straight lines if the routing engine is not available or fails.
    """
    # removed strict require_event_published to allow admin to view draft events as well
    idset: Optional[Set[str]] = None
    if ids:
        idset = set([s.strip() for s in ids.split(',') if s.strip()])
    # compute points first (fast mode doesn't affect geometry extraction)
    data = await compute_team_paths(event_id, version, idset, fast=True)
    team_geoms: Dict[str, Dict[str, Any]] = {}
    for tid, rec in (data.get('team_paths') or {}).items():
        pts = rec.get('points') or []
        segments = []
        for i in range(len(pts)-1):
            a = pts[i]; b = pts[i+1]
            if a.get('lat') is None or a.get('lon') is None or b.get('lat') is None or b.get('lon') is None:
                continue
            coords = [(float(a['lat']), float(a['lon'])), (float(b['lat']), float(b['lon']))]
            geom = await route_polyline(coords)
            if geom:
                segments.append(geom)
            else:
                # fallback straight line
                segments.append([[a['lat'], a['lon']], [b['lat'], b['lon']]])
        team_geoms[tid] = {'segments': segments}
    return {'team_geometries': team_geoms, 'bounds': data.get('bounds')}


@router.delete('/{event_id}/matches')
async def delete_matches(event_id: str, version: Optional[int] = None, _=Depends(require_admin)):
    """Delete match proposals for an event.

    - If `version` is provided, delete only that version.
    - Otherwise, delete all proposals for the event.
    Updates the event.matching_status to 'not_started' if all are deleted.
    """
    await require_event_published(event_id)
    now = datetime.datetime.utcnow()
    if version is not None:
        m = await db_mod.db.matches.find_one({'event_id': event_id, 'version': int(version)})
        if not m:
            raise HTTPException(status_code=404, detail='Match version not found')
        # Delete the match document
        res = await db_mod.db.matches.delete_one({'_id': m['_id']})

        # Delete any plans generated from this version (plans are tagged with match_version)
        try:
            plans_res = await db_mod.db.plans.delete_many({'event_id': ObjectId(event_id), 'match_version': int(version)})
            plans_deleted = plans_res.deleted_count
        except Exception:
            plans_deleted = 0

        # Find matching_jobs that reference this version in their proposals and remove them.
        job_ids = []
        async for jd in db_mod.db.matching_jobs.find({'event_id': event_id, 'proposals.version': int(version)}):
            job_ids.append(jd.get('_id'))

        # Cancel any in-memory active tasks for those job ids
        try:
            for jid in list(job_ids):
                task = _matching_jobs_module._ACTIVE_JOBS.get(jid)
                if task is not None and not task.done():
                    try:
                        task.cancel()
                    except Exception:
                        pass
            if job_ids:
                await db_mod.db.matching_jobs.delete_many({'_id': {'$in': job_ids}})
            jobs_deleted = len(job_ids)
        except Exception:
            jobs_deleted = 0

        return {'deleted_matches': res.deleted_count, 'deleted_jobs': jobs_deleted, 'deleted_plans': plans_deleted, 'version': int(version)}
    # delete all
    # delete all match docs for event
    res = await db_mod.db.matches.delete_many({'event_id': event_id})
    # delete all plans for event
    try:
        plans_res = await db_mod.db.plans.delete_many({'event_id': ObjectId(event_id)})
        plans_deleted = plans_res.deleted_count
    except Exception:
        plans_deleted = 0

    # find all job ids for this event, cancel in-memory, and remove job docs
    job_ids = []
    async for jd in db_mod.db.matching_jobs.find({'event_id': event_id}):
        job_ids.append(jd.get('_id'))
    try:
        for jid in list(job_ids):
            task = _matching_jobs_module._ACTIVE_JOBS.get(jid)
            if task is not None and not task.done():
                try:
                    task.cancel()
                except Exception:
                    pass
        if job_ids:
            jobs_del_res = await db_mod.db.matching_jobs.delete_many({'_id': {'$in': job_ids}})
            jobs_deleted = jobs_del_res.deleted_count
        else:
            jobs_deleted = 0
    except Exception:
        jobs_deleted = 0

    # set event.matching_status back to not_started
    await db_mod.db.events.update_one({'_id': ObjectId(event_id)}, {'$set': {'matching_status': 'not_started', 'updated_at': now}})
    return {'deleted_matches': res.deleted_count, 'deleted_jobs': jobs_deleted, 'deleted_plans': plans_deleted}


@router.post('/{event_id}/unrelease')
async def unrelease_match(event_id: str, version: int, current_admin=Depends(require_admin)):
    """Less-destructive undo of a release: remove generated plans for the given match version
    and revert the match document's finalized metadata while keeping the proposal itself.

    - Deletes plans tagged with match_version == version for this event.
    - Sets match.status back to 'proposed' and unsets finalized_by/finalized_at.
    - Updates event.matching_status based on remaining matches.
    """
    await require_event_published(event_id)
    try:
        event_oid = ObjectId(event_id)
    except Exception:
        raise HTTPException(status_code=404, detail='Event not found')
    m = await db_mod.db.matches.find_one({'event_id': event_id, 'version': int(version)})
    if not m:
        raise HTTPException(status_code=404, detail='Match version not found')
    # Only operate if currently finalized (safety)
    if (m.get('status') or '').lower() != 'finalized':
        raise HTTPException(status_code=400, detail='Match version is not finalized')

    # Delete associated plans for this version (plans have match_version)
    try:
        plans_res = await db_mod.db.plans.delete_many({'event_id': event_oid, 'match_version': int(version)})
        plans_deleted = plans_res.deleted_count
    except Exception:
        plans_deleted = 0

    # Revert match metadata: set status to proposed and remove finalized fields
    now = datetime.datetime.utcnow()
    try:
        await db_mod.db.matches.update_one({'_id': m['_id']}, {'$set': {'status': 'proposed', 'updated_at': now}, '$unset': {'finalized_by': '', 'finalized_at': ''}})
    except Exception:
        pass

    # Recompute event.matching_status: if any finalized remain -> finalized, elif any proposals -> proposed, else not_started
    any_finalized = await db_mod.db.matches.find_one({'event_id': event_id, 'status': 'finalized'})
    if any_finalized:
        new_status = 'finalized'
    else:
        any_match = await db_mod.db.matches.find_one({'event_id': event_id})
        new_status = 'proposed' if any_match else 'not_started'
    try:
        await db_mod.db.events.update_one({'_id': event_oid}, {'$set': {'matching_status': new_status, 'updated_at': now}})
    except Exception:
        pass

    return {'status': 'unreleased', 'plans_deleted': plans_deleted, 'version': int(version)}


@router.post('/{event_id}/validate')
async def validate_groups(event_id: str, payload: dict, _=Depends(require_admin)):
    """Validate a set of groups for duplicate pairs and basic structural issues.

    Payload: { groups: [ { phase, host_team_id, guest_team_ids }... ] }
    Returns: { violations: [...], phase_issues: [...], group_issues: [...] }
    """
    await require_event_published(event_id)
    groups = payload.get('groups') or []
    # duplicate pair counts
    pair_counts = _collect_pairs(groups)
    violations = [ {'pair': list(pk), 'count': c} for pk, c in pair_counts.items() if c > 1 ]
    # phase-level: team appears more than once in same phase
    phase_seen: Dict[str, Set[str]] = {}
    phase_issues: List[dict] = []
    for g in groups:
        phase = str(g.get('phase'))
        phase_seen.setdefault(phase, set())
        ids = []
        if g.get('host_team_id') is not None:
            ids.append(str(g['host_team_id']))
        ids.extend([str(x) for x in (g.get('guest_team_ids') or [])])
        for tid in ids:
            key = f"{phase}:{tid}"
            if tid in phase_seen[phase]:
                phase_issues.append({'phase': phase, 'team_id': tid, 'issue': 'duplicate_in_phase'})
            phase_seen[phase].add(tid)
    # group-level structural issues
    group_issues: List[dict] = []
    by_phase: Dict[str, List[dict]] = {}
    for g in groups:
        p = str(g.get('phase'))
        by_phase.setdefault(p, []).append(g)
    for p, lst in by_phase.items():
        for idx, g in enumerate(lst):
            host = g.get('host_team_id')
            guests = g.get('guest_team_ids') or []
            if host is None:
                group_issues.append({'phase': p, 'group_idx': idx, 'issue': 'missing_host'})
            if len(guests) != 2:
                group_issues.append({'phase': p, 'group_idx': idx, 'issue': f'invalid_guest_count:{len(guests)}'})
            # prevent host duplicated as guest
            if host is not None and any(str(x) == str(host) for x in guests):
                group_issues.append({'phase': p, 'group_idx': idx, 'issue': 'host_in_guests'})
    return {'violations': violations, 'phase_issues': phase_issues, 'group_issues': group_issues}


@router.post('/{event_id}/set_groups')
async def set_groups(event_id: str, payload: dict, _=Depends(require_admin)):
    """Persist edited groups for a given match version.

    Payload: { version:int, groups:[...], force?:bool }
    - Validates duplicates and structural issues; if any and not force, returns status=warning with details.
    - On success, updates groups, recomputes travel/score/warnings and aggregate metrics, and returns status=saved.
    """
    await require_event_published(event_id)
    version = int(payload.get('version'))
    groups_in = payload.get('groups') or []
    force = bool(payload.get('force', False))
    m = await db_mod.db.matches.find_one({'event_id': event_id, 'version': version})
    if not m:
        raise HTTPException(status_code=404, detail='Match version not found')
    # validate
    v = await validate_groups(event_id, {'groups': groups_in})
    if (v['violations'] or v['phase_issues'] or v['group_issues']) and not force:
        return { 'status': 'warning', **v }
    # Recompute per-group metrics using current team attributes
    ev = await db_mod.db.events.find_one({'_id': ObjectId(event_id)})
    if not ev:
        raise HTTPException(status_code=404, detail='Event not found')
    teams = await _build_teams(ev['_id'])
    tmap = _build_team_map_with_emails(teams)
    # helper for host public address
    from ..services.matching import _user_address_string as _host_addr
    new_groups: List[dict] = []
    for g in groups_in:
        phase = g.get('phase')
        host_id = str(g.get('host_team_id')) if g.get('host_team_id') is not None else None
        guest_ids = [str(x) for x in (g.get('guest_team_ids') or [])]
        host = tmap.get(host_id, {}) if host_id else {}
        guests = [tmap.get(tid, {}) for tid in guest_ids]
        base_score, warns, allergy_details = _score_group_phase(host, guests, phase, {})
        travel = await _travel_time_for_phase(host, guests)
        host_email = _get_host_email(host)
        addr_full = addr_pub = None
        if host_email:
            try:
                addr = await _host_addr(host_email)
                if addr:
                    addr_full, addr_pub = addr
            except Exception:
                pass
        new_groups.append({
            'phase': phase,
            'host_team_id': host_id,
            'guest_team_ids': guest_ids,
            'score': base_score - 1.0 * (travel or 0.0),
            'travel_seconds': travel,
            'warnings': warns,
            'host_address': addr_full,
            'host_address_public': addr_pub,
            'host_allergies': allergy_details.get('host_allergies', []),
            'guest_allergies': allergy_details.get('guest_allergies', {}),
            'guest_allergies_union': allergy_details.get('guest_allergies_union', []),
            'uncovered_allergies': allergy_details.get('uncovered_allergies', []),
        })
    metrics = _compute_metrics(new_groups, {}, team_details=tmap)
    await db_mod.db.matches.update_one({'_id': m['_id']}, {'$set': {'groups': new_groups, 'metrics': metrics, 'updated_at': datetime.datetime.utcnow()}})
    return { 'status': 'saved', 'version': version, 'metrics': metrics }


@router.post('/{event_id}/optimize')
async def optimize_existing_match(
    event_id: str,
    payload: Optional[dict] = None,
    _=Depends(require_admin)
):
    """
    Manually trigger optimization for an existing match version.
    Attempts to improve the match by recreating auto-paired teams.
    
    Payload: {
        version?: int,  # Match version to optimize (default: latest)
        weights?: dict, # Custom scoring weights
        max_attempts?: int,  # Maximum optimization attempts (default: 3)
        parallel?: bool  # Run attempts in parallel for speed (default: true)
    }
    """
    await require_event_published(event_id)
    payload = payload or {}
    
    # Find the match to optimize
    version = payload.get('version')
    query: Dict[str, Any] = {'event_id': event_id}
    if version is not None:
        query['version'] = int(version)
    
    match_doc = await db_mod.db.matches.find_one(query, sort=[('version', -1)])
    if not match_doc:
        raise HTTPException(status_code=404, detail='No match found to optimize')
    
    # Extract event ObjectId
    try:
        event_oid = ObjectId(event_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail='Invalid event ID')
    
    # Prepare the result dict for optimization
    initial_result = {
        'algorithm': match_doc.get('algorithm', 'unknown'),
        'groups': match_doc.get('groups', []),
        'metrics': match_doc.get('metrics', {}),
        'unmatched_units': match_doc.get('unmatched_units', []),
    }
    
    # Get weights from payload or use defaults
    weights = payload.get('weights') or {}
    max_attempts = int(payload.get('max_attempts', 3))
    max_attempts = max(1, min(10, max_attempts))  # Clamp between 1 and 10
    
    # Get parallel mode (default: true for speed)
    parallel = payload.get('parallel', True)
    
    # Run optimization
    optimized_result = await optimize_match_result(
        event_oid,
        initial_result,
        weights,
        max_attempts=max_attempts,
        parallel=parallel,
    )
    
    # Check if optimization improved the result
    improved = optimized_result != initial_result
    
    if improved:
        # Save optimized result as a new version
        from ..services.matching import persist_match_proposal
        new_match = await persist_match_proposal(event_id, optimized_result)
        
        return {
            'status': 'optimized',
            'improved': True,
            'mode': 'parallel' if parallel else 'sequential',
            'original_version': match_doc.get('version'),
            'new_version': new_match.get('version'),
            'new_match_id': new_match.get('id'),
            'original_metrics': initial_result.get('metrics', {}),
            'optimized_metrics': optimized_result.get('metrics', {}),
        }
    else:
        return {
            'status': 'no_improvement',
            'improved': False,
            'mode': 'parallel' if parallel else 'sequential',
            'version': match_doc.get('version'),
            'metrics': initial_result.get('metrics', {}),
        }

