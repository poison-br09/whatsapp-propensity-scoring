import asyncio
import io
import logging

import httpx
import openpyxl
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import UserProfile, get_current_user, get_poll_repository, require_admin, require_api_key, require_superadmin
from app.core.bridge_pool import BridgePool
from app.core.config import Settings, get_settings
from app.models.whatsapp import (
    WhatsAppBackfillActionResponse,
    WhatsAppKeywordAddRequest,
    WhatsAppKeywordAddResponse,
    WhatsAppKeywordAddResult,
    WhatsAppKeywordAnalysisActionResponse,
    WhatsAppKeywordControlRequest,
    WhatsAppKeywordControlResponse,
    WhatsAppKeywordControlResult,
    WhatsAppKeywordDeleteRequest,
    WhatsAppKeywordDeleteResponse,
    WhatsAppKeywordDeleteResult,
    WhatsAppKeywordItem,
    WhatsAppKeywordListResponse,
    WhatsAppPropensityActionResponse,
)
from app.repositories.supabase_poll_repository import SupabasePollRepository
from app.services.keyword_analysis_state import KeywordAnalysisStateService
from app.services.propensity_scoring_state import PropensityScoringStateService

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/admin', tags=['admin'])


def _get_keyword_analysis_state(request: Request) -> KeywordAnalysisStateService:
    state = getattr(request.app.state, 'keyword_analysis_state', None)
    if state is None:
        raise HTTPException(status_code=503, detail='Keyword analysis state service is unavailable.')
    return state


def _get_propensity_state(request: Request) -> PropensityScoringStateService:
    state = getattr(request.app.state, 'propensity_scoring_state', None)
    if state is None:
        raise HTTPException(status_code=503, detail='Propensity scoring state service is unavailable.')
    return state


async def _call_bridge_backfill(action: str, port: int, settings: Settings) -> WhatsAppBackfillActionResponse:
    url = f'http://127.0.0.1:{port}/backfill/{action}'
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers={'x-control-token': settings.whatsapp_internal_token or ''},
                timeout=5.0,
            )
    except httpx.ConnectError as exc:
        logger.error('Backfill control server unreachable action=%s port=%s', action, port)
        raise HTTPException(status_code=503, detail='Bridge backfill control server is not reachable.') from exc

    if response.status_code == 401:
        raise HTTPException(status_code=502, detail='Bridge rejected the control token.')
    if response.status_code == 503:
        raise HTTPException(status_code=503, detail='Bridge socket is not connected.')
    if not response.is_success:
        raise HTTPException(status_code=502, detail=f'Bridge returned unexpected status {response.status_code}.')

    return WhatsAppBackfillActionResponse(action=action, accepted=True)


async def _fetch_bridge_groups(port: int, settings: Settings) -> dict:
    url = f'http://127.0.0.1:{port}/groups'
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers={'x-control-token': settings.whatsapp_internal_token or ''},
                timeout=10.0,
            )
        if not response.is_success:
            return {}
        return response.json()
    except Exception:
        return {}


async def _backfill_all_bridges(action: str, request: Request, settings: Settings) -> WhatsAppBackfillActionResponse:
    pool: BridgePool | None = getattr(request.app.state, 'bridge_pool', None)
    if pool is None:
        raise HTTPException(status_code=503, detail='Bridge pool is unavailable.')
    bridges = pool.all_bridges()
    if not bridges:
        raise HTTPException(status_code=503, detail='No active bridges found.')
    results = await asyncio.gather(
        *[_call_bridge_backfill(action, bridge._backfill_port, settings) for bridge in bridges.values()],
        return_exceptions=True,
    )
    failures = [r for r in results if isinstance(r, Exception)]
    if failures:
        logger.warning('Backfill %s: %d/%d bridges failed', action, len(failures), len(bridges))
    return WhatsAppBackfillActionResponse(action=action, accepted=True)


def _resolve_backfill_port(request: Request, current_user: UserProfile, settings: Settings) -> int:
    if current_user.role == 'superadmin':
        return settings.backfill_control_port
    pool: BridgePool | None = getattr(request.app.state, 'bridge_pool', None)
    if pool is None:
        raise HTTPException(status_code=503, detail='Bridge pool is unavailable.')
    port = pool.get_backfill_port(current_user.user_id)
    if port is None:
        raise HTTPException(status_code=404, detail='No WhatsApp session found for this account.')
    return port


# ── History backfill ──────────────────────────────────────────────────────────

@router.post('/backfill/start', response_model=WhatsAppBackfillActionResponse)
async def start_history_backfill(
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> WhatsAppBackfillActionResponse:
    logger.info('Admin: start history backfill user_id=%s', current_user.user_id)
    if current_user.role in ('superadmin', 'admin'):
        return await _backfill_all_bridges('start', request, settings)
    port = _resolve_backfill_port(request, current_user, settings)
    return await _call_bridge_backfill('start', port, settings)


@router.post('/backfill/stop', response_model=WhatsAppBackfillActionResponse)
async def stop_history_backfill(
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> WhatsAppBackfillActionResponse:
    logger.info('Admin: stop history backfill user_id=%s', current_user.user_id)
    if current_user.role in ('superadmin', 'admin'):
        return await _backfill_all_bridges('stop', request, settings)
    port = _resolve_backfill_port(request, current_user, settings)
    return await _call_bridge_backfill('stop', port, settings)


# ── Keyword analysis ──────────────────────────────────────────────────────────

@router.post('/keyword-analysis/start', response_model=WhatsAppKeywordAnalysisActionResponse)
async def start_keyword_analysis(
    request: Request,
    _: None = Depends(require_api_key),
) -> WhatsAppKeywordAnalysisActionResponse:
    _get_keyword_analysis_state(request).enable()
    logger.info('Admin: keyword analysis enabled')
    return WhatsAppKeywordAnalysisActionResponse(action='start', enabled=True)


@router.post('/keyword-analysis/stop', response_model=WhatsAppKeywordAnalysisActionResponse)
async def stop_keyword_analysis(
    request: Request,
    _: None = Depends(require_api_key),
) -> WhatsAppKeywordAnalysisActionResponse:
    _get_keyword_analysis_state(request).disable()
    logger.info('Admin: keyword analysis disabled')
    return WhatsAppKeywordAnalysisActionResponse(action='stop', enabled=False)


@router.get('/keyword-analysis/keywords', response_model=WhatsAppKeywordListResponse)
async def list_keywords(
    _: UserProfile = Depends(get_current_user),
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> WhatsAppKeywordListResponse:
    rows = await repository.get_all_keywords()
    keywords = [WhatsAppKeywordItem(id=row['id'], keyword=row['keyword'], is_active=row['is_active']) for row in rows]
    return WhatsAppKeywordListResponse(keywords=keywords)


@router.delete('/keyword-analysis/keywords', response_model=WhatsAppKeywordDeleteResponse)
async def delete_keywords(
    payload: WhatsAppKeywordDeleteRequest,
    _: UserProfile = Depends(require_superadmin),
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> WhatsAppKeywordDeleteResponse:
    logger.info('Admin: delete keywords keywords=%s', payload.keywords)
    rows = await repository.delete_keywords(payload.keywords)
    results = [WhatsAppKeywordDeleteResult(keyword=row['keyword'], deleted=row['deleted']) for row in rows]
    return WhatsAppKeywordDeleteResponse(results=results)


@router.get('/keyword-analysis/matches')
async def list_matches(
    current_user: UserProfile = Depends(get_current_user),
    repository: SupabasePollRepository = Depends(get_poll_repository),
    keyword: list[str] = Query(),
    date_from: str | None = Query(default=None, description='ISO 8601, e.g. 2025-01-01T00:00:00Z'),
    date_to: str | None = Query(default=None, description='ISO 8601, e.g. 2025-12-31T23:59:59Z'),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
) -> dict:
    offset = (page - 1) * page_size
    if current_user.role in ('superadmin', 'admin'):
        receiver_phone = None
    elif current_user.whatsapp_phone:
        receiver_phone = current_user.whatsapp_phone
    else:
        return {'total': 0, 'page': page, 'page_size': page_size, 'results': []}

    result = await repository.query_matches(
        keywords=keyword,
        date_from=date_from,
        date_to=date_to,
        receiver_phone=receiver_phone,
        limit=page_size,
        offset=offset,
    )
    return {'total': result['total'], 'page': page, 'page_size': page_size, 'results': result['rows']}


@router.get('/keyword-analysis/matches/export')
async def export_matches(
    current_user: UserProfile = Depends(get_current_user),
    repository: SupabasePollRepository = Depends(get_poll_repository),
    keyword: list[str] = Query(),
    date_from: str | None = Query(default=None, description='ISO 8601, e.g. 2025-01-01T00:00:00Z'),
    date_to: str | None = Query(default=None, description='ISO 8601, e.g. 2025-12-31T23:59:59Z'),
) -> StreamingResponse:
    if current_user.role in ('superadmin', 'admin'):
        receiver_phone = None  # no filter — sees everything
        result = await repository.query_matches(
            keywords=keyword, date_from=date_from, date_to=date_to,
            receiver_phone=receiver_phone, limit=10000, offset=0,
        )
        rows = result['rows']
    elif current_user.whatsapp_phone:
        result = await repository.query_matches(
            keywords=keyword, date_from=date_from, date_to=date_to,
            receiver_phone=current_user.whatsapp_phone, limit=10000, offset=0,
        )
        rows = result['rows']
    else:
        rows = []  # user has no WhatsApp number linked yet

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Matches'
    ws.append(['Keyword', 'Sender Name', 'Sender Phone', 'Receiver Phone', 'Message', 'Message Date'])
    for row in rows:
        ws.append([
            row.get('keyword'),
            row.get('sender_name'),
            row.get('sender_phone'),
            row.get('receiver_phone'),
            row.get('message'),
            row.get('message_date'),
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f'matches_{"_".join(keyword)}.xlsx'
    return StreamingResponse(
        buf,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@router.post('/keyword-analysis/keywords', response_model=WhatsAppKeywordAddResponse)
async def add_keywords(
    payload: WhatsAppKeywordAddRequest,
    _: UserProfile = Depends(get_current_user),
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> WhatsAppKeywordAddResponse:
    logger.info('Admin: add keywords keywords=%s', payload.keywords)
    rows = await repository.add_keywords(payload.keywords)
    results = [
        WhatsAppKeywordAddResult(
            keyword=row['keyword'],
            added=row['added'],
            already_existed=row['already_existed'],
        )
        for row in rows
    ]
    return WhatsAppKeywordAddResponse(results=results)


@router.patch('/keyword-analysis/keywords', response_model=WhatsAppKeywordControlResponse)
async def control_keywords(
    payload: WhatsAppKeywordControlRequest,
    _: UserProfile = Depends(require_superadmin),
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> WhatsAppKeywordControlResponse:
    requested = {kw.lower() for kw in payload.keywords}
    logger.info('Admin: keyword control keywords=%s enabled=%s', list(requested), payload.enabled)
    updated_rows = await repository.set_keywords_active(list(requested), payload.enabled)
    updated_set = {row['keyword'].lower() for row in updated_rows}
    results = [
        WhatsAppKeywordControlResult(
            keyword=kw,
            enabled=payload.enabled,
            found=kw.lower() in updated_set,
        )
        for kw in payload.keywords
    ]
    return WhatsAppKeywordControlResponse(updated=results)


# ── Propensity scoring ────────────────────────────────────────────────────────

@router.post('/propensity/start', response_model=WhatsAppPropensityActionResponse)
async def start_propensity_scoring(
    request: Request,
    _: None = Depends(require_api_key),
) -> WhatsAppPropensityActionResponse:
    _get_propensity_state(request).enable()
    logger.info('Admin: propensity scoring enabled')
    return WhatsAppPropensityActionResponse(action='start', enabled=True)


@router.post('/propensity/stop', response_model=WhatsAppPropensityActionResponse)
async def stop_propensity_scoring(
    request: Request,
    _: None = Depends(require_api_key),
) -> WhatsAppPropensityActionResponse:
    _get_propensity_state(request).disable()
    logger.info('Admin: propensity scoring disabled')
    return WhatsAppPropensityActionResponse(action='stop', enabled=False)


# ── Poll tracking ─────────────────────────────────────────────────────────────

@router.get('/polls')
async def list_polls(
    _: UserProfile = Depends(require_admin),
    repository: SupabasePollRepository = Depends(get_poll_repository),
    group_jid: str | None = Query(default=None, description='Filter by WhatsApp group JID'),
    group_name: str | None = Query(default=None, description='Filter by WhatsApp group name'),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict:
    offset = (page - 1) * page_size
    result = await repository.query_polls(group_jid=group_jid, group_name=group_name, limit=page_size, offset=offset)
    return {'total': result['total'], 'page': page, 'page_size': page_size, 'polls': result['rows']}


@router.get('/polls/{poll_message_id}/votes')
async def list_poll_votes(
    poll_message_id: str,
    _: UserProfile = Depends(require_admin),
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> dict:
    votes = await repository.query_poll_votes(poll_message_id)
    return {'poll_message_id': poll_message_id, 'total_voters': len(votes), 'votes': votes}


# ── Group participants ────────────────────────────────────────────────────────

@router.get('/groups')
async def list_groups(
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    pool: BridgePool | None = getattr(request.app.state, 'bridge_pool', None)
    if pool is None:
        raise HTTPException(status_code=503, detail='Bridge pool is unavailable.')

    if current_user.role in ('superadmin', 'admin'):
        bridges = pool.all_bridges()
        results = await asyncio.gather(*[
            _fetch_bridge_groups(b._backfill_port, settings) for b in bridges.values()
        ])
        groups = []
        for data in results:
            receiver = data.get('receiver_phone')
            for g in data.get('groups', []):
                groups.append({'jid': g['jid'], 'name': g['name'], 'receiver_phone': receiver})
        return {'groups': groups}

    port = pool.get_backfill_port(current_user.user_id)
    if port is None:
        raise HTTPException(status_code=404, detail='No active bridge for this account.')
    data = await _fetch_bridge_groups(port, settings)
    groups = [{'jid': g['jid'], 'name': g['name']} for g in data.get('groups', [])]
    return {'groups': groups}


@router.get('/groups/participants/export')
async def export_group_participants(
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    group_name: list[str] = Query(default=[]),
    user_id: list[str] = Query(default=[]),
) -> StreamingResponse:
    pool: BridgePool | None = getattr(request.app.state, 'bridge_pool', None)
    if pool is None:
        raise HTTPException(status_code=503, detail='Bridge pool is unavailable.')

    filter_groups = {n.lower() for n in group_name}

    if current_user.role in ('superadmin', 'admin'):
        bridges = pool.all_bridges()
        if user_id:
            bridges = {uid: b for uid, b in bridges.items() if uid in user_id}
        datas = await asyncio.gather(*[
            _fetch_bridge_groups(b._backfill_port, settings) for b in bridges.values()
        ])
    else:
        port = pool.get_backfill_port(current_user.user_id)
        if port is None:
            raise HTTPException(status_code=404, detail='No active bridge for this account.')
        datas = [await _fetch_bridge_groups(port, settings)]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Participants'
    ws.append(['Phone Number', 'Name', 'WhatsApp Group', 'Login Number'])

    for data in datas:
        receiver = data.get('receiver_phone', '')
        for group in data.get('groups', []):
            gname = group.get('name') or ''
            if filter_groups and gname.lower() not in filter_groups:
                continue
            for p in group.get('participants', []):
                ws.append([p.get('phone'), p.get('name'), gname, receiver])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = 'group_participants.xlsx'
    return StreamingResponse(
        buf,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# ── User management ───────────────────────────────────────────────────────────

@router.get('/users')
async def list_users(
    request: Request,
    current_user: UserProfile = Depends(require_admin),
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> dict:
    exclude_superadmin = current_user.role == 'admin'
    users = await repository.fetch_users(exclude_superadmin=exclude_superadmin)
    pool: BridgePool | None = getattr(request.app.state, 'bridge_pool', None)
    for user in users:
        state = pool.get_session_state(user['id']) if pool else None
        user['whatsapp_status'] = state.get_status().status if state else None
    return {'users': users}


def _assert_not_superadmin_target(target_role: str | None, actor_role: str) -> None:
    if target_role == 'superadmin':
        raise HTTPException(status_code=403, detail='Cannot manage superadmin accounts.')
    if actor_role == 'admin' and target_role == 'admin':
        raise HTTPException(status_code=403, detail='Admin cannot manage other admin accounts.')


async def _fetch_target_role(user_id: str, repository: SupabasePollRepository) -> str | None:
    users = await repository.fetch_users()
    for u in users:
        if str(u['id']) == user_id:
            return u.get('role')
    return None


@router.patch('/users/{user_id}/deactivate')
async def deactivate_user(
    user_id: str,
    request: Request,
    current_user: UserProfile = Depends(require_admin),
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> dict:
    target_role = await _fetch_target_role(user_id, repository)
    _assert_not_superadmin_target(target_role, current_user.role)
    await repository.deactivate_user_db(user_id)
    pool: BridgePool | None = getattr(request.app.state, 'bridge_pool', None)
    if pool:
        pool.stop_bridge(user_id)
    logger.info('Deactivated user user_id=%s by %s=%s', user_id, current_user.role, current_user.user_id)
    return {'user_id': user_id, 'deactivated': True}


@router.patch('/users/{user_id}/activate')
async def activate_user(
    user_id: str,
    request: Request,
    current_user: UserProfile = Depends(require_admin),
    repository: SupabasePollRepository = Depends(get_poll_repository),
    settings: Settings = Depends(get_settings),
) -> dict:
    target_role = await _fetch_target_role(user_id, repository)
    _assert_not_superadmin_target(target_role, current_user.role)
    row = await repository.activate_user_db(user_id)
    if not row:
        raise HTTPException(status_code=404, detail='User not found.')
    pool: BridgePool | None = getattr(request.app.state, 'bridge_pool', None)
    if pool and row.get('whatsapp_phone') and pool.get(user_id) is None:
        await asyncio.to_thread(pool.start_bridge, user_id, row['whatsapp_phone'], row.get('target_group_jid'))
    logger.info('Activated user user_id=%s by %s=%s', user_id, current_user.role, current_user.user_id)
    return {'user_id': user_id, 'activated': True}


@router.delete('/users/{user_id}')
async def delete_user(
    user_id: str,
    request: Request,
    current_user: UserProfile = Depends(require_admin),
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> dict:
    target_role = await _fetch_target_role(user_id, repository)
    _assert_not_superadmin_target(target_role, current_user.role)
    pool: BridgePool | None = getattr(request.app.state, 'bridge_pool', None)
    if pool:
        pool.stop_bridge(user_id)
    row = await repository.delete_user_db(user_id)
    if not row:
        raise HTTPException(status_code=404, detail='User not found.')
    logger.info('Deleted user user_id=%s by %s=%s', user_id, current_user.role, current_user.user_id)
    return {'user_id': user_id, 'deleted': True}


@router.patch('/users/{user_id}/role')
async def set_user_role(
    user_id: str,
    body: dict,
    _: UserProfile = Depends(require_superadmin),
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> dict:
    new_role = body.get('role', '')
    if new_role not in ('user', 'admin'):
        raise HTTPException(status_code=422, detail="role must be 'user' or 'admin'.")
    row = await repository.set_user_role(user_id, new_role)
    if not row:
        raise HTTPException(status_code=404, detail='User not found.')
    logger.info('Role changed user_id=%s new_role=%s', user_id, new_role)
    return {'user_id': user_id, 'role': new_role}
