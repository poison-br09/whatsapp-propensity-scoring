import logging
import os
import subprocess
from pathlib import Path

from app.core.config import Settings

Logger = logging.getLogger
logger = Logger(__name__)


class BaileysBridgeProcessManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._process: subprocess.Popen[str] | None = None
        self._stdout_handle = None
        self._stderr_handle = None

    def start(self) -> None:
        if not self._settings.whatsapp_bridge_enabled:
            logger.info('WhatsApp bridge startup skipped because WHATSAPP_BRIDGE_ENABLED is false')
            return

        if not self._settings.whatsapp_group_jid and not self._settings.whatsapp_group_name:
            logger.warning('WhatsApp bridge startup skipped because neither WHATSAPP_GROUP_JID nor WHATSAPP_GROUP_NAME is configured')
            return

        if not self._settings.whatsapp_internal_token:
            logger.warning('WhatsApp bridge startup skipped because WHATSAPP_INTERNAL_TOKEN is missing')
            return

        bridge_dir = self._settings.baileys_project_path
        entrypoint = bridge_dir / 'poll-bridge.ts'
        tsx_cli = bridge_dir / 'node_modules' / 'tsx' / 'dist' / 'cli.mjs'
        if not bridge_dir.exists() or not entrypoint.exists() or not tsx_cli.exists():
            logger.warning(
                'WhatsApp bridge startup skipped because bridge files are missing project_dir=%s',
                bridge_dir,
            )
            return

        auth_dir = str(self._settings.baileys_auth_path or (bridge_dir / 'baileys_auth_info'))
        log_dir = self._settings.baileys_log_path
        log_dir.mkdir(parents=True, exist_ok=True)
        server_log_path = log_dir / 'server.log'
        error_log_path = log_dir / 'error.log'

        env = os.environ.copy()
        env.update(
            {
                'TARGET_GROUP_JID': self._settings.whatsapp_group_jid or '',
                'TARGET_GROUP_NAME': self._settings.whatsapp_group_name or '',
                'USE_PAIRING_CODE': 'true' if self._settings.whatsapp_use_pairing_code else 'false',
                'PHONE_NUMBER': self._settings.whatsapp_phone_number or '',
                'PYTHON_INTERNAL_BASE_URL': self._settings.whatsapp_internal_base_url,
                'PYTHON_INTERNAL_TOKEN': self._settings.whatsapp_internal_token,
                'AUTH_DIR': auth_dir,
                'LOG_LEVEL': self._settings.baileys_log_level,
                'PROTOCOL_LOG_LEVEL': self._settings.baileys_protocol_log_level,
            }
        )

        self._stdout_handle = server_log_path.open('a', encoding='utf-8')
        self._stderr_handle = error_log_path.open('a', encoding='utf-8')

        command = ['node', str(tsx_cli), str(entrypoint)]
        logger.info('Starting WhatsApp bridge command=%s cwd=%s server_log=%s error_log=%s', command, bridge_dir, server_log_path, error_log_path)
        self._process = subprocess.Popen(
            command,
            cwd=bridge_dir,
            env=env,
            stdout=self._stdout_handle,
            stderr=self._stderr_handle,
            text=True,
        )

    def stop(self) -> None:
        if self._process is None:
            self._close_log_handles()
            return

        if self._process.poll() is not None:
            self._close_log_handles()
            return

        logger.info('Stopping WhatsApp bridge pid=%s', self._process.pid)
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning('Force killing WhatsApp bridge pid=%s', self._process.pid)
            self._process.kill()
            self._process.wait(timeout=5)
        finally:
            self._close_log_handles()

    def _close_log_handles(self) -> None:
        if self._stdout_handle is not None:
            self._stdout_handle.close()
            self._stdout_handle = None

        if self._stderr_handle is not None:
            self._stderr_handle.close()
            self._stderr_handle = None
