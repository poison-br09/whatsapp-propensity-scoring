from datetime import datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.models.user import UserProfile

_bearer_scheme = HTTPBearer()


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(
    user_id: str,
    username: str,
    role: str,
    whatsapp_phone: str | None,
    target_group_jid: str | None,
    settings: Settings,
) -> str:
    payload = {
        'sub': user_id,
        'username': username,
        'role': role,
        'phone': whatsapp_phone,
        'group_jid': target_group_jid,
        'exp': datetime.utcnow() + timedelta(hours=settings.jwt_expire_hours),
    }
    return jwt.encode(payload, settings.jwt_secret or '', algorithm='HS256')


def decode_access_token(token: str, settings: Settings) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret or '', algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail='Token has expired.')
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail='Invalid token.')


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> UserProfile:
    payload = decode_access_token(credentials.credentials, settings)
    return UserProfile(
        user_id=payload['sub'],
        username=payload['username'],
        role=payload['role'],
        whatsapp_phone=payload['phone'],
        target_group_jid=payload.get('group_jid'),
    )


def require_superadmin(
    current_user: UserProfile = Depends(get_current_user),
) -> UserProfile:
    if current_user.role != 'superadmin':
        raise HTTPException(status_code=403, detail='Superadmin access required.')
    return current_user
