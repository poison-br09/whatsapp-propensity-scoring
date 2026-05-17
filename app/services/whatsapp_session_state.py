from dataclasses import asdict, dataclass
from threading import Lock

from app.models.whatsapp import WhatsAppSessionEventWebhook, WhatsAppSessionStatusResponse


@dataclass(slots=True)
class WhatsAppSessionState:
    status: str = 'starting'
    phone_number: str | None = None
    target_group_jid: str | None = None
    last_event_at: str | None = None
    last_disconnect_code: int | None = None
    pairing_required: bool = False


class WhatsAppSessionStateService:
    def __init__(self) -> None:
        self._lock = Lock()
        self._state = WhatsAppSessionState()

    def record_event(self, event: WhatsAppSessionEventWebhook) -> WhatsAppSessionStatusResponse:
        with self._lock:
            if event.phone_number is not None:
                self._state.phone_number = event.phone_number

            if event.target_group_jid is not None:
                self._state.target_group_jid = event.target_group_jid

            if event.occurred_at is not None:
                self._state.last_event_at = event.occurred_at

            if event.status_code is not None:
                self._state.last_disconnect_code = event.status_code

            if event.pairing_required is not None:
                self._state.pairing_required = event.pairing_required

            self._state.status = event.event
            return WhatsAppSessionStatusResponse(**asdict(self._state))

    def get_status(self) -> WhatsAppSessionStatusResponse:
        with self._lock:
            return WhatsAppSessionStatusResponse(**asdict(self._state))
