import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_poll_repository, require_api_key
from app.core.config import Settings, get_settings
from app.models.whatsapp import (
    WhatsAppBackfillActionResponse,
    WhatsAppKeywordAnalysisActionResponse,
    WhatsAppKeywordControlRequest,
    WhatsAppKeywordControlResponse,
    WhatsAppKeywordControlResult,
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


async def _call_bridge_backfill(action: str, settings: Settings) -> WhatsAppBackfillActionResponse:
    url = f'http://127.0.0.1:{settings.backfill_control_port}/backfill/{action}'
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers={'x-control-token': settings.whatsapp_internal_token or ''},
                timeout=5.0,
            )
    except httpx.ConnectError as exc:
        logger.error('Backfill control server unreachable action=%s', action)
        raise HTTPException(status_code=503, detail='Bridge backfill control server is not reachable.') from exc

    if response.status_code == 401:
        raise HTTPException(status_code=502, detail='Bridge rejected the control token.')
    if response.status_code == 503:
        raise HTTPException(status_code=503, detail='Bridge socket is not connected.')
    if not response.is_success:
        raise HTTPException(status_code=502, detail=f'Bridge returned unexpected status {response.status_code}.')

    return WhatsAppBackfillActionResponse(action=action, accepted=True)


# ── History backfill ──────────────────────────────────────────────────────────

@router.post('/backfill/start', response_model=WhatsAppBackfillActionResponse)
async def start_history_backfill(
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> WhatsAppBackfillActionResponse:
    logger.info('Admin: start history backfill')
    return await _call_bridge_backfill('start', settings)


@router.post('/backfill/stop', response_model=WhatsAppBackfillActionResponse)
async def stop_history_backfill(
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> WhatsAppBackfillActionResponse:
    logger.info('Admin: stop history backfill')
    return await _call_bridge_backfill('stop', settings)


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
