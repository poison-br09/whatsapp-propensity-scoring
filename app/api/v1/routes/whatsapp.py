import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.api.deps import UserProfile, get_current_user
from app.core.bridge_pool import BridgePool
from app.core.whatsapp_bridge import BaileysBridgeProcessManager
from app.models.whatsapp import (
    WhatsAppPairingRequest,
    WhatsAppPairingResponse,
    WhatsAppSessionStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/whatsapp', tags=['whatsapp'])


def _get_bridge_for_user(request: Request, user_id: str) -> BaileysBridgeProcessManager:
    pool: BridgePool = getattr(request.app.state, 'bridge_pool', None)
    if pool is None:
        raise HTTPException(status_code=503, detail='Bridge pool is unavailable.')
    bridge = pool.get(user_id)
    if bridge is None:
        raise HTTPException(status_code=404, detail='No WhatsApp session found for this account. Register first.')
    return bridge


@router.post(
    '/pairing-code',
    response_model=WhatsAppPairingResponse,
    responses={
        400: {'description': 'Invalid phone number.'},
        401: {'description': 'Missing or invalid token.'},
        404: {'description': 'No WhatsApp session found for this account.'},
        503: {'description': 'Bridge pool is unavailable.'},
        504: {'description': 'Timed out waiting for the pairing code.'},
    },
)
async def request_whatsapp_pairing_code(
    payload: WhatsAppPairingRequest,
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
) -> WhatsAppPairingResponse:
    logger.info('Received WhatsApp pairing request phone_number=%s user_id=%s', payload.phone_number, current_user.user_id)
    bridge = _get_bridge_for_user(request, current_user.user_id)
    try:
        pairing_code = await run_in_threadpool(bridge.request_pairing_code, payload.phone_number)
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
        401: {'description': 'Missing or invalid token.'},
        404: {'description': 'No WhatsApp session found for this account.'},
        503: {'description': 'Bridge pool is unavailable.'},
    },
)
async def get_whatsapp_status(
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
) -> WhatsAppSessionStatusResponse:
    pool: BridgePool | None = getattr(request.app.state, 'bridge_pool', None)
    if pool is None:
        raise HTTPException(status_code=503, detail='Bridge pool is unavailable.')
    state = pool.get_session_state(current_user.user_id)
    if state is None:
        raise HTTPException(status_code=404, detail='No WhatsApp session found for this account.')
    status = state.get_status()
    logger.info(
        'Returned WhatsApp status status=%s pairing_required=%s user_id=%s',
        status.status,
        status.pairing_required,
        current_user.user_id,
    )
    return status
