import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler

# ---- IST timezone (UTC+5:30) ----
IST = timezone(timedelta(hours=5, minutes=30))


# ---- ANSI color codes for terminal output ----
class _Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # Log level colors
    DEBUG = "\033[36m"  # Cyan
    INFO = ""  # Bright white
    WARNING = "\033[33m"  # Yellow
    ERROR = "\033[31m"  # Red
    CRITICAL = "\033[35m"  # Magenta

    # Metadata colors
    TIMESTAMP = "\033[90m"  # Dark gray
    NAME = "\033[96m"  # Light cyan


# ---- Human-readable colored formatter for terminal ----
class ISTColorFormatter(logging.Formatter):
    """
    Terminal formatter:
      - Timestamps converted to IST (UTC+5:30)
      - ANSI colors per log level
      - Shows filename and line number
    """

    LEVEL_COLORS = {
        logging.DEBUG: _Colors.DEBUG,
        logging.INFO: _Colors.INFO,
        logging.WARNING: _Colors.WARNING,
        logging.ERROR: _Colors.ERROR,
        logging.CRITICAL: _Colors.CRITICAL,
    }

    LEVEL_LABELS = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def __init__(self, use_color: bool = True):
        super().__init__()
        self.use_color = use_color

    def formatTime(self, record: logging.LogRecord, datefmt=None) -> str:  # noqa: N802
        ist_dt = datetime.fromtimestamp(record.created, tz=IST)
        return ist_dt.strftime("%Y-%m-%d %H:%M:%S IST")

    def format(self, record: logging.LogRecord) -> str:
        level_color = self.LEVEL_COLORS.get(record.levelno, _Colors.RESET)
        label = self.LEVEL_LABELS.get(record.levelno, record.levelname)

        timestamp = self.formatTime(record)
        filename = record.filename
        lineno = record.lineno
        logger_name = record.name

        if self.use_color:
            ts_part = f"{_Colors.TIMESTAMP}{timestamp}{_Colors.RESET}"
            level_part = f"{_Colors.BOLD}{level_color}{label:<8}{_Colors.RESET}"
            file_part = f"{_Colors.DIM}{filename}:{lineno}{_Colors.RESET}"
            name_part = f"{_Colors.NAME}{logger_name}{_Colors.RESET}"
            msg_part = f"{level_color}{record.getMessage()}{_Colors.RESET}"
        else:
            ts_part = timestamp
            level_part = label.ljust(8)
            file_part = f"{filename}:{lineno}"
            name_part = logger_name
            msg_part = record.getMessage()

        formatted = f"[{ts_part}] [{level_part}] [{file_part}] {name_part} - {msg_part}"

        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            formatted += "\n" + self.formatStack(record.stack_info)

        return formatted


# ---- Structured JSON formatter for log files ----
class JSONFormatter(logging.Formatter):
    """
    File formatter: emits one JSON object per line.
    Fields: timestamp, level, logger, file, line, function, message,
            and optionally exc_info / stack_info.
    """

    def formatTime(self, record: logging.LogRecord, datefmt=None) -> str:  # noqa: N802
        ist_dt = datetime.fromtimestamp(record.created, tz=IST)
        return ist_dt.isoformat()

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "file": record.filename,
            "line": record.lineno,
            "function": record.funcName,
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_entry["exc_info"] = self.formatException(record.exc_info)

        if record.stack_info:
            log_entry["stack_info"] = self.formatStack(record.stack_info)

        # Attach any extra fields passed via `extra={}` on the log call
        standard_keys = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in standard_keys:
                try:
                    json.dumps(value)  # only include JSON-serialisable extras
                    log_entry[key] = value
                except (TypeError, ValueError):
                    log_entry[key] = str(value)

        return json.dumps(log_entry, ensure_ascii=False)


# ---- Formatter instances ----
color_formatter = ISTColorFormatter(use_color=True)
plain_formatter = ISTColorFormatter(use_color=False)  # kept for compatibility
json_formatter = JSONFormatter()


# ---- Root / named-logger bootstrap ----
def get_logger(name: str) -> logging.Logger:
    """
    Return a logger for *name* (typically __name__).

    The first call also configures the root logger so that:
      - stdout  → human-readable colored output  (INFO+)
      - any per-document file handler → JSON output (DEBUG+)
    """
    _bootstrap_root_logger()
    return logging.getLogger(name)


_root_bootstrapped = False


def _bootstrap_root_logger() -> None:
    global _root_bootstrapped
    if _root_bootstrapped:
        return
    _root_bootstrapped = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Remove pre-existing handlers to avoid duplicates
    root.handlers.clear()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(color_formatter)
    root.addHandler(stdout_handler)

    # Third-party library noise suppression
    logging.getLogger("celery").setLevel(logging.INFO)
    logging.getLogger("celery.task").setLevel(logging.DEBUG)
    logging.getLogger("celery.worker").setLevel(logging.INFO)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("gunicorn").setLevel(logging.INFO)
    logging.getLogger("redis").setLevel(logging.WARNING)
    logging.getLogger("kombu").setLevel(logging.WARNING)
    logging.getLogger("weasyprint").setLevel(logging.CRITICAL)
    logging.getLogger("weasyprint.progress").setLevel(logging.CRITICAL)
    logging.getLogger("fontTools").setLevel(logging.CRITICAL)

    _log_startup_banner()


def _log_startup_banner() -> None:
    log = logging.getLogger("Digitization")
    environment = os.environ.get("ENVIRONMENT", "unknown")
    log.info("=" * 80)
    log.info("🚀 Logging initialized for: caf_data_digitization")
    log.info(f"   Environment : {environment}")
    log.info(f"   Process ID  : {os.getpid()}")
    log.info("   Log Level   : DEBUG (file) / INFO (stdout)")
    log.info("   stdout      : human-readable colored")
    log.info("   file        : structured JSON (per-document)")
    log.info("   Timezone    : IST (UTC+5:30)")
    log.info("=" * 80)


# ---- Per-document rotating JSON file handlers ----
_document_file_handlers: dict[str, RotatingFileHandler] = {}


def setup_document_file_handler(document_id: str) -> None:
    """
    Attach a per-document rotating file handler to the root logger.
    Writes structured JSON — one object per line.
    Only active in sandbox/testing environments.

    Args:
        document_id: Used as the log filename (<document_id>.log)
    """
    from app.common.constants import constants  # local import to avoid circular deps

    remove_document_file_handler(document_id)

    resolved_log_dir = constants.LOG_DIR
    os.makedirs(resolved_log_dir, exist_ok=True)

    log_file_path = os.path.join(resolved_log_dir, f"{document_id}.log")

    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=10 * 1024 * 1024,  # 10 MB per file
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(json_formatter)  # ← JSON in files

    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    _document_file_handlers[document_id] = file_handler

    logging.getLogger("Digitization").info(
        f"[Logging] JSON file handler attached: document_id={document_id} "
        f"→ {os.path.abspath(log_file_path)}"
    )


def remove_document_file_handler(document_id: str) -> None:
    """
    Detach and close the file handler for document_id.
    Call after document processing is complete.
    """
    handler = _document_file_handlers.pop(document_id, None)
    if handler:
        logging.getLogger().removeHandler(handler)
        handler.close()
        logging.getLogger("Digitization").debug(
            f"[Logging] JSON file handler removed: document_id={document_id}"
        )


# ---- Structured helper functions ----


def log_task_start(task_name: str, **kwargs) -> None:
    log = logging.getLogger("Digitization")
    log.info("=" * 80)
    log.info(f"🚀 TASK STARTED: {task_name}")
    for key, value in kwargs.items():
        log.info(f"   {key}: {value}")
    log.info("=" * 80)


def log_task_end(task_name: str, duration: float = None, **kwargs) -> None:
    log = logging.getLogger("Digitization")
    log.info("=" * 80)
    log.info(f"✅ TASK COMPLETED: {task_name}")
    if duration is not None:
        log.info(f"   Duration: {duration:.2f}s")
    for key, value in kwargs.items():
        log.info(f"   {key}: {value}")
    log.info("=" * 80)


def log_task_error(task_name: str, error: Exception, **kwargs) -> None:
    log = logging.getLogger("Digitization")
    log.error("=" * 80)
    log.error(f"❌ TASK FAILED: {task_name}")
    log.error(f"   Error: {type(error).__name__}: {str(error)}")
    for key, value in kwargs.items():
        log.error(f"   {key}: {value}")
    log.error("=" * 80)
    log.error(f"Full Traceback:\n{traceback.format_exc()}")
    log.error("=" * 80)


# ---- Context manager for timed operations ----
class LogTimer:
    """Context manager that logs the duration of a block."""

    def __init__(self, operation_name: str, log_level: int = logging.INFO):
        self.operation_name = operation_name
        self.log_level = log_level
        self.start_time: datetime | None = None
        self._log = logging.getLogger("Digitization")

    def __enter__(self):
        self.start_time = datetime.now()
        self._log.log(self.log_level, f"⏱️  Starting: {self.operation_name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (datetime.now() - self.start_time).total_seconds()
        if exc_type is None:
            self._log.log(
                self.log_level,
                f"✅ Completed: {self.operation_name} ({duration:.2f}s)",
            )
        else:
            self._log.error(
                f"❌ Failed: {self.operation_name} ({duration:.2f}s) "
                f"- {exc_type.__name__}: {exc_val}"
            )
        return False  # don't suppress exceptions
