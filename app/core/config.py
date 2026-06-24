from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    api_key: str | None = None
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    supabase_poll_history_rpc: str = 'get_poll_user_history'
    supabase_whatsapp_poll_table: str = 'whatsapp_poll'
    supabase_whatsapp_vote_event_table: str = 'whatsapp_poll_vote_event'
    supabase_whatsapp_vote_snapshot_table: str = 'whatsapp_poll_vote_snapshot'
    supabase_whatsapp_keyword_match_table: str = 'whatsapp_keyword_match'
    supabase_whatsapp_keywords_table: str = 'whatsapp_keywords'
    whatsapp_internal_token: str | None = None
    whatsapp_internal_base_url: str = 'http://127.0.0.1:8000'
    whatsapp_bridge_enabled: bool = False
    whatsapp_group_jid: str | None = None
    whatsapp_group_name: str | None = None
    whatsapp_use_pairing_code: bool = False
    whatsapp_phone_number: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    whatsapp_alert_recipients: str | None = None
    app_log_dir: str = 'logs/app'
    baileys_project_dir: str = 'whatsapp_bridge'
    baileys_auth_dir: str | None = None
    baileys_log_level: str = 'trace'
    baileys_protocol_log_level: str = 'error'
    baileys_log_dir: str = 'logs/server'
    backfill_control_port: int = 8001
    jwt_secret: str | None = None
    jwt_expire_hours: int = 24
    supabase_users_table: str = 'users'

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def resolve_path(self, value: str | None) -> Path | None:
        if not value:
            return None

        path = Path(value)
        if path.is_absolute():
            return path

        return self.project_root / path

    @property
    def app_log_path(self) -> Path:
        return self.resolve_path(self.app_log_dir) or self.project_root / 'logs' / 'app'

    @property
    def baileys_project_path(self) -> Path:
        return self.resolve_path(self.baileys_project_dir) or self.project_root / 'whatsapp_bridge'

    @property
    def baileys_auth_path(self) -> Path | None:
        return self.resolve_path(self.baileys_auth_dir)

    @property
    def baileys_log_path(self) -> Path:
        return self.resolve_path(self.baileys_log_dir) or self.project_root / 'logs' / 'server'

    @property
    def whatsapp_alert_recipient_list(self) -> list[str]:
        if not self.whatsapp_alert_recipients:
            return []

        return [email.strip() for email in self.whatsapp_alert_recipients.split(',') if email.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
