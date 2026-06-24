import logging
from pathlib import Path

from app.core.config import Settings
from app.core.whatsapp_bridge import BaileysBridgeProcessManager

logger = logging.getLogger(__name__)


class BridgePool:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bridges: dict[str, BaileysBridgeProcessManager] = {}

    def get(self, user_id: str) -> BaileysBridgeProcessManager | None:
        return self._bridges.get(user_id)

    def start_bridge(
        self,
        user_id: str,
        whatsapp_phone: str,
        target_group_jid: str | None,
    ) -> BaileysBridgeProcessManager:
        if user_id in self._bridges:
            return self._bridges[user_id]

        root = self._settings.project_root
        index = len(self._bridges)

        bridge = BaileysBridgeProcessManager(
            self._settings,
            phone_number=whatsapp_phone,
            group_jid=target_group_jid,
            auth_path=root / 'whatsapp_bridge' / f'baileys_auth_{user_id}',
            log_path=root / 'logs' / user_id,
            backfill_port=self._settings.backfill_control_port + index,
        )
        bridge.start()
        self._bridges[user_id] = bridge
        logger.info('Started bridge for user_id=%s phone=%s', user_id, whatsapp_phone)
        return bridge

    def stop_bridge(self, user_id: str) -> None:
        bridge = self._bridges.pop(user_id, None)
        if bridge:
            bridge.stop()
            logger.info('Stopped bridge for user_id=%s', user_id)

    def stop_all(self) -> None:
        for user_id in list(self._bridges):
            self.stop_bridge(user_id)

    def all_bridges(self) -> dict[str, BaileysBridgeProcessManager]:
        return dict(self._bridges)
