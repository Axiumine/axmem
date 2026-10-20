"""Integration tests for CLI output conversion.

Covers values that are not natively JSON and a stdout that refuses to be reconfigured.
"""
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.routes.cli import render


def test_to_jsonable_stringifies_values_json_cannot_represent():
    identifier = UUID("12345678-1234-5678-1234-567812345678")
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    converted = render.to_jsonable({"id": identifier, "seen": [moment], "path": Path("/srv/axmem/x")})

    assert converted == {
        "id": "12345678-1234-5678-1234-567812345678",
        "seen": ["2026-01-02 03:04:05+00:00"],
        "path": "/srv/axmem/x",
    }
    assert json.dumps(converted)


def test_ensure_safe_stdout_survives_a_stream_that_cannot_be_reconfigured(monkeypatch):
    class UnreconfigurableStdout(io.TextIOWrapper):
        def __init__(self, error):
            super().__init__(io.BytesIO(), encoding="cp1252", errors="strict")
            self._error = error

        def reconfigure(self, **kwargs):
            raise self._error

    for error in (OSError("stream is detached"), ValueError("I/O operation on closed file")):
        stream = UnreconfigurableStdout(error)
        monkeypatch.setattr("sys.stdout", stream)

        render.ensure_safe_stdout()

        assert stream.errors == "strict"
