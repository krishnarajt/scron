import logging
import os
import sys
from datetime import datetime, timezone
from pythonjsonlogger.json import JsonFormatter


class AppJsonFormatter(JsonFormatter):
    """
    Custom JSON formatter that adds consistent fields to every log line.
    Designed for ingestion by Grafana Loki / Promtail / any log aggregator.
    """

    def add_fields(
        self, log_record: dict, record: logging.LogRecord, message_dict: dict
    ):
        super().add_fields(log_record, record, message_dict)

        # Timestamp in ISO 8601 UTC — Loki/Grafana expects this
        log_record["timestamp"] = datetime.now(timezone.utc).isoformat()

        # Standard level field (uppercase)
        log_record["level"] = record.levelname

        # Source location — file, line, function
        log_record["logger"] = record.name
        log_record["file"] = record.pathname
        log_record["line"] = record.lineno
        log_record["function"] = record.funcName

        # App metadata — useful for filtering in Grafana
        log_record["app"] = "scron"
        log_record["env"] = os.getenv("ENV", "development")

        # Remove default fields that are redundant or ugly in JSON
        log_record.pop("color_message", None)


def _build_logger() -> logging.Logger:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    numeric_level = getattr(logging, log_level, logging.INFO)

    # Create logs directory
    os.makedirs("logs", exist_ok=True)

    formatter = AppJsonFormatter(
        fmt="%(timestamp)s %(level)s %(name)s %(message)s",
    )

    # Console handler — JSON to stdout (for Docker / Grafana Loki scraping)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # File handler — daily log file, also JSON
    log_filename = f"logs/app_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_filename, encoding="utf-8")
    file_handler.setFormatter(formatter)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Avoid adding duplicate handlers if this is called multiple times
    if not root_logger.handlers:
        root_logger.addHandler(console_handler)
        root_logger.addHandler(file_handler)

    # Silence noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return root_logger


# Build once at import time
_build_logger()


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger. Use __name__ as the name so log lines
    show the exact module they came from.

    Usage:
        from app.utils.logging_utils import get_logger
        logger = get_logger(__name__)
    """
    return logging.getLogger(name)


# Convenience default logger for quick imports
# from app.utils.logging_utils import logger
logger = get_logger("scron")
