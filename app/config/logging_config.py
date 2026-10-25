"""Centralised logging configuration

This module provides:
- JSONFormatter: Structured JSON logs
- ConsoleFormatter: Human-readable for development (with pretty colours)
- SensitiveDataFilter: Masks passwords, tokens, connection strings
- RecordPreservingQueueHandler: QueueHandler that keeps exception details and request context
- configure_logging(): this just sets up the Queue handler for asyncio-safe logging
"""

import copy
import json
import logging
import logging.config
import logging.handlers
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from queue import Queue
from uuid import UUID

from app.config.settings import LOG_FORMATS, canonical_choice


def wants_colour(stream: object) -> bool:
    """Whether ANSI colour codes belong on ``stream``: it is a terminal and ``NO_COLOR`` is not set.

    A pipe, a file or a log collector would otherwise show the escape sequences as text. ``NO_COLOR``
    (https://no-color.org) turns colour off whatever the stream is: any non-empty value counts.
    """
    if os.environ.get("NO_COLOR"):
        return False
    # bool() is False: a stream without isatty is not a terminal
    isatty = getattr(stream, "isatty", bool)
    return bool(isatty())


class ConsoleFormatter(logging.Formatter):
    """Console formatter with Pretty ANSI colour codes for development.

    Colours log levels for easy visual scanning, when ``colour`` is true (see ``wants_colour``):
    - DEBUG: Cyan
    - INFO: Green
    - WARNING: Yellow
    - ERROR: Red
    - CRITICAL: Magenta
    """

    COLOURS = {
        "DEBUG": "\033[36m",   # Cyan
        "INFO": "\033[32m",    # Green
        "WARNING": "\033[33m", # Yellow
        "ERROR": "\033[31m",   # Red
        "CRITICAL": "\033[35m", # Magenta
     }
    RESET = "\033[0m"

    def __init__(self, *args, colour: bool = True, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.colour = colour

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record with coloured level name."""
        if not self.colour:
            return super().format(record)
        colour = self.COLOURS.get(record.levelname, self.RESET)

        levelname_coloured = f"{colour}{record.levelname}{self.RESET}"

        original_levelname = record.levelname
        record.levelname = levelname_coloured

        formatted = super().format(record)

        record.levelname = original_levelname

        return formatted

# The attributes every LogRecord has; anything else on a record came from ``extra={...}`` or from the queue handler.
_RECORD_ATTRIBUTES = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}


class SensitiveDataFilter(logging.Filter):
    """Filter to mask sensitive data in log messages.

    Patterns masked:
    - Passwords (password=xxx, "password": "xxx")
    - Tokens (token=xxx, "token": "xxx", Bearer <credential>)
    - API keys (api-key=xxx, "api_key": "xxx")
    - URLs that carry credentials (hides user:password)

    A value is only masked when it follows a key/value separator (``=`` or ``:``), or a
    ``Bearer`` that is followed by something credential-shaped, so ordinary prose such as
    "Token cache disabled" or "Validating Bearer token via X" is left alone.
    """

    SENSITIVE_PATTERNS = [
        # password=something or password="something" or "password":"something"
        (re.compile(r'password["\']?\s*[:=]\s*["\']?([^"\s,}]+)["\']?', re.IGNORECASE), "password=***"),
        # token=something or "token":"something"
        (re.compile(r'token["\']?\s*[:=]\s*["\']?([^"\s,}]+)["\']?', re.IGNORECASE), "token=***"),
        # Authorization: Bearer xxx (any value after the header name)
        (
            re.compile(r'(authorization["\']?\s*[:=]\s*["\']?)bearer\s+[^\s,}"\']+', re.IGNORECASE),
            r"\1bearer ***",
        ),
        # bearer <credential>: needs a digit or 16+ token characters, so "Bearer token" survives
        (
            re.compile(
                r"\bbearer\s+(?=[A-Za-z0-9._~+/=-]*\d|[A-Za-z0-9._~+/=-]{16})[A-Za-z0-9._~+/=-]+",
                re.IGNORECASE,
            ),
            "bearer ***",
        ),
        # api-key=something, "api_key": "something" (the Qdrant API key travels in the api-key header)
        (re.compile(r'api[-_]?key["\']?\s*[:=]\s*["\']?([^"\s,}]+)["\']?', re.IGNORECASE), "api-key=***"),
        # URL with credentials: scheme://user:password@host. Both parts stop at whitespace and are bounded, so a
        # hostile text of "://a:" repeated cannot make the scan of each "://" run to the end of the message (that
        # was quadratic and froze the event loop). The user stops at the characters that end a URL's authority; the
        # password may hold "/", "?" and "#", which a generated password often does and which are masked all the same.
        (re.compile(r"://[^\s:/?#@]{1,256}:[^\s@]{1,4096}@", re.IGNORECASE), "://***:***@"),
    ]

    # Text longer than this is shortened before it is scanned (its head and its tail stay): a log line of
    # this size is a mistake or an attack, and the scan cost grows with the text.
    MAX_TEXT_LENGTH = 32_768
    _MAX_DEPTH = 4

    def filter(self, record: logging.LogRecord) -> bool:
        """Mask sensitive data in the message, the arguments, the traceback and the ``extra`` fields.

        Returns True to allow the log through (after modification).
        """
        if isinstance(record.msg, str):
            record.msg = self._mask_value(record.msg)

        self._mask_args(record)
        self._mask_exception_text(record)
        self._mask_extra_fields(record)

        return True

    def _mask_extra_fields(self, record: logging.LogRecord) -> None:
        """Mask what ``logger.info(..., extra={...})`` put on the record: the JSON formatter writes all of it."""
        for key in set(vars(record)) - _RECORD_ATTRIBUTES:
            setattr(record, key, self._mask_value(getattr(record, key)))

    def _mask_args(self, record: logging.LogRecord) -> None:
        """Mask the %-style arguments of a record, whether passed as a dict or a tuple"""
        if not record.args:
            return
        if isinstance(record.args, tuple):
            record.args = tuple(self._mask_value(arg) for arg in record.args)
        else:
            record.args = {
                k: self._mask_value(v) for k, v in record.args.items()
            }

    def _mask_exception_text(self, record: logging.LogRecord) -> None:
        """Render and mask a record's traceback

        Tracebacks are rendered by the handler's formatter after this filter has run, so
        render and mask them here (the formatter reuses a cached ``exc_text``).
        """
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._mask_value(record.exc_text)

    def _mask_value(self, value, depth: int = 0):
        """Mask a string, or the strings inside a list, tuple or dict; anything else is returned as it is"""
        if isinstance(value, str):
            value = self._shorten(value)
            for pattern, replacement in self.SENSITIVE_PATTERNS:
                value = pattern.sub(replacement, value)
        elif depth < self._MAX_DEPTH:
            if isinstance(value, list | tuple):
                return type(value)(self._mask_value(item, depth + 1) for item in value)
            if isinstance(value, dict):
                return {key: self._mask_value(item, depth + 1) for key, item in value.items()}
        return value

    @classmethod
    def _shorten(cls, text: str) -> str:
        """``text`` itself, or its head and its tail with a note of how much was left out"""
        if len(text) <= cls.MAX_TEXT_LENGTH:
            return text
        half = cls.MAX_TEXT_LENGTH // 2
        return f"{text[:half]} ...[{len(text) - 2 * half} characters left out]... {text[-half:]}"


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging.

    Outputs each log record as a single line of JSON with fields:
    - timestamp: ISO 8601 UTC timestamp
    - level: Log Level
    - logger: Logger name (module path)
    - message: Log message
    - module, function, line: source code location
    - exception: Stack trace (if present)
    - All extra={} fields from logger calls
    - request_id, user_id: from contextvars (if set)
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as JSON."""
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = record.exc_text or self.formatException(record.exc_info)

        # Add extra fields from logger.info(..., extra={...})
        # Standard LogRecord attributes to skip
        standard_attrs = {
            "name", "msg", "args", "created", "filename", "funcName", "levelname",
            "levelno", "lineno", "module", "msecs", "message", "pathname", "process",
            "processName", "relativeCreated", "thread", "threadName", "exc_info",
            "exc_text", "stack_info", "getMessage", "taskName",
        }

        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                log_data[key] = value

        # Add contextvars (request_id, user_id) if available. Records that went through the
        # QueueHandler already carry them as attributes (the listener thread cannot see the
        # emitting request's context), so this covers direct, same-thread use of the formatter.
        log_data.update(_current_context_ids())

        return json.dumps(log_data, default=_serialise_log_value)


def _current_context_ids() -> dict[str, str]:
    """Return the request_id / user_id of the current context, omitting unset ones."""
    # Import here to avoid circular dependency
    from app.middleware.logging_middleware import get_request_id, get_user_id

    ids = {"request_id": get_request_id(), "user_id": get_user_id()}
    return {key: value for key, value in ids.items() if value}


class RecordPreservingQueueHandler(logging.handlers.QueueHandler):
    """QueueHandler that hands the listener thread a faithful copy of the record.

    The stock ``QueueHandler.prepare()`` formats the record with a default formatter, bakes the
    traceback into ``msg`` and clears ``exc_info``, so a downstream ``JSONFormatter`` can never
    emit its ``exception`` field. It also runs ``format()`` in the listener thread, where the
    emitting request's contextvars are not visible.

    Here the message is merged in the emitting thread (arguments are evaluated there, not
    later), ``exc_info`` / ``stack_info`` are kept, and the request context is copied onto the
    record. The copy keeps the listener thread's filters from mutating a record that other
    root handlers still see.
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        """Copy the record for the queue, keeping its exception details."""
        prepared = copy.copy(record)
        prepared.msg = record.getMessage()
        prepared.args = None
        for key, value in _current_context_ids().items():
            prepared.__dict__.setdefault(key, value)
        return prepared

def _serialise_log_value(obj):
    """Handle non-JSON-serialisable types"""
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")


# Global listener instance (stored for cleanup)
_queue_listener: logging.handlers.QueueListener | None = None


def _stderr_handler(log_format: str) -> logging.StreamHandler:
    """A handler that writes masked records to stderr, which never interferes with the stdio transport."""
    formatter: logging.Formatter
    if log_format == "json":
        formatter = JSONFormatter()
    else:
        formatter = ConsoleFormatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            colour=wants_colour(sys.stderr),
        )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    handler.addFilter(SensitiveDataFilter())
    return handler


@contextmanager
def logging_while_starting(log_level: str, log_format: str) -> Iterator[None]:
    """Show log records on stderr while the block runs, for work that happens before ``configure_logging``.

    The server configures its queued logging inside the lifespan, after the TensorRT models have started, so
    without this their start-up lines (a model fetch, an engine build, the pruned engine directories) would be
    dropped. Every other handler and the root level are left as they were.
    """
    log_format = canonical_choice("LOG_FORMAT", log_format, LOG_FORMATS)
    root = logging.getLogger()
    handler = _stderr_handler(log_format)
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(log_level.upper())
    try:
        yield
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


# The default level is named: it is normalised on the way in (``upper()``), so mutation testing, which makes "info" of "INFO"
# in a literal default, cannot be answered by a test (the format is normalised too, but a test can see what it is handed).
_DEFAULT_LOG_LEVEL = "INFO"


def configure_logging(
    log_level: str = _DEFAULT_LOG_LEVEL,
    log_format: str = "console",
) -> logging.handlers.QueueListener:
    """Configure centralized logging with QueueHandler for asyncio.

    This function sets up:
    1. QueueHandler (non-blocking, asyncio-safe)
    2. QueueListener in separate thread
    3. Formatter (JSON or Console based on log_format)
    4. SensitiveDataFilter
    5. Root logger configuration

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: "json" for production, "console" for development (case-insensitive;
            any other value raises ValueError)

    Returns:
        QueueListener instance (must be stopped during shutdown)

    Learning: QueueHandler is CRITICAL for asyncio. Without it, logging
    I/O operations (file writes, network) block the event loop, causing
    slowdowns. The QueueListener runs in a separate thread, so I/O happens
    off the event loop.
    """
    global _queue_listener

    log_format = canonical_choice("LOG_FORMAT", log_format, LOG_FORMATS)

    # Create queue for log records
    log_queue: Queue = Queue(-1)  # Unlimited size

    # Create console handler (writes to stderr to avoid interfering with stdio transport)
    console_handler = _stderr_handler(log_format)

    # Create QueueListener with the real handler
    # respect_handler_level=True means handler's level is checked
    _queue_listener = logging.handlers.QueueListener(
        log_queue,
        console_handler,
        respect_handler_level=True,
    )

    # Start the listener thread
    _queue_listener.start()

    # Configure root logger with QueueHandler
    queue_handler = RecordPreservingQueueHandler(log_queue)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level.upper())
    root_logger.handlers.clear()  # Remove any existing handlers
    root_logger.addHandler(queue_handler)

    # Configure library loggers
    # Reduce noise from uvicorn access logs (keep only warnings+)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # httpx logs every request at INFO, and the Qdrant client makes several for each tool call
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Keep FastAPI at INFO
    logging.getLogger("fastapi").setLevel(logging.INFO)

    return _queue_listener


def shutdown_logging():
    """Shutdown the QueueListener gracefully.

    Call this during application shutdown to ensure all queued
    log records are flushed before exit.

    """
    global _queue_listener
    if _queue_listener:
        _queue_listener.stop()
        _queue_listener = None


