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
            "forgetful.test", logging.ERROR, __file__, 1, message, None, sys.exc_info(),
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
