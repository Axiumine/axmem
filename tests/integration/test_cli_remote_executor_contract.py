# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the exact URLs, requests and messages of the remote side of the CLI.

The server URL an operator types is turned into the URL the client connects to, the discovery requests carry no empty
filter, and a bearer token that cannot be sent is refused with a message that never repeats it. The fastmcp client, the
one external boundary, is replaced with a recording stub.
"""
import pytest

from app.routes.cli.remote_executor import (
    RemoteExecutor,
    bearer_token_from_env,
    normalize_server_url,
    strip_url_userinfo,
)


class StubResult:
    def __init__(self, data):
        self.data = data
        self.content = []


class StubClient:
    """Stands in for fastmcp.Client: records the tool calls."""

    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return StubResult({"ok": True})

    async def close(self):
        pass


@pytest.mark.parametrize(
    ("typed", "requested"),
    [
        ("http://example.com/mcpX", "http://example.com/mcpX"),
        ("example.com/gateX/", "https://example.com/gateX"),
        ("localhost:8020", "http://localhost:8020/mcp"),
        ("[::1]:8020", "http://[::1]:8020/mcp"),
        ("127.0.0.1", "http://127.0.0.1/mcp"),
        ("example.com:8020", "https://example.com:8020/mcp"),
        ("HTTP://Example.com", "http://Example.com/mcp"),
        ("https://example.com/", "https://example.com/mcp"),
        ("https://example.com/api/", "https://example.com/api"),
    ],
)
def test_a_typed_server_is_turned_into_the_url_the_client_requests(typed, requested):
    assert normalize_server_url(typed) == requested


@pytest.mark.parametrize(
    ("url", "bare"),
    [
        ("http://user:p@ss@host:8020/mcp", "http://host:8020/mcp"),
        ("https://user@host/mcp", "https://host/mcp"),
        ("https://host:8020/mcp?a=b@c", "https://host:8020/mcp?a=b@c"),
    ],
)
def test_the_credentials_of_a_url_are_cut_at_its_last_at_sign_of_the_authority(url, bare):
    assert strip_url_userinfo(url) == bare


def test_a_token_with_control_characters_is_refused_without_repeating_it(monkeypatch):
    monkeypatch.setenv("AXMEM_TOKEN", "secret\ntoken")

    with pytest.raises(ValueError) as excinfo:
        bearer_token_from_env()

    assert str(excinfo.value) == "AXMEM_TOKEN contains control or non-ASCII characters; fix the value or unset it"


@pytest.mark.parametrize("category", [None, ""])
async def test_listing_the_tools_without_a_category_sends_no_filter(category):
    client = StubClient()
    executor = RemoteExecutor("http://example.com:8020", client_factory=lambda url, token: client)

    await executor.list_tools(category)

    assert client.calls == [("discover_axmem_tools", {})]
