"""Log masking of non-tuple argument shapes and JSON rendering of rich ``extra`` values.

``SensitiveDataFilter`` must mask secrets however a record carries its arguments (positional
tuple, ``%(name)s`` mapping, non-string message objects) and ``JSONFormatter`` must render the
value types services put into ``extra={...}`` (UUID, datetime, date) and refuse the rest loudly.
"""

import json
import logging
import sys
from datetime import UTC, date, datetime
from types import MappingProxyType
from uuid import UUID

import pytest

from app.config.logging_config import JSONFormatter, SensitiveDataFilter


def _record(message, args=None, extra=None) -> logging.LogRecord:
    return logging.getLogger("tests.logging").makeRecord(
        "tests.logging", logging.INFO, __file__, 1, message, args, None, extra=extra,
    )


@pytest.mark.parametrize(
    "mapping",
    [
        {"credential": "password=hunter2"},
        MappingProxyType({"credential": "password=hunter2"}),
    ],
    ids=["dict", "read-only-mapping"],
)
def test_named_mapping_arguments_are_masked(mapping):
    record = _record("login with %(credential)s", (mapping,))

    assert SensitiveDataFilter().filter(record) is True

    assert record.getMessage() == "login with password=***"


def test_positional_arguments_of_any_type_are_masked_or_kept():
    record = _record("attempt %d with %s", (3, "password=hunter2"))

    assert SensitiveDataFilter().filter(record) is True

    assert record.getMessage() == "attempt 3 with password=***"


def test_non_string_message_object_is_kept_and_its_arguments_are_masked():
    class Event:
        def __str__(self) -> str:
            return "event carried %s"

    event = Event()
    record = _record(event, ("token=abc123def",))  # type: ignore[arg-type]

    assert SensitiveDataFilter().filter(record) is True

    assert record.msg is event
    assert record.getMessage() == "event carried token=***"


def test_json_formatter_renders_uuid_datetime_and_date_extras_as_strings():
    ident = UUID("12345678-1234-5678-1234-567812345678")
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    record = _record(
        "recorded", extra={"ident": ident, "moment": moment, "day": date(2026, 1, 2)},
    )

    payload = json.loads(JSONFormatter().format(record))

    assert payload["message"] == "recorded"
    assert payload["ident"] == "12345678-1234-5678-1234-567812345678"
    assert payload["moment"] == "2026-01-02T03:04:05+00:00"
    assert payload["day"] == "2026-01-02"


def test_json_formatter_rejects_extra_values_it_cannot_serialise():
    record = _record("recorded", extra={"handle": object()})

    with pytest.raises(TypeError, match="Object of type object is not JSON serialisable"):
        JSONFormatter().format(record)


def _record_with_exception(message: str = "failed") -> logging.LogRecord:
    try:
        raise ValueError("boom")
    except ValueError:
        return logging.LogRecord(
            "axmem.test", logging.ERROR, __file__, 1, message, None, sys.exc_info(),
        )


def test_json_formatter_adds_the_traceback_of_a_logged_exception():
    payload = json.loads(JSONFormatter().format(_record_with_exception()))

    assert payload["message"] == "failed"
    assert payload["exception"].startswith("Traceback")
    assert payload["exception"].rstrip().endswith("ValueError: boom")


def test_queue_handler_keeps_the_exception_and_adds_the_request_context_of_the_emitting_thread():
    from queue import Queue

    from app.config.logging_config import RecordPreservingQueueHandler
    from app.middleware.logging_middleware import request_id_var, user_id_var

    handler = RecordPreservingQueueHandler(Queue())
    record = _record_with_exception("value %s")
    record.args = ("one",)
    request_token = request_id_var.set("req-1")
    user_token = user_id_var.set("user-1")
    try:
        prepared = handler.prepare(record)
    finally:
        request_id_var.reset(request_token)
        user_id_var.reset(user_token)

    assert prepared is not record
    assert (prepared.msg, prepared.args) == ("value one", None)
    assert prepared.exc_info == record.exc_info
    assert (prepared.request_id, prepared.user_id) == ("req-1", "user-1")
    assert record.args == ("one",)  # the record the other handlers see is untouched
    assert json.loads(JSONFormatter().format(prepared))["exception"].endswith("ValueError: boom")


# ---- a hostile text cannot hold the log filter -----------------------------------------------------------


@pytest.mark.parametrize(
    "bait", ["://a:", "://a:/?#", "://" + "a" * 200 + ":", "x://" + "u" * 300 + ":" + "p" * 3000],
)
def test_a_text_built_to_slow_the_url_pattern_is_filtered_in_a_moment(bait):
    """The scan of every ``://`` used to run to the end of the message: a megabyte of ``://a:`` took about a minute."""
    import time

    record = _record("tool %s", (bait * (1_000_000 // len(bait)),))

    started = time.perf_counter()
    SensitiveDataFilter().filter(record)

    assert time.perf_counter() - started < 2.0
    assert len(record.args[0]) < SensitiveDataFilter.MAX_TEXT_LENGTH + 100


def test_a_long_text_keeps_its_head_and_its_tail_and_says_how_much_was_left_out():
    text = "head-" + "x" * 100_000 + "-tail"
    record = _record(text)

    SensitiveDataFilter().filter(record)

    assert record.msg.startswith("head-x")
    assert record.msg.endswith("x-tail")
    assert f"[{len(text) - SensitiveDataFilter.MAX_TEXT_LENGTH} characters left out]" in record.msg


def test_a_text_below_the_limit_is_not_shortened():
    record = _record("y" * SensitiveDataFilter.MAX_TEXT_LENGTH)

    SensitiveDataFilter().filter(record)

    assert record.msg == "y" * SensitiveDataFilter.MAX_TEXT_LENGTH


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("connect to postgres://admin:s3cret@db.internal:5432/app", "connect to postgres://***:***@db.internal:5432/app"),
        ("see https://example.com/a:b and mail user:pw@example.com", "see https://example.com/a:b and mail user:pw@example.com"),
        ("http://user:p%40ss@host/path", "http://***:***@host/path"),
        # a password that was not percent-encoded: "/", "?" and "#" are common in a generated one
        ("postgresql://app:pa/ss@db:5432/x", "postgresql://***:***@db:5432/x"),
        ("redis://u:pa?ss@h", "redis://***:***@h"),
        ("redis://u:pa#ss@h", "redis://***:***@h"),
        pytest.param("https://u:" + "p" * 3000 + "@host/x", "https://***:***@host/x", id="a-token-as-the-password"),
    ],
)
def test_credentials_in_a_url_are_still_masked(text, masked):
    record = _record(text)

    SensitiveDataFilter().filter(record)

    assert record.msg == masked


# ---- the extra fields are masked too ---------------------------------------------------------------------


def test_extra_fields_are_masked_before_the_json_formatter_writes_them():
    record = _record("recorded", extra={
        "detail": "token=abc123def",
        "items": ["password=hunter2", "plain", 3],
        "nested": {"url": "https://u:p@host/x", "count": 2},
        "pair": ("api-key=secret123", 1),
        "number": 7,
    })

    SensitiveDataFilter().filter(record)
    payload = json.loads(JSONFormatter().format(record))

    assert payload["detail"] == "token=***"
    assert payload["items"] == ["password=***", "plain", 3]
    assert payload["nested"] == {"url": "https://***:***@host/x", "count": 2}
    assert payload["pair"] == ["api-key=***", 1]
    assert payload["number"] == 7
    assert "abc123def" not in json.dumps(payload)


def test_a_structure_nested_deeper_than_the_filter_looks_is_left_as_it_is():
    deep = {"a": {"b": {"c": {"d": {"e": "token=abc123def"}}}}}
    record = _record("recorded", extra={"deep": deep})

    SensitiveDataFilter().filter(record)

    assert record.deep == deep


def test_the_standard_attributes_of_a_record_are_left_alone():
    record = _record("recorded %s", ("token=abc123def",))
    before = (record.name, record.levelno, record.pathname, record.lineno, record.created)

    SensitiveDataFilter().filter(record)

    assert (record.name, record.levelno, record.pathname, record.lineno, record.created) == before


def test_the_legacy_settings_warning_is_not_garbled_by_the_filter(monkeypatch, caplog):
    """The names of old settings contain PASSWORD and TOKEN: a ``NAME: advice`` listing lost a word of the advice."""
    from app.config import legacy_settings

    monkeypatch.setattr(legacy_settings.settings, "AXMEM_SCOPES", "read")
    with caplog.at_level(logging.WARNING, logger=legacy_settings.logger.name):
        legacy_settings.warn_about_legacy_settings(
            environ={"POSTGRES_PASSWORD": "x", "FORGETFUL_TOKEN": "y"}, dotenv_paths=[],
        )
    [record] = caplog.records
    SensitiveDataFilter().filter(record)

    message = record.getMessage()
    assert "POSTGRES_PASSWORD (Qdrant is the only store" in message
    assert "FORGETFUL_TOKEN (the product was renamed: the variable is AXMEM_TOKEN now)" in message
    assert "***" not in message


# ---- colour only on a terminal ---------------------------------------------------------------------------


class _Stream:
    def __init__(self, tty: bool) -> None:
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def test_colour_is_for_a_terminal_that_did_not_ask_for_none(monkeypatch):
    from app.config.logging_config import wants_colour

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert wants_colour(_Stream(tty=True)) is True
    assert wants_colour(_Stream(tty=False)) is False
    assert wants_colour(object()) is False  # a stream that cannot tell is a file or a pipe

    monkeypatch.setenv("NO_COLOR", "1")
    assert wants_colour(_Stream(tty=True)) is False

    monkeypatch.setenv("NO_COLOR", "")  # an empty value does not count (https://no-color.org)
    assert wants_colour(_Stream(tty=True)) is True


def test_the_console_formatter_writes_escape_sequences_only_when_it_is_told_to():
    from app.config.logging_config import ConsoleFormatter

    record = _record("hello")
    plain = ConsoleFormatter(fmt="%(levelname)s %(message)s", colour=False).format(record)
    coloured = ConsoleFormatter(fmt="%(levelname)s %(message)s").format(record)

    assert plain == "INFO hello"
    assert "\033[" in coloured
    assert coloured.endswith("hello")


@pytest.mark.parametrize("tty", [False, True])
def test_the_stderr_handler_follows_the_stream_it_writes_to(monkeypatch, tty):
    from app.config import logging_config

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys, "stderr", _Stream(tty))

    handler = logging_config._stderr_handler("console")

    assert handler.formatter.colour is tty
