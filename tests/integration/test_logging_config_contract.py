# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The exact output and the exact set-up of the logging: JSON fields, console colours, masking bounds and the queue.

What the log shows an operator (the fields of a JSON line, the colour of a level, the format of the console line, what
``configure_logging`` leaves on the root logger and on the noisy libraries) and what it must never show (a secret in a
message, a traceback or an ``extra`` field) is pinned here to the character.
"""
import inspect
import io
import json
import logging
import sys
import time
from datetime import UTC, date, datetime
from queue import Queue
from uuid import UUID

import pytest

from app.config import logging_config
from app.config.logging_config import (
    ConsoleFormatter,
    JSONFormatter,
    RecordPreservingQueueHandler,
    SensitiveDataFilter,
    configure_logging,
    shutdown_logging,
    wants_colour,
)
from app.middleware.logging_middleware import request_id_var, user_id_var

CREATED = datetime(2026, 1, 2, 3, 4, 5, 500000, tzinfo=UTC)
LIBRARY_LOGGERS = ("uvicorn.access", "httpx", "httpcore", "fastapi")
RESET = "\033[0m"


def _record(message="hello world", *, level=logging.INFO, args=None, exc_info=None, extra=None) -> logging.LogRecord:
    """A record of ``axmem.test`` as the function ``do_work`` of ``/srv/axmem/app/worker.py`` line 42 made it."""
    record = logging.getLogger("axmem.test").makeRecord(
        "axmem.test", level, "/srv/axmem/app/worker.py", 42, message, args, exc_info, "do_work", extra,
    )
    record.created = CREATED.timestamp()
    return record


def _raised(message: str):
    try:
        raise ValueError(message)  # noqa: TRY301 - the test needs a live exception to take its info from
    except ValueError:
        return sys.exc_info()


class _Stream(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


# ---- colour -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("no_color", "tty", "expected"), [
    (None, True, True),
    (None, False, False),
    ("", True, True),  # an empty NO_COLOR is not set
    ("1", True, False),
    ("0", True, False),  # any non-empty value counts
    ("1", False, False),
])
def test_colour_belongs_on_a_terminal_unless_no_color_is_set(monkeypatch, no_color, tty, expected):
    if no_color is None:
        monkeypatch.delenv("NO_COLOR", raising=False)
    else:
        monkeypatch.setenv("NO_COLOR", no_color)

    assert wants_colour(_Stream(tty)) is expected


def test_a_stream_without_isatty_gets_no_colour(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)

    assert wants_colour(object()) is False


def test_a_truthy_isatty_counts_as_a_terminal_and_the_answer_is_a_bool(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)

    class Odd:
        def isatty(self):
            return 1

    assert wants_colour(Odd()) is True


@pytest.mark.parametrize(("level", "code"), [
    (logging.DEBUG, "\033[36m"),
    (logging.INFO, "\033[32m"),
    (logging.WARNING, "\033[33m"),
    (logging.ERROR, "\033[31m"),
    (logging.CRITICAL, "\033[35m"),
])
def test_each_level_has_its_colour_and_the_record_keeps_its_plain_level_name(level, code):
    record = _record(level=level)
    name = record.levelname

    line = ConsoleFormatter("%(levelname)s %(message)s").format(record)

    assert line == f"{code}{name}{RESET} hello world"
    assert record.levelname == name


def test_a_level_without_a_colour_is_reset_around_its_name():
    record = _record()
    record.levelname = "NOTICE"

    assert ConsoleFormatter("%(levelname)s %(message)s").format(record) == f"{RESET}NOTICE{RESET} hello world"
    assert record.levelname == "NOTICE"


def test_without_colour_the_line_is_the_plain_format():
    record = _record(level=logging.ERROR)

    line = ConsoleFormatter("%(levelname)s %(message)s", colour=False).format(record)

    assert line == "ERROR hello world"
    assert record.levelname == "ERROR"


def test_the_console_formatter_passes_its_format_date_format_and_style_on():
    formatter = ConsoleFormatter("{levelname}|{asctime}|{message}", datefmt="%Y", style="{")
    formatter.converter = time.gmtime

    assert formatter.format(_record()) == f"\033[32mINFO{RESET}|2026|hello world"


# ---- the fields of a JSON line ---------------------------------------------------------------------------


def test_a_json_line_holds_exactly_these_fields_of_the_record():
    payload = json.loads(JSONFormatter().format(_record("hello %s", args=("world",))))

    assert payload == {
        "timestamp": "2026-01-02T03:04:05.500000+00:00",
        "level": "INFO",
        "logger": "axmem.test",
        "message": "hello world",
        "module": "worker",
        "function": "do_work",
        "line": 42,
    }


def test_the_request_context_is_added_only_for_the_ids_that_are_set():
    request_token = request_id_var.set("req-7")
    try:
        only_request = json.loads(JSONFormatter().format(_record()))
        user_token = user_id_var.set("user-9")
        try:
            both = json.loads(JSONFormatter().format(_record()))
        finally:
            user_id_var.reset(user_token)
    finally:
        request_id_var.reset(request_token)

    assert (only_request["request_id"], "user_id" in only_request) == ("req-7", False)
    assert (both["request_id"], both["user_id"]) == ("req-7", "user-9")


def test_a_cached_traceback_text_is_the_one_written_and_a_missing_one_is_rendered():
    cached = _record("failed", level=logging.ERROR, exc_info=_raised("boom"))
    cached.exc_text = "traceback that the filter masked"
    rendered = _record("failed", level=logging.ERROR, exc_info=_raised("boom"))

    assert json.loads(JSONFormatter().format(cached))["exception"] == "traceback that the filter masked"
    assert json.loads(JSONFormatter().format(rendered))["exception"].startswith("Traceback (most recent call last)")
    assert "exception" not in json.loads(JSONFormatter().format(_record()))


def test_the_extra_fields_are_written_as_they_are_and_the_private_ones_are_left_out():
    record = _record(extra={"tenant": "acme", "count": 3, "_internal": "never written"})

    payload = json.loads(JSONFormatter().format(record))

    assert (payload["tenant"], payload["count"]) == ("acme", 3)
    assert "_internal" not in payload
    assert set(payload) == {
        "timestamp", "level", "logger", "message", "module", "function", "line", "tenant", "count",
    }


def test_a_message_that_a_filter_masked_after_another_handler_formatted_the_record_is_the_masked_one():
    """``Formatter.format`` leaves the unmasked text in ``record.message``; the line must not fall back to it."""
    record = _record("login password=hunter2")
    logging.Formatter().format(record)
    SensitiveDataFilter().filter(record)

    assert json.loads(JSONFormatter().format(record))["message"] == "login password=***"


def test_the_fields_of_the_line_come_from_the_record_and_not_from_its_raw_attributes():
    """A record that wraps another one reports its own ``module`` and ``getMessage`` and keeps no raw copy in the line."""

    class Wrapping(logging.LogRecord):
        @property
        def module(self):
            return "reported-module"

        @module.setter
        def module(self, value):
            self.__dict__["module"] = value

    record = Wrapping("axmem.test", logging.INFO, "/srv/axmem/app/worker.py", 42, "raw message", None, None, "do_work")
    record.getMessage = lambda: "reported message"  # type: ignore[method-assign]

    payload = json.loads(JSONFormatter().format(record))

    assert (payload["module"], payload["message"]) == ("reported-module", "reported message")
    assert "getMessage" not in payload


def test_ids_dates_and_moments_are_written_as_text_and_anything_else_is_refused_by_type_name():
    ident = UUID("12345678-1234-5678-1234-567812345678")
    record = _record(extra={"ident": ident, "moment": CREATED, "day": date(2026, 1, 2)})

    payload = json.loads(JSONFormatter().format(record))

    assert (payload["ident"], payload["moment"], payload["day"]) == (
        "12345678-1234-5678-1234-567812345678", "2026-01-02T03:04:05.500000+00:00", "2026-01-02",
    )
    with pytest.raises(TypeError) as refused:
        JSONFormatter().format(_record(extra={"handle": object()}))
    assert str(refused.value) == "Object of type object is not JSON serialisable"


def test_the_queue_handler_hands_on_a_formatted_copy_with_the_exception_and_the_context_of_the_caller():
    handler = RecordPreservingQueueHandler(Queue())
    record = _record("value %s", args=("one",), level=logging.ERROR, exc_info=_raised("boom"))
    record.user_id = "set-on-the-record"
    request_token = request_id_var.set("req-5")
    user_token = user_id_var.set("user-5")
    try:
        prepared = handler.prepare(record)
    finally:
        request_id_var.reset(request_token)
        user_id_var.reset(user_token)

    assert prepared is not record
    assert (prepared.msg, prepared.args) == ("value one", None)
    assert prepared.exc_info == record.exc_info
    assert (prepared.request_id, prepared.user_id) == ("req-5", "set-on-the-record")  # what the record has is kept
    assert (record.msg, record.args) == ("value %s", ("one",))
    assert not hasattr(record, "request_id")


def test_the_queue_handler_adds_no_context_that_is_not_set():
    prepared = RecordPreservingQueueHandler(Queue()).prepare(_record())

    assert not hasattr(prepared, "request_id")
    assert not hasattr(prepared, "user_id")


# ---- the handler that writes to stderr -------------------------------------------------------------------


def test_the_json_handler_writes_masked_json_lines_to_stderr(capsys):
    handler = logging_config._stderr_handler("json")

    handler.handle(_record("login password=hunter2"))

    assert handler.stream is sys.stderr
    assert isinstance(handler.formatter, JSONFormatter)
    assert json.loads(capsys.readouterr().err)["message"] == "login password=***"


def test_the_console_handler_writes_the_plain_line_to_a_pipe_and_the_coloured_one_to_a_terminal(monkeypatch):
    monkeypatch.setattr(logging.Formatter, "converter", time.gmtime)
    monkeypatch.delenv("NO_COLOR", raising=False)
    pipe = _Stream(tty=False)
    terminal = _Stream(tty=True)

    monkeypatch.setattr(sys, "stderr", pipe)
    logging_config._stderr_handler("console").handle(_record("login token=abc123def"))
    monkeypatch.setattr(sys, "stderr", terminal)
    logging_config._stderr_handler("console").handle(_record("login token=abc123def"))

    assert pipe.getvalue() == "2026-01-02 03:04:05 - axmem.test - INFO - login token=***\n"
    assert terminal.getvalue() == f"2026-01-02 03:04:05 - axmem.test - \033[32mINFO{RESET} - login token=***\n"


def test_the_handler_carries_the_masking_filter():
    handler = logging_config._stderr_handler("console")

    assert [type(f) for f in handler.filters] == [SensitiveDataFilter]


# ---- configure_logging and shutdown_logging --------------------------------------------------------------


@pytest.fixture
def queued_logging(monkeypatch):
    """``configure_logging`` with the logger levels and the root handlers put back afterwards (read stderr with capsys)."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    shutdown_logging()
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    levels = {name: logging.getLogger(name).level for name in LIBRARY_LOGGERS}
    for name in LIBRARY_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)
    yield
    shutdown_logging()
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, previous in levels.items():
        logging.getLogger(name).setLevel(previous)


def test_the_documented_defaults_are_info_and_console():
    parameters = inspect.signature(configure_logging).parameters

    assert (parameters["log_level"].default, parameters["log_format"].default) == ("INFO", "console")


def test_without_arguments_the_root_logs_info_and_up_in_the_console_format(queued_logging, capsys):
    listener = configure_logging()
    logging.getLogger("axmem.probe").debug("not shown")
    logging.getLogger("axmem.probe").info("shown")
    shutdown_logging()

    assert logging.getLogger().level == logging.INFO
    assert isinstance(listener.handlers[0].formatter, ConsoleFormatter)
    written = capsys.readouterr().err
    assert written.endswith(" - axmem.probe - INFO - shown\n")
    assert "not shown" not in written


def test_the_level_and_the_format_are_taken_whatever_their_case(queued_logging):
    listener = configure_logging("debug", "JSON")

    assert logging.getLogger().level == logging.DEBUG
    assert isinstance(listener.handlers[0].formatter, JSONFormatter)


def test_a_format_that_is_not_known_is_refused_naming_the_setting(queued_logging):
    with pytest.raises(ValueError) as refused:
        configure_logging("INFO", "yaml")

    assert str(refused.value) == "Unsupported LOG_FORMAT setting. Must be one of: console, json"


def test_a_blank_format_is_the_console_format(queued_logging):
    assert isinstance(configure_logging("INFO", "").handlers[0].formatter, ConsoleFormatter)


def test_the_root_has_one_queue_handler_on_an_unlimited_queue_that_the_listener_drains(queued_logging):
    root = logging.getLogger()
    root.addHandler(logging.NullHandler())

    listener = configure_logging("INFO", "json")

    [handler] = root.handlers
    assert isinstance(handler, RecordPreservingQueueHandler)
    assert handler.queue is listener.queue
    assert handler.queue.maxsize == -1
    assert logging_config._queue_listener is listener


def test_records_reach_stderr_masked_and_with_their_request_context(queued_logging, capsys):
    configure_logging("INFO", "json")
    token = request_id_var.set("req-3")
    try:
        logging.getLogger("axmem.probe").info("login password=%s", "hunter2", extra={"tenant": "acme"})
    finally:
        request_id_var.reset(token)
    shutdown_logging()

    payload = json.loads(capsys.readouterr().err)
    assert (payload["message"], payload["tenant"], payload["request_id"]) == ("login password=***", "acme", "req-3")


def test_the_level_of_the_handler_is_respected(queued_logging, capsys):
    listener = configure_logging("DEBUG", "json")
    listener.handlers[0].setLevel(logging.ERROR)

    logging.getLogger("axmem.probe").warning("quiet")
    logging.getLogger("axmem.probe").error("loud")
    shutdown_logging()

    assert listener.respect_handler_level is True
    assert [json.loads(line)["message"] for line in capsys.readouterr().err.splitlines()] == ["loud"]


def test_the_noisy_libraries_are_held_back_and_the_root_keeps_the_level_asked_for(queued_logging):
    configure_logging("DEBUG", "console")

    assert logging.getLogger().level == logging.DEBUG
    assert [logging.getLogger(name).level for name in LIBRARY_LOGGERS] == [
        logging.WARNING, logging.WARNING, logging.WARNING, logging.INFO,
    ]


def test_shutdown_flushes_the_queued_records_stops_the_listener_and_forgets_it(queued_logging, capsys):
    listener = configure_logging("INFO", "json")
    logging.getLogger("axmem.probe").info("last words")

    shutdown_logging()

    assert json.loads(capsys.readouterr().err)["message"] == "last words"
    assert listener._thread is None
    assert logging_config._queue_listener is None
    shutdown_logging()  # a second call finds nothing to stop
    assert logging_config._queue_listener is None


# ---- the bounds of the masking ---------------------------------------------------------------------------


def _wrapped(depth: int, leaf, wrap):
    for _ in range(depth):
        leaf = wrap(leaf)
    return leaf


@pytest.mark.parametrize("wrap", [lambda inner: [inner], lambda inner: (inner,), lambda inner: {"k": inner}], ids=[
    "list", "tuple", "dict",
])
def test_a_secret_is_masked_inside_four_levels_of_containers_and_deeper_ones_are_left_alone(wrap):
    """The scan of a structure is bounded: a hostile deeply nested value costs a bounded amount of work."""
    masker = SensitiveDataFilter()

    assert masker._mask_value(_wrapped(4, "password=hunter2", wrap)) == _wrapped(4, "password=***", wrap)
    assert masker._mask_value(_wrapped(5, "password=hunter2", wrap)) == _wrapped(5, "password=hunter2", wrap)


def test_the_containers_keep_their_type_and_the_other_values_are_returned_as_they_are():
    masker = SensitiveDataFilter()
    marker = object()

    masked = masker._mask_value({"items": ["token=abc123def", ("api-key=secret123", 7)], "marker": marker, "none": None})

    assert masked == {"items": ["token=***", ("api-key=***", 7)], "marker": marker, "none": None}
    assert isinstance(masked["items"][1], tuple)


def test_a_text_of_exactly_the_limit_is_kept_and_one_more_character_leaves_the_middle_out():
    limit = SensitiveDataFilter.MAX_TEXT_LENGTH
    half = limit // 2
    text = "H" * half + "Z" + "T" * half

    assert len(text) == limit + 1
    assert SensitiveDataFilter._shorten("y" * limit) == "y" * limit
    assert SensitiveDataFilter._shorten(text) == "H" * half + " ...[1 characters left out]... " + "T" * half


def test_the_arguments_of_a_record_are_masked_as_a_tuple_and_as_a_mapping_and_an_empty_one_is_left():
    positional = _record("attempt %d with %s", args=(3, "password=hunter2"))
    named = _record("login with %(credential)s", args=({"credential": "token=abc123def", "count": 2},))
    bare = _record("nothing to fill in")

    for record in (positional, named, bare):
        assert SensitiveDataFilter().filter(record) is True

    assert positional.args == (3, "password=***")
    assert named.args == {"credential": "token=***", "count": 2}
    assert bare.args is None
    assert named.getMessage() == "login with token=***"


def test_a_traceback_is_rendered_and_masked_and_the_exception_is_kept():
    record = _record("failed", level=logging.ERROR, exc_info=_raised("could not log in with password=hunter2"))

    SensitiveDataFilter().filter(record)

    assert record.exc_info is not None
    assert record.exc_text.startswith("Traceback (most recent call last)")
    assert record.exc_text.endswith("ValueError: could not log in with password=***")


def test_a_traceback_that_was_rendered_earlier_is_masked_and_not_rendered_again():
    record = _record("failed", level=logging.ERROR, exc_info=_raised("boom"))
    record.exc_text = "earlier text with token=abc123def"

    SensitiveDataFilter().filter(record)

    assert record.exc_text == "earlier text with token=***"


def test_a_record_without_an_exception_gets_no_traceback_text():
    record = _record()

    assert SensitiveDataFilter().filter(record) is True
    assert record.exc_text is None
