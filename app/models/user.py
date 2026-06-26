from pydantic import BaseModel, Field


class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8)
    whatsapp_phone: str | None = None
    target_group_jid: str | None = None


class UserLoginRequest(BaseModel):
    username: str
    password: str


class UserLoginResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    role: str
    whatsapp_phone: str | None = None


class UserProfile(BaseModel):
    user_id: str
    username: str
    role: str
    whatsapp_phone: str | None
    target_group_jid: str | None
