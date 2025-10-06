from fastapi import APIRouter, HTTPException, Depends
from .. import db as db_mod
from bson.objectid import ObjectId
from ..auth import require_admin
from ..utils import require_event_published
from typing import Optional, List, Dict, Any, Tuple, Set
from ..services.matching import run_algorithms, persist_match_proposal, mark_finalized, list_issues, refunds_overview, finalize_and_generate_plans, _build_teams, _score_group_phase, _travel_time_for_phase, _compute_metrics, _team_emails_map
from ..services.matching import compute_team_paths  # new for travel map
from ..services.routing import route_polyline  # use OSRM real route geometry
from ..services.matching import process_refunds  # ajout pour refunds processing
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
    now = datetime.datetime.now(datetime.timezone.utc)
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
        await db_mod.db.events.update_one({'_id': ObjectId(event_id)}, {'$set': {'matching_status': 'proposed', 'updated_at': datetime.datetime.now(datetime.timezone.utc)}})
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
        await db_mod.db.matches.update_one({'_id': m['_id']}, {'$set': {'groups': new_groups, 'updated_at': datetime.datetime.now(datetime.timezone.utc)}})
        return {'status': 'moved', 'violations': violations if violations else []}
    return {'status': 'noop', 'reason': 'team_not_guest_or_already_present'}


@router.get('/{event_id}/refunds')
async def refunds(event_id: str, _=Depends(require_admin)):
    await require_event_published(event_id)
    return await refunds_overview(event_id)


@router.post('/{event_id}/refunds/process')
async def process_refunds_endpoint(event_id: str, payload: dict | None = None, _=Depends(require_admin)):
    await require_event_published(event_id)
    registration_ids = None
    if payload and isinstance(payload, dict):
        val = payload.get('registration_ids')
        if isinstance(val, list):
            registration_ids = [str(x) for x in val if isinstance(x, (str, int))]
    result = await process_refunds(event_id, registration_ids=registration_ids)
    return result


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
                host_email = None
                if t:
                    team_doc = t.get('team_doc') or {}
                    members = team_doc.get('members') or []
                    cooking_loc = (t.get('cooking_location') or 'creator')
                    if members:
                        if cooking_loc == 'creator':
                            host_email = (members[0] or {}).get('email')
                        elif len(members) > 1:
                            host_email = (members[1] or {}).get('email')
                    if not host_email and members:
                        host_email = (members[0] or {}).get('email')
                addr = await _host_addr(host_email) if host_email else None
                if addr:
                    gg['host_address'] = addr[0]
                    gg['host_address_public'] = addr[1]
            except Exception:
                pass
        groups_out.append(gg)
    # Compose output
    out = {
        'version': m.get('version'),
        'metrics': m.get('metrics') or {},
        'algorithm': m.get('algorithm') or 'unknown',
        'groups': groups_out,
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
    # helper for host address
    from ..services.matching import _user_address_string as _host_addr
    for g in groups:
        phase = g.get('phase')
        host_id = str(g.get('host_team_id'))
        guest_ids = [str(x) for x in (g.get('guest_team_ids') or [])]
        host = tmap.get(host_id, {})
        guests = [tmap.get(tid, {}) for tid in guest_ids]
        base_score, warns = _score_group_phase(host, guests, phase, {})
        travel = await _travel_time_for_phase(host, guests)
        # compute host public address best-effort
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
        })
    metrics = _compute_metrics(new_groups, {})
    await db_mod.db.matches.update_one({'_id': m['_id']}, {'$set': {'groups': new_groups, 'metrics': metrics, 'updated_at': datetime.datetime.now(datetime.timezone.utc)}})
    return {'version': m.get('version'), 'metrics': metrics}


@router.post('/{event_id}/preview')
async def preview_groups(event_id: str, payload: dict, _=Depends(require_admin)):
    """Compute metrics and annotate provided groups without persisting.

    Payload: { groups: [ { phase, host_team_id, guest_team_ids }... ] }
    Returns: { groups: [ with score, travel_seconds, warnings ], metrics: {..} }
    """
    await require_event_published(event_id)
    groups_in = payload.get('groups') or []
    # Load event and build team map (lat/lon, capabilities)
    ev = await db_mod.db.events.find_one({'_id': ObjectId(event_id)})
    if not ev:
        raise HTTPException(status_code=404, detail='Event not found')
    teams = await _build_teams(ev['_id'])
    tmap: Dict[str, dict] = { str(t['team_id']): t for t in teams }
    # helper for host address
    from ..services.matching import _user_address_string as _host_addr
    new_groups: List[dict] = []
    for g in groups_in:
        phase = g.get('phase')
        host_id = str(g.get('host_team_id')) if g.get('host_team_id') is not None else None
        guest_ids = [str(x) for x in (g.get('guest_team_ids') or [])]
        host = tmap.get(host_id, {}) if host_id else {}
        guests = [tmap.get(tid, {}) for tid in guest_ids]
        base_score, warns = _score_group_phase(host, guests, phase, {})
        travel = await _travel_time_for_phase(host, guests)
        # compute host public address best-effort
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
        })
    metrics = _compute_metrics(new_groups, {})
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
    if version is not None:
        m = await db_mod.db.matches.find_one({'event_id': event_id, 'version': int(version)})
        if not m:
            raise HTTPException(status_code=404, detail='Match version not found')
        res = await db_mod.db.matches.delete_one({'_id': m['_id']})
        return {'deleted_count': res.deleted_count, 'version': int(version)}
    # delete all
    res = await db_mod.db.matches.delete_many({'event_id': event_id})
    # set event.matching_status back to not_started
    now = datetime.datetime.utcnow()
    await db_mod.db.events.update_one({'_id': ObjectId(event_id)}, {'$set': {'matching_status': 'not_started', 'updated_at': now}})
    return {'deleted_count': res.deleted_count}


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
    tmap: Dict[str, dict] = { str(t['team_id']): t for t in teams }
    # helper for host public address
    from ..services.matching import _user_address_string as _host_addr
    new_groups: List[dict] = []
    for g in groups_in:
        phase = g.get('phase')
        host_id = str(g.get('host_team_id')) if g.get('host_team_id') is not None else None
        guest_ids = [str(x) for x in (g.get('guest_team_ids') or [])]
        host = tmap.get(host_id, {}) if host_id else {}
        guests = [tmap.get(tid, {}) for tid in guest_ids]
        base_score, warns = _score_group_phase(host, guests, phase, {})
        travel = await _travel_time_for_phase(host, guests)
        # host address
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
        })
    metrics = _compute_metrics(new_groups, {})
    await db_mod.db.matches.update_one({'_id': m['_id']}, {'$set': {'groups': new_groups, 'metrics': metrics, 'updated_at': datetime.datetime.utcnow()}})
    return { 'status': 'saved', 'version': version, 'metrics': metrics }
