import asyncio
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from supabase import create_client

from app.core.auth import create_access_token, hash_password, require_superadmin, verify_password
from app.core.config import Settings, get_settings
from app.models.user import UserLoginRequest, UserLoginResponse, UserProfile, UserRegisterRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/auth', tags=['auth'])


def _get_supabase(settings: Settings):
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


@router.post('/register', response_model=UserLoginResponse)
async def register(
    payload: UserRegisterRequest,
    request: Request,
    _: UserProfile = Depends(require_superadmin),
) -> UserLoginResponse:
    settings = get_settings()
    normalized_phone = re.sub(r'\D', '', payload.whatsapp_phone) if payload.whatsapp_phone else None

    def _insert():
        client = _get_supabase(settings)
        row_data: dict = {
            'username': payload.username,
            'password_hash': hash_password(payload.password),
            'role': 'user',
        }
        if normalized_phone:
            row_data['whatsapp_phone'] = normalized_phone
        if payload.target_group_jid:
            row_data['target_group_jid'] = payload.target_group_jid
        return client.table(settings.supabase_users_table).insert(row_data).execute()

    try:
        result = await asyncio.to_thread(_insert)
    except Exception as exc:
        msg = str(exc).lower()
        if 'duplicate key' in msg or '409' in msg or 'already exists' in msg or 'unique' in msg:
            raise HTTPException(status_code=409, detail='Username or phone number already registered.')
        raise

    row = result.data[0]
    user_id = row['id']
    role = row['role']

    bridge_pool = getattr(request.app.state, 'bridge_pool', None)
    if bridge_pool is not None and normalized_phone:
        await asyncio.to_thread(bridge_pool.start_bridge, user_id, normalized_phone, payload.target_group_jid)

    access_token = create_access_token(
        user_id=user_id,
        username=payload.username,
        role=role,
        whatsapp_phone=normalized_phone,
        target_group_jid=payload.target_group_jid,
        settings=settings,
    )
    return UserLoginResponse(
        access_token=access_token,
        role=role,
        whatsapp_phone=normalized_phone,
    )


@router.post('/login', response_model=UserLoginResponse)
async def login(payload: UserLoginRequest) -> UserLoginResponse:
    settings = get_settings()

    def _fetch():
        client = _get_supabase(settings)
        return client.table(settings.supabase_users_table).select('*').eq('username', payload.username).single().execute()

    try:
        result = await asyncio.to_thread(_fetch)
    except Exception:
        raise HTTPException(status_code=401, detail='Invalid credentials.')

    row = result.data
    if not row or not row.get('is_active', False):
        raise HTTPException(status_code=401, detail='Invalid credentials.')

    if not verify_password(payload.password, row['password_hash']):
        raise HTTPException(status_code=401, detail='Invalid credentials.')

    access_token = create_access_token(
        user_id=str(row['id']),
        username=row['username'],
        role=row['role'],
        whatsapp_phone=row.get('whatsapp_phone'),
        target_group_jid=row.get('target_group_jid'),
        settings=settings,
    )
    return UserLoginResponse(
        access_token=access_token,
        role=row['role'],
        whatsapp_phone=row.get('whatsapp_phone'),
    )
