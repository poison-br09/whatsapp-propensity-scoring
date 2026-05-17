import logging
import sys
from pathlib import Path

from app.core.config import Settings

LOG_FORMAT = '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
UVICORN_LOGGERS = ('uvicorn', 'uvicorn.error', 'uvicorn.access')
QUIET_LOGGERS = ('httpx', 'httpcore', 'hpack')
LOGGER_NAME_ALIASES = {
    'uvicorn.error': 'uvicorn.lifecycle',
}


class FriendlyNameFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        original_name = record.name
        record.name = LOGGER_NAME_ALIASES.get(record.name, record.name)
        try:
            return super().format(record)
        finally:
            record.name = original_name


def _file_handler(path: Path, level: int) -> logging.FileHandler:
    handler = logging.FileHandler(path, encoding='utf-8')
    handler.setLevel(level)
    handler.setFormatter(FriendlyNameFormatter(LOG_FORMAT))
    return handler


def configure_logging(settings: Settings) -> None:
    log_dir = settings.app_log_path
    log_dir.mkdir(parents=True, exist_ok=True)

    app_log_path = log_dir / 'app.log'
    error_log_path = log_dir / 'error.log'

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(FriendlyNameFormatter(LOG_FORMAT))

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(_file_handler(app_log_path, logging.DEBUG))
    root_logger.addHandler(_file_handler(error_log_path, logging.ERROR))

    for logger_name in UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.setLevel(logging.INFO)
        uvicorn_logger.propagate = True

    for logger_name in QUIET_LOGGERS:
        noisy_logger = logging.getLogger(logger_name)
        noisy_logger.handlers.clear()
        noisy_logger.setLevel(logging.WARNING)
        noisy_logger.propagate = True
