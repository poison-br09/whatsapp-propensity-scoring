import logging
import secrets

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.auth import get_current_user, require_admin, require_superadmin
from app.core.config import Settings, get_settings
from app.models.user import UserProfile
from app.repositories.supabase_poll_repository import SupabasePollRepository

__all__ = [
    'require_api_key',
    'get_poll_repository',
    'get_current_user',
    'require_admin',
    'require_superadmin',
    'UserProfile',
]

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(
    name='x-api-key',
    auto_error=False,
    description='API key required for protected endpoints.',
)


def require_api_key(
    x_api_key: str | None = Security(api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.api_key:
        logger.error('API key protection is not configured')
        raise HTTPException(status_code=500, detail='API key is not configured.')

    if x_api_key is None:
        raise HTTPException(status_code=401, detail='Missing x-api-key header.')

    if not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail='Invalid API key.')


def get_poll_repository(
    settings: Settings = Depends(get_settings),
) -> SupabasePollRepository:
    return SupabasePollRepository(settings)
