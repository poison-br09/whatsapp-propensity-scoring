from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    api_key: str | None = None
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    supabase_poll_history_rpc: str = "get_poll_user_history"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
