import logging
import os
import sys
from datetime import datetime, timezone
from pythonjsonlogger.json import JsonFormatter

from datetime import timedelta

IST = timezone(timedelta(hours=5, minutes=30))


class _AppJsonFormatter(JsonFormatter):
    """
    Adds consistent fields to every log line for Grafana/Loki ingestion.
    """

    def add_fields(
        self, log_record: dict, record: logging.LogRecord, message_dict: dict
    ):
        super().add_fields(log_record, record, message_dict)

        log_record["timestamp"] = datetime.now(
            timezone.utc
        ).isoformat()  # for Grafana/Loki
        log_record["timestamp_ist"] = datetime.now(
            IST
        ).isoformat()  # for human reading on argo.
        log_record["logger"] = record.name
        log_record["file"] = record.pathname
        log_record["line"] = record.lineno
        log_record["function"] = record.funcName
        log_record["app"] = "scron"
        log_record["env"] = os.getenv("ENV", "development")

        log_record.pop("color_message", None)


def _setup():
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    os.makedirs("logs", exist_ok=True)

    formatter = _AppJsonFormatter(fmt="%(timestamp)s %(level)s %(logger)s %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    log_filename = f"logs/app_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_filename, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    if not root.handlers:
        root.addHandler(console_handler)
        root.addHandler(file_handler)

    # Silence noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


_setup()
