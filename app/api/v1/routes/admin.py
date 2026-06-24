import io
import logging

import httpx
import openpyxl
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import UserProfile, get_current_user, get_poll_repository, require_api_key, require_superadmin
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
    port = _resolve_backfill_port(request, current_user, settings)
    return await _call_bridge_backfill('start', port, settings)


@router.post('/backfill/stop', response_model=WhatsAppBackfillActionResponse)
async def stop_history_backfill(
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> WhatsAppBackfillActionResponse:
    logger.info('Admin: stop history backfill user_id=%s', current_user.user_id)
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
    _: None = Depends(require_api_key),
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> WhatsAppKeywordListResponse:
    rows = await repository.get_all_keywords()
    keywords = [WhatsAppKeywordItem(id=row['id'], keyword=row['keyword'], is_active=row['is_active']) for row in rows]
    return WhatsAppKeywordListResponse(keywords=keywords)


@router.delete('/keyword-analysis/keywords', response_model=WhatsAppKeywordDeleteResponse)
async def delete_keywords(
    payload: WhatsAppKeywordDeleteRequest,
    _: None = Depends(require_api_key),
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> WhatsAppKeywordDeleteResponse:
    logger.info('Admin: delete keywords keywords=%s', payload.keywords)
    rows = await repository.delete_keywords(payload.keywords)
    results = [WhatsAppKeywordDeleteResult(keyword=row['keyword'], deleted=row['deleted']) for row in rows]
    return WhatsAppKeywordDeleteResponse(results=results)


@router.get('/keyword-analysis/matches/export')
async def export_matches(
    current_user: UserProfile = Depends(get_current_user),
    repository: SupabasePollRepository = Depends(get_poll_repository),
    keyword: list[str] = Query(),
    date_from: str | None = Query(default=None, description='ISO 8601, e.g. 2025-01-01T00:00:00Z'),
    date_to: str | None = Query(default=None, description='ISO 8601, e.g. 2025-12-31T23:59:59Z'),
) -> StreamingResponse:
    receiver_phone = None if current_user.role == 'superadmin' else current_user.whatsapp_phone
    result = await repository.query_matches(
        keywords=keyword,
        date_from=date_from,
        date_to=date_to,
        receiver_phone=receiver_phone,
        limit=10000,
        offset=0,
    )
    rows = result['rows']

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
    _: None = Depends(require_api_key),
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
    _: None = Depends(require_api_key),
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


# ── User management ───────────────────────────────────────────────────────────

@router.get('/users')
async def list_users(
    current_user: UserProfile = Depends(require_superadmin),
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> dict:
    users = await repository.fetch_users()
    return {'users': users}


@router.patch('/users/{user_id}/deactivate')
async def deactivate_user(
    user_id: str,
    request: Request,
    current_user: UserProfile = Depends(require_superadmin),
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> dict:
    await repository.deactivate_user_db(user_id)
    pool: BridgePool | None = getattr(request.app.state, 'bridge_pool', None)
    if pool:
        pool.stop_bridge(user_id)
    logger.info('Deactivated user user_id=%s by superadmin=%s', user_id, current_user.user_id)
    return {'user_id': user_id, 'deactivated': True}
