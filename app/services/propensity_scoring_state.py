from threading import Lock


class PropensityScoringStateService:
    def __init__(self, enabled: bool = True) -> None:
        self._lock = Lock()
        self._enabled = enabled

    def enable(self) -> None:
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled
