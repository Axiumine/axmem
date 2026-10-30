# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for how RemoteExecutor reads results that carry no structured data.

A bare array or scalar has no MCP structured content (the root must be an object), so
the remote tool result arrives as JSON text in the content blocks instead of ``.data``.
The fastmcp client is replaced by a stub through the client-factory seam.
"""
from types import SimpleNamespace

import pytest

from app.routes.cli.remote_executor import RemoteExecutor


class _TextOnlyClient:
    """Stands in for fastmcp.Client; every tool call returns the given content blocks."""

    def __init__(self, content):
        self._content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def call_tool(self, name, arguments):
        return SimpleNamespace(data=None, content=self._content)


def _executor(content):
    return RemoteExecutor(
        "http://example.com:8020", client_factory=lambda url, token: _TextOnlyClient(content),
    )


def _text(value):
    return SimpleNamespace(text=value)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ([_text('[{"id": 1}, {"id": 2}]')], [{"id": 1}, {"id": 2}]),
        ([_text("42")], 42),
        ([_text("plain words, not JSON")], "plain words, not JSON"),
        ([SimpleNamespace(), _text('["after a block without text"]')], ["after a block without text"]),
        ([SimpleNamespace(), SimpleNamespace(mimeType="image/png")], None),
        ([], None),
        (None, None),
    ],
    ids=[
        "json-array-text",
        "json-scalar-text",
        "non-json-text-returned-verbatim",
        "blocks-without-text-are-skipped",
        "no-text-block-at-all",
        "empty-content",
        "missing-content",
    ],
)
async def test_result_without_structured_data_is_read_from_the_text_content(content, expected):
    result = await _executor(content).execute("list_projects", {})

    assert result == expected
