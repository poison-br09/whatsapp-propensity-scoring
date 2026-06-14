import logging
import secrets

from fastapi.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.core.config import Settings, get_settings
from app.models.whatsapp import (
    WhatsAppMessageWebhook,
    WhatsAppMessageWebhookResponse,
    WhatsAppPollCreatedWebhook,
    WhatsAppPollCreatedWebhookResponse,
    WhatsAppPollVoteWebhook,
    WhatsAppPollVoteWebhookResponse,
    WhatsAppSessionEventWebhook,
    WhatsAppSessionEventWebhookResponse,
)
from app.repositories.supabase_poll_repository import SupabasePollRepository
from app.services.email_service import EmailService
from app.services.keyword_analysis_service import KeywordAnalysisService
from app.services.keyword_analysis_state import KeywordAnalysisStateService
from app.services.propensity_scoring_state import PropensityScoringStateService
from app.services.whatsapp_session_state import WhatsAppSessionStateService
from app.services.whatsapp_poll_ingestion_service import WhatsAppPollIngestionService

Logger = logging.getLogger
logger = Logger(__name__)

router = APIRouter(prefix='/internal/whatsapp', tags=['internal-whatsapp'])


def get_poll_repository(
    settings: Settings = Depends(get_settings),
) -> SupabasePollRepository:
    return SupabasePollRepository(settings)


def get_whatsapp_poll_ingestion_service(
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> WhatsAppPollIngestionService:
    return WhatsAppPollIngestionService(repository)


def get_email_service(
    settings: Settings = Depends(get_settings),
) -> EmailService:
    return EmailService(settings)


def get_session_state_service(request: Request) -> WhatsAppSessionStateService:
    session_state_service = getattr(request.app.state, 'whatsapp_session_state', None)
    if session_state_service is None:
        raise HTTPException(status_code=503, detail='WhatsApp session state service is unavailable.')

    return session_state_service


def get_keyword_analysis_state(request: Request) -> KeywordAnalysisStateService:
    state = getattr(request.app.state, 'keyword_analysis_state', None)
    if state is None:
        raise HTTPException(status_code=503, detail='Keyword analysis state service is unavailable.')
    return state


def get_propensity_scoring_state(request: Request) -> PropensityScoringStateService:
    state = getattr(request.app.state, 'propensity_scoring_state', None)
    if state is None:
        raise HTTPException(status_code=503, detail='Propensity scoring state service is unavailable.')
    return state


def get_keyword_analysis_service(
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> KeywordAnalysisService:
    return KeywordAnalysisService(repository)


def require_internal_token(
    x_internal_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.whatsapp_internal_token:
        logger.error('WhatsApp internal token is not configured')
        raise HTTPException(status_code=500, detail='WhatsApp internal token is not configured.')

    if x_internal_token is None:
        logger.warning('Rejected internal WhatsApp request: missing x-internal-token header')
        raise HTTPException(status_code=401, detail='Missing x-internal-token header.')

    if not secrets.compare_digest(x_internal_token, settings.whatsapp_internal_token):
        logger.warning('Rejected internal WhatsApp request: invalid x-internal-token header')
        raise HTTPException(status_code=401, detail='Invalid internal token.')


@router.post('/poll-created', response_model=WhatsAppPollCreatedWebhookResponse)
async def ingest_poll_created(
    payload: WhatsAppPollCreatedWebhook,
    _: None = Depends(require_internal_token),
    service: WhatsAppPollIngestionService = Depends(get_whatsapp_poll_ingestion_service),
) -> WhatsAppPollCreatedWebhookResponse:
    logger.info(
        'Received internal WhatsApp poll definition poll_message_id=%s options=%s',
        payload.poll_message_id,
        payload.poll_options,
    )
    return await service.ingest_poll_created(payload)


@router.post('/poll-vote', response_model=WhatsAppPollVoteWebhookResponse)
async def ingest_poll_vote(
    payload: WhatsAppPollVoteWebhook,
    request: Request,
    _: None = Depends(require_internal_token),
    service: WhatsAppPollIngestionService = Depends(get_whatsapp_poll_ingestion_service),
) -> WhatsAppPollVoteWebhookResponse:
    logger.info(
        'Received internal WhatsApp poll vote poll_message_id=%s voter_jid=%s voter_phone=%s selected_options=%s',
        payload.poll_message_id,
        payload.voter_jid,
        payload.voter_phone,
        payload.selected_options,
    )
    scoring_enabled = get_propensity_scoring_state(request).enabled
    return await service.ingest_vote(payload, scoring_enabled=scoring_enabled)


@router.post('/message', response_model=WhatsAppMessageWebhookResponse)
async def ingest_message(
    payload: WhatsAppMessageWebhook,
    request: Request,
    _: None = Depends(require_internal_token),
    service: KeywordAnalysisService = Depends(get_keyword_analysis_service),
) -> WhatsAppMessageWebhookResponse:
    state: KeywordAnalysisStateService = get_keyword_analysis_state(request)
    if not state.enabled:
        logger.debug(
            'Keyword analysis disabled, skipping message_id=%s',
            payload.message_id,
        )
        return WhatsAppMessageWebhookResponse(matched=False)

    logger.debug(
        'Received internal WhatsApp message message_id=%s sender_phone=%s',
        payload.message_id,
        payload.sender_phone,
    )
    return await service.analyze_message(payload)


@router.post('/session-event', response_model=WhatsAppSessionEventWebhookResponse)
async def ingest_session_event(
    payload: WhatsAppSessionEventWebhook,
    _: None = Depends(require_internal_token),
    email_service: EmailService = Depends(get_email_service),
    session_state_service: WhatsAppSessionStateService = Depends(get_session_state_service),
) -> WhatsAppSessionEventWebhookResponse:
    logger.info(
        'Received internal WhatsApp session event event=%s phone_number=%s status_code=%s',
        payload.event,
        payload.phone_number,
        payload.status_code,
    )
    session_state_service.record_event(payload)

    if payload.event != 'logged_out':
        return WhatsAppSessionEventWebhookResponse(accepted=True, email_sent=False)

    email_sent = await run_in_threadpool(
        email_service.send_whatsapp_logged_out_alert,
        phone_number=payload.phone_number,
        occurred_at=payload.occurred_at,
        status_code=payload.status_code,
    )
    return WhatsAppSessionEventWebhookResponse(accepted=True, email_sent=email_sent)
