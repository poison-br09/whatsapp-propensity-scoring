import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.api.deps import require_api_key
from app.core.whatsapp_bridge import BaileysBridgeProcessManager
from app.models.whatsapp import (
    WhatsAppPairingRequest,
    WhatsAppPairingResponse,
    WhatsAppSessionStatusResponse,
)
from app.services.whatsapp_session_state import WhatsAppSessionStateService

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/whatsapp', tags=['whatsapp'])


def get_bridge_manager(request: Request) -> BaileysBridgeProcessManager:
    bridge_manager = getattr(request.app.state, 'bridge_manager', None)
    if bridge_manager is None:
        raise HTTPException(status_code=503, detail='WhatsApp bridge manager is unavailable.')
    return bridge_manager


def get_session_state_service(request: Request) -> WhatsAppSessionStateService:
    session_state_service = getattr(request.app.state, 'whatsapp_session_state', None)
    if session_state_service is None:
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except RuntimeError as exc:
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
