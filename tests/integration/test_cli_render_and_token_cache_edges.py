"""Integration tests for CLI output conversion and the on-disk OAuth token cache.

Covers values that are not natively JSON, a stdout that refuses to be reconfigured,
and the token cache when the disk fails while relocated metadata is being repaired.
"""
import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from key_value.aio.stores.filetree.store import FileTreeStore

from app.routes.cli import render, token_storage


def test_to_jsonable_stringifies_values_json_cannot_represent():
    identifier = UUID("12345678-1234-5678-1234-567812345678")
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    converted = render.to_jsonable({"id": identifier, "seen": [moment], "path": Path("/srv/forgetful/x")})

    assert converted == {
        "id": "12345678-1234-5678-1234-567812345678",
        "seen": ["2026-01-02 03:04:05+00:00"],
        "path": "/srv/forgetful/x",
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


def test_relocated_cache_metadata_survives_a_failing_disk_untouched(tmp_path, monkeypatch):
    cache = tmp_path / "tokens"
    cache.mkdir()
    info_file = cache / "mcp-oauth-tokens-info.json"
    stale = json.dumps({"directory": "/old/home/tokens/mcp-oauth-tokens"})
    info_file.write_text(stale, encoding="utf-8")

    def disk_full(source, destination):
        raise OSError("no space left on device")

    monkeypatch.setattr(os, "replace", disk_full)

    store = token_storage.oauth_token_file_tree_store(cache)

    assert isinstance(store, FileTreeStore)
    assert info_file.read_text(encoding="utf-8") == stale
    assert [path.name for path in cache.iterdir()] == [info_file.name]
