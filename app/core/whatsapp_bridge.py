import logging
import os
import signal
import shutil
import subprocess
import time
from json import JSONDecodeError, loads
from pathlib import Path

from app.core.config import Settings

Logger = logging.getLogger
logger = Logger(__name__)


class BaileysBridgeProcessManager:
    def __init__(
        self,
        settings: Settings,
        *,
        phone_number: str | None = None,
        group_jid: str | None = None,
        group_name: str | None = None,
        auth_path: Path | None = None,
        log_path: Path | None = None,
        backfill_port: int | None = None,
    ) -> None:
        self._settings = settings
        self._phone_number = phone_number
        self._group_jid = group_jid
        self._group_name = group_name
        self._auth_path = auth_path
        self._log_path = log_path
        self._backfill_port = backfill_port
        self._process: subprocess.Popen[str] | None = None
        self._stdout_handle = None
        self._stderr_handle = None

    def start(self) -> None:
        if not self._settings.whatsapp_bridge_enabled:
            logger.info('WhatsApp bridge startup skipped because WHATSAPP_BRIDGE_ENABLED is false')
            return

        if not self._settings.whatsapp_internal_token:
            logger.warning('WhatsApp bridge startup skipped because WHATSAPP_INTERNAL_TOKEN is missing')
            return

        self._start_process(
            phone_number=self._phone_number or self._settings.whatsapp_phone_number,
            use_pairing_code=self._settings.whatsapp_use_pairing_code,
            reset_session=False,
            pairing_code_output_path=None,
        )

    def request_pairing_code(self, phone_number: str, timeout_seconds: int = 30) -> str:
        normalized_phone_number = ''.join(ch for ch in phone_number if ch.isdigit())
        if not normalized_phone_number:
            raise ValueError('Phone number must include digits.')

        log_dir = self._log_path or self._settings.baileys_log_path
        pairing_code_path = log_dir / 'pairing-code.json'
        self.stop()
        self._start_process(
            phone_number=normalized_phone_number,
            use_pairing_code=True,
            reset_session=True,
            pairing_code_output_path=pairing_code_path,
        )

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError('WhatsApp bridge exited before producing a pairing code.')

            if pairing_code_path.exists():
                try:
                    payload = loads(pairing_code_path.read_text(encoding='utf-8'))
                except JSONDecodeError:
                    time.sleep(0.5)
                    continue

                code = payload.get('code')
                if isinstance(code, str) and code:
                    logger.info('Received WhatsApp pairing code for phone_number=%s', normalized_phone_number)
                    return code

            time.sleep(0.5)

        raise TimeoutError('Timed out waiting for WhatsApp pairing code.')

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

    def _start_process(
        self,
        *,
        phone_number: str | None,
        use_pairing_code: bool,
        reset_session: bool,
        pairing_code_output_path: Path | None,
    ) -> None:
        self.stop()

        bridge_dir = self._settings.baileys_project_path
        entrypoint = bridge_dir / 'poll-bridge.ts'
        tsx_cli = bridge_dir / 'node_modules' / 'tsx' / 'dist' / 'cli.mjs'
        if not bridge_dir.exists() or not entrypoint.exists() or not tsx_cli.exists():
            raise RuntimeError(f'WhatsApp bridge files are missing under {bridge_dir}')

        self._terminate_stale_bridge_processes(entrypoint)

        auth_path = self._auth_path or self._settings.baileys_auth_path or (bridge_dir / 'baileys_auth_info')
        if reset_session and auth_path.exists():
            for item in auth_path.iterdir():
                shutil.rmtree(item) if item.is_dir() else item.unlink()

        log_dir = self._log_path or self._settings.baileys_log_path
        log_dir.mkdir(parents=True, exist_ok=True)
        server_log_path = log_dir / 'server.log'
        error_log_path = log_dir / 'error.log'

        if pairing_code_output_path is not None and pairing_code_output_path.exists():
            pairing_code_output_path.unlink()

        env = os.environ.copy()
        env.update(
            {
                'TARGET_GROUP_JID': self._group_jid or self._settings.whatsapp_group_jid or '',
                'TARGET_GROUP_NAME': self._group_name or self._settings.whatsapp_group_name or '',
                'USE_PAIRING_CODE': 'true' if use_pairing_code else 'false',
                'PHONE_NUMBER': phone_number or '',
                'PYTHON_INTERNAL_BASE_URL': self._settings.whatsapp_internal_base_url,
                'PYTHON_INTERNAL_TOKEN': self._settings.whatsapp_internal_token or '',
                'AUTH_DIR': str(auth_path),
                'LOG_LEVEL': self._settings.baileys_log_level,
                'PROTOCOL_LOG_LEVEL': self._settings.baileys_protocol_log_level,
                'PAIRING_CODE_OUTPUT_PATH': str(pairing_code_output_path) if pairing_code_output_path else '',
                'BACKFILL_CONTROL_PORT': str(self._backfill_port or self._settings.backfill_control_port),
            }
        )

        self._stdout_handle = server_log_path.open('a', encoding='utf-8')
        self._stderr_handle = error_log_path.open('a', encoding='utf-8')

        command = ['node', str(tsx_cli), str(entrypoint)]
        logger.info(
            'Starting WhatsApp bridge command=%s cwd=%s server_log=%s error_log=%s use_pairing_code=%s reset_session=%s',
            command,
            bridge_dir,
            server_log_path,
            error_log_path,
            use_pairing_code,
            reset_session,
        )
        self._process = subprocess.Popen(
            command,
            cwd=bridge_dir,
            env=env,
            stdout=self._stdout_handle,
            stderr=self._stderr_handle,
            text=True,
        )

    def _terminate_stale_bridge_processes(self, entrypoint: Path) -> None:
        try:
            result = subprocess.run(
                ['pgrep', '-f', str(entrypoint)],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            logger.warning('Unable to inspect stale WhatsApp bridge processes: %s', error)
            return

        if result.returncode not in {0, 1}:
            logger.warning(
                'Failed to inspect stale WhatsApp bridge processes returncode=%s stderr=%s',
                result.returncode,
                result.stderr.strip(),
            )
            return

        current_pid = os.getpid()
        stale_pids = []
        for raw_pid in result.stdout.splitlines():
            raw_pid = raw_pid.strip()
            if not raw_pid:
                continue

            try:
                pid = int(raw_pid)
            except ValueError:
                continue

            if pid == current_pid:
                continue

            if self._process is not None and self._process.pid == pid:
                continue

            stale_pids.append(pid)

        for pid in stale_pids:
            try:
                os.kill(pid, signal.SIGTERM)
                logger.info('Terminated stale WhatsApp bridge pid=%s', pid)
            except ProcessLookupError:
                continue
            except OSError as error:
                logger.warning('Failed to terminate stale WhatsApp bridge pid=%s error=%s', pid, error)
