# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract of the ``created_since`` argument of get_recent_memories (``parse_created_since``).

An agent sends the filter as text; it becomes a time in UTC, or is refused with the one message the agent reads back,
pinned here word for word. A timestamp with no instant in UTC (year 1 moved forward past the range, year 9999 moved
back) is refused with that message like any other value that is not a timestamp with a timezone.
"""

from datetime import UTC, datetime, timedelta, tzinfo

import pytest
from fastmcp.exceptions import ToolError

from app.routes.mcp.adapters import memory_arguments
from app.routes.mcp.adapters.memory_arguments import parse_created_since

CREATED_SINCE_REFUSAL = "created_since must be an ISO 8601 timestamp with a timezone"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-18T12:30:45Z", datetime(2026, 10, 18, 12, 30, 45, tzinfo=UTC)),
        ("2026-10-18T12:30:45+00:00", datetime(2026, 10, 18, 12, 30, 45, tzinfo=UTC)),
        ("2026-10-18T12:30:45+02:00", datetime(2026, 10, 18, 10, 30, 45, tzinfo=UTC)),
        ("2026-10-18T01:30:45-05:30", datetime(2026, 10, 18, 7, 0, 45, tzinfo=UTC)),
        ("2026-10-18T12:30:45.250Z", datetime(2026, 10, 18, 12, 30, 45, 250000, tzinfo=UTC)),
    ],
)
def test_parse_created_since_returns_the_instant_in_utc(text, expected):
    result = parse_created_since(text)

    assert result == expected
    assert result is not None
    assert result.utcoffset() == timedelta(0)
    assert result.tzinfo is UTC


def test_parse_created_since_leaves_an_omitted_value_alone():
    assert parse_created_since(None) is None


@pytest.mark.parametrize(
    "value",
    [
        7,
        1.5,
        True,
        ["2026-10-18T12:30:45Z"],
        "yesterday",
        "",
        "2026-10-18",
        "2026-10-18T12:30:45",
        "2026-10-18 12:30:45+00:00",
        "2026-13-45T12:30:45Z",
    ],
    ids=["int", "float", "bool", "list", "word", "empty", "date-only", "no-timezone", "space-separator", "bad-month"],
)
def test_parse_created_since_refuses_what_is_not_a_timestamp_with_a_timezone(value):
    with pytest.raises(ToolError) as caught:
        parse_created_since(value)

    assert str(caught.value) == CREATED_SINCE_REFUSAL


@pytest.mark.parametrize(
    "value",
    ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"],
    ids=["before-year-1-in-utc", "after-year-9999-in-utc"],
)
def test_parse_created_since_refuses_a_timestamp_that_has_no_instant_in_utc(value):
    with pytest.raises(ToolError) as caught:
        parse_created_since(value)

    assert str(caught.value) == CREATED_SINCE_REFUSAL
    assert isinstance(caught.value.__cause__, OverflowError)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0001-01-01T00:00:00Z", datetime(1, 1, 1, tzinfo=UTC)),
        ("0001-01-01T00:00:00-01:00", datetime(1, 1, 1, 1, tzinfo=UTC)),
        ("9999-12-31T23:59:59.999999Z", datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)),
        ("9999-12-31T23:59:59+01:00", datetime(9999, 12, 31, 22, 59, 59, tzinfo=UTC)),
    ],
)
def test_parse_created_since_accepts_a_timestamp_at_the_edge_of_the_range(text, expected):
    assert parse_created_since(text) == expected


def test_parse_created_since_gives_the_parser_an_explicit_offset_in_place_of_the_z_suffix(monkeypatch):
    handed_over = []

    class _RecordingDatetime:
        @staticmethod
        def fromisoformat(text):
            handed_over.append(text)
            return datetime.fromisoformat(text)

    monkeypatch.setattr(memory_arguments, "datetime", _RecordingDatetime)

    assert parse_created_since("2026-10-18T12:30:45Z") == datetime(2026, 10, 18, 12, 30, 45, tzinfo=UTC)
    assert parse_created_since("2026-10-18T12:30:45+02:00") == datetime(2026, 10, 18, 10, 30, 45, tzinfo=UTC)
    assert handed_over == ["2026-10-18T12:30:45+00:00", "2026-10-18T12:30:45+02:00"]


def test_parse_created_since_refuses_a_timestamp_whose_timezone_reports_no_offset(monkeypatch):
    class _NoOffset(tzinfo):
        def utcoffset(self, dt):
            return None

        def dst(self, dt):
            return None

    class _ParserWithoutOffset:
        @staticmethod
        def fromisoformat(_text):
            return datetime(2026, 10, 18, 12, 30, 45, tzinfo=_NoOffset())

    monkeypatch.setattr(memory_arguments, "datetime", _ParserWithoutOffset)

    with pytest.raises(ToolError) as caught:
        parse_created_since("2026-10-18T12:30:45+02:00")

    assert str(caught.value) == CREATED_SINCE_REFUSAL


def test_parse_created_since_hides_the_parser_error_behind_the_one_message():
    with pytest.raises(ToolError) as caught:
        parse_created_since("yesterday")

    assert isinstance(caught.value.__cause__, ValueError)
