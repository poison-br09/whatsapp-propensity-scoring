import logging
import secrets

from fastapi.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.core.config import Settings, get_settings
from app.core.whatsapp_bridge import BaileysBridgeProcessManager
from app.models.whatsapp import (
    WhatsAppPairingRequest,
    WhatsAppPairingResponse,
    WhatsAppSessionStatusResponse,
)
from app.services.whatsapp_session_state import WhatsAppSessionStateService

Logger = logging.getLogger
logger = Logger(__name__)

router = APIRouter(prefix='/whatsapp', tags=['whatsapp'])
api_key_header = APIKeyHeader(
    name='x-api-key',
    auto_error=False,
    description='API key required to manage the WhatsApp bridge session.',
)


def require_api_key(
    x_api_key: str | None = Security(api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.api_key:
        logger.error('API key protection is not configured')
        raise HTTPException(status_code=500, detail='API key is not configured.')

    if x_api_key is None:
        logger.warning('Rejected WhatsApp pairing request: missing x-api-key header')
        raise HTTPException(status_code=401, detail='Missing x-api-key header.')

    if not secrets.compare_digest(x_api_key, settings.api_key):
        logger.warning('Rejected WhatsApp pairing request: invalid x-api-key header')
        raise HTTPException(status_code=401, detail='Invalid API key.')


def get_bridge_manager(request: Request) -> BaileysBridgeProcessManager:
    bridge_manager = getattr(request.app.state, 'bridge_manager', None)
    if bridge_manager is None:
        logger.error('WhatsApp bridge manager is unavailable on application state')
        raise HTTPException(status_code=503, detail='WhatsApp bridge manager is unavailable.')

    return bridge_manager


def get_session_state_service(request: Request) -> WhatsAppSessionStateService:
    session_state_service = getattr(request.app.state, 'whatsapp_session_state', None)
    if session_state_service is None:
        logger.error('WhatsApp session state service is unavailable on application state')
        raise HTTPException(status_code=503, detail='WhatsApp session state service is unavailable.')

    return session_state_service


@router.post(
    '/pairing-code',
    response_model=WhatsAppPairingResponse,
    responses={
        400: {'description': 'Invalid phone number.'},
        401: {'description': 'Missing or invalid x-api-key header.'},
        503: {'description': 'WhatsApp bridge is unavailable.'},
        504: {'description': 'Timed out waiting for the pairing code.'},
    },
)
async def request_whatsapp_pairing_code(
    payload: WhatsAppPairingRequest,
    _: None = Depends(require_api_key),
    bridge_manager: BaileysBridgeProcessManager = Depends(get_bridge_manager),
) -> WhatsAppPairingResponse:
    logger.info('Received WhatsApp pairing request phone_number=%s', payload.phone_number)
    try:
        pairing_code = await run_in_threadpool(bridge_manager.request_pairing_code, payload.phone_number)
    except ValueError as exc:
        logger.warning('Rejected WhatsApp pairing request phone_number=%s reason=%s', payload.phone_number, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimeoutError as exc:
        logger.error('Timed out generating WhatsApp pairing code phone_number=%s', payload.phone_number)
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.error('WhatsApp pairing request failed phone_number=%s reason=%s', payload.phone_number, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return WhatsAppPairingResponse(
        phone_number=''.join(ch for ch in payload.phone_number if ch.isdigit()),
        pairing_code=pairing_code,
    )


@router.get(
    '/status',
    response_model=WhatsAppSessionStatusResponse,
    responses={
        401: {'description': 'Missing or invalid x-api-key header.'},
        503: {'description': 'WhatsApp session state is unavailable.'},
    },
)
async def get_whatsapp_status(
    _: None = Depends(require_api_key),
    session_state_service: WhatsAppSessionStateService = Depends(get_session_state_service),
) -> WhatsAppSessionStatusResponse:
    status = session_state_service.get_status()
    logger.info(
        'Returned WhatsApp status status=%s pairing_required=%s target_group_jid=%s',
        status.status,
        status.pairing_required,
        status.target_group_jid,
    )
    return status
