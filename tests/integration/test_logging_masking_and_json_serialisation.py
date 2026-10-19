"""Log masking of non-tuple argument shapes and JSON rendering of rich ``extra`` values.

``SensitiveDataFilter`` must mask secrets however a record carries its arguments (positional
tuple, ``%(name)s`` mapping, non-string message objects) and ``JSONFormatter`` must render the
value types services put into ``extra={...}`` (UUID, datetime, date) and refuse the rest loudly.
"""

import json
import logging
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
