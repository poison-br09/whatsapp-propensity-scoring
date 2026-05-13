import logging
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException

from app.core.config import Settings, get_settings
from app.models.whatsapp import (
    WhatsAppPollCreatedWebhook,
    WhatsAppPollCreatedWebhookResponse,
    WhatsAppPollVoteWebhook,
    WhatsAppPollVoteWebhookResponse,
)
from app.repositories.supabase_poll_repository import SupabasePollRepository
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
    _: None = Depends(require_internal_token),
    service: WhatsAppPollIngestionService = Depends(get_whatsapp_poll_ingestion_service),
) -> WhatsAppPollVoteWebhookResponse:
    logger.info(
        'Received internal WhatsApp poll vote poll_message_id=%s voter_jid=%s selected_options=%s',
        payload.poll_message_id,
        payload.voter_jid,
        payload.selected_options,
    )
    return await service.ingest_vote(payload)
