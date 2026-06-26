import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from supabase import create_client

from app.api.deps import UserProfile, get_current_user
from app.core.auth import create_access_token
from app.core.bridge_pool import BridgePool
from app.core.config import Settings, get_settings
from app.models.whatsapp import (
    WhatsAppPairingRequest,
    WhatsAppPairingResponse,
    WhatsAppSessionStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/whatsapp', tags=['whatsapp'])


@router.post(
    '/pairing-code',
    response_model=WhatsAppPairingResponse,
    responses={
        400: {'description': 'Invalid phone number.'},
        401: {'description': 'Missing or invalid token.'},
        409: {'description': 'Phone number already linked to another account.'},
        503: {'description': 'Bridge pool unavailable or bridge failed to start.'},
        504: {'description': 'Timed out waiting for the pairing code.'},
    },
)
async def request_whatsapp_pairing_code(
    payload: WhatsAppPairingRequest,
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> WhatsAppPairingResponse:
    normalized_phone = ''.join(ch for ch in payload.phone_number if ch.isdigit())
    if not normalized_phone:
        raise HTTPException(status_code=400, detail='Invalid phone number.')

    pool: BridgePool | None = getattr(request.app.state, 'bridge_pool', None)
    if pool is None:
        raise HTTPException(status_code=503, detail='Bridge pool is unavailable.')

    new_token: str | None = None

    if pool.get(current_user.user_id) is None:
        # No bridge yet — save phone to DB and start the bridge now
        def _set_phone():
            client = create_client(settings.supabase_url, settings.supabase_service_role_key)
            return (
                client.table(settings.supabase_users_table)
                .update({'whatsapp_phone': normalized_phone})
                .eq('id', current_user.user_id)
                .execute()
            )

        try:
            await asyncio.to_thread(_set_phone)
        except Exception as exc:
            msg = str(exc).lower()
            if 'duplicate key' in msg or 'unique' in msg:
                raise HTTPException(status_code=409, detail='Phone number already linked to another account.')
            raise

        await run_in_threadpool(
            pool.start_bridge,
            current_user.user_id,
            normalized_phone,
            current_user.target_group_jid,
        )

        new_token = create_access_token(
            user_id=current_user.user_id,
            username=current_user.username,
            role=current_user.role,
            whatsapp_phone=normalized_phone,
            target_group_jid=current_user.target_group_jid,
            settings=settings,
        )
        logger.info('Linked phone and started bridge user_id=%s phone=%s', current_user.user_id, normalized_phone)

    bridge = pool.get(current_user.user_id)
    if bridge is None:
        raise HTTPException(status_code=503, detail='Bridge failed to start.')

    logger.info('Requesting pairing code phone=%s user_id=%s', normalized_phone, current_user.user_id)
    try:
        pairing_code = await run_in_threadpool(bridge.request_pairing_code, normalized_phone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return WhatsAppPairingResponse(
        phone_number=normalized_phone,
        pairing_code=pairing_code,
        access_token=new_token,
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
