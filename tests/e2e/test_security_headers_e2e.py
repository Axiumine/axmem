# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests: the security headers on the answers of a server that checks a credential, refusals included.

The app is built as ``main._serve`` builds it, with the middleware of ``main._http_middleware``, over the real routes,
a real Qdrant node and the static-token provider of ``AUTH_MODE=static``. The 401 of a REST route, the 401 of the MCP
authentication, the 403 of a scope and the 413 of the body limit are produced at four different depths of the stack and
must all carry the headers; so must the event stream of a real MCP session, which must still work through the middleware.
"""
from contextlib import asynccontextmanager

import pytest
from fastmcp import Client
from httpx import ASGITransport, AsyncClient

import main
from app.config.settings import settings
from app.routes.mcp.scope_resolver import parse_scopes, resolve_permitted_tools
from tests.e2e.conftest import (
    TENANT_TOKENS,
    AuthStack,
    build_app,
    static_token_verifier,
)

LOCAL = "http://127.0.0.1:8020"
ALICE = TENANT_TOKENS["alice"]
# Pinned here in full: the values are a decision, see app/middleware/security_headers.py
HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "cache-control": "no-store",
    "cross-origin-resource-policy": "same-origin",
}
INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "probe", "version": "1"}},
}
MEMORY = {
    "title": "Header probe", "content": "A memory behind a credential.", "context": "security headers e2e test",
    "keywords": ["headers"], "tags": ["e2e"], "importance": 5,
}


def assert_carries(response, expected=HEADERS):
    for name, value in expected.items():
        assert response.headers.get_list(name) == [value], (name, dict(response.headers))
    assert "strict-transport-security" not in response.headers  # this process serves no TLS here


@pytest.fixture
async def served(monkeypatch, embedding_adapter, reranker_adapter):
    """The app of ``AUTH_MODE=static`` with its middleware; yields (FastMCP server, ASGI app). The server's lifespan runs."""
    monkeypatch.setattr(settings, "AUTH_MODE", "static")
    monkeypatch.setattr(settings, "CORS_ENABLED", False)
    async for server in build_app(embedding_adapter, reranker_adapter, auth_provider=static_token_verifier()):
        async with Client(server):  # attaches the services, the registry and the scope ceiling, as the lifespan does
            yield server, server.http_app(middleware=main._http_middleware())


@asynccontextmanager
async def streaming(monkeypatch, embedding_adapter, reranker_adapter):
    """The same app with the lifespan of its HTTP application running too, which the MCP endpoint needs."""
    monkeypatch.setattr(settings, "AUTH_MODE", "static")
    monkeypatch.setattr(settings, "CORS_ENABLED", False)
    async for server in build_app(embedding_adapter, reranker_adapter, auth_provider=static_token_verifier()):
        app = server.http_app(middleware=main._http_middleware())
        async with app.router.lifespan_context(app):
            yield app


@pytest.fixture
async def client(served):
    _, app = served
    async with AsyncClient(transport=ASGITransport(app=app), base_url=LOCAL) as http:
        yield http


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---- success ---------------------------------------------------------------------------------------------


async def test_an_authorised_answer_carries_the_headers(client):
    created = await client.post("/api/v1/memories", json=MEMORY, headers=bearer(ALICE))
    listed = await client.get("/api/v1/memories", headers=bearer(ALICE))

    assert (created.status_code, listed.status_code) == (201, 200)
    assert_carries(created)
    assert_carries(listed)


async def test_no_route_serves_a_page_or_oauth_discovery_so_the_policy_needs_no_exemption(served):
    _, app = served
    paths = {route.path for route in app.routes}

    assert not [path for path in paths if path.startswith(("/docs", "/redoc", "/openapi", "/.well-known", "/authorize", "/token"))]
    assert "/mcp" in paths


# ---- the refusals of the credential checks ---------------------------------------------------------------


@pytest.mark.parametrize(
    "headers", [{}, bearer("not-a-token"), {"Authorization": "Basic YWxpY2U6c2VjcmV0"}], ids=["none", "unknown", "basic"],
)
async def test_the_401_of_a_rest_route_carries_the_headers(client, headers):
    response = await client.get("/api/v1/memories", headers=headers)

    assert response.status_code == 401
    assert_carries(response)


@pytest.mark.parametrize("headers", [{}, bearer("not-a-token")], ids=["none", "unknown"])
async def test_the_401_of_the_mcp_authentication_carries_the_headers(client, headers):
    response = await client.post(
        "/mcp", json=INITIALIZE, headers={"Accept": "application/json, text/event-stream", **headers},
    )

    assert response.status_code == 401
    assert "www-authenticate" in response.headers  # the answer of the authentication is kept as it is
    assert_carries(response)


async def test_the_403_of_a_scope_carries_the_headers(served, client):
    server, _ = served
    ceiling = parse_scopes("write:memories")
    server._instance_permitted_tools = resolve_permitted_tools(ceiling, server.registry)
    server._instance_scopes = ceiling

    response = await client.get("/api/v1/memories", headers=bearer(ALICE))

    assert response.status_code == 403
    assert_carries(response)


# ---- the other refusals ---------------------------------------------------------------------------------


async def test_the_413_of_the_body_limit_carries_the_headers_before_any_credential_is_read(monkeypatch, served):
    server, _ = served
    monkeypatch.setattr(settings, "REQUEST_BODY_MAX_BYTES", 1024)  # read when the stack is built: build one of its own
    app = server.http_app(middleware=main._http_middleware())
    async with AsyncClient(transport=ASGITransport(app=app), base_url=LOCAL) as small:
        response = await small.post("/api/v1/memories", content=b"x" * 2048, headers=bearer(ALICE))

    assert response.status_code == 413
    assert_carries(response)


@pytest.mark.parametrize(("method", "path", "status"), [("GET", "/api/v1/nope", 404), ("DELETE", "/health", 405)])
async def test_a_404_and_a_405_carry_the_headers(client, method, path, status):
    response = await client.request(method, path, headers=bearer(ALICE))

    assert response.status_code == status
    assert_carries(response)


# ---- the event stream of the MCP endpoint keeps working --------------------------------------------------


async def test_the_event_stream_keeps_its_own_cache_control_and_gets_the_other_headers(
    monkeypatch, embedding_adapter, reranker_adapter,
):
    async with (
        streaming(monkeypatch, embedding_adapter, reranker_adapter) as app,
        AsyncClient(transport=ASGITransport(app=app), base_url=LOCAL) as http,
    ):
        response = await http.post(
            "/mcp", json=INITIALIZE, headers={"Accept": "application/json, text/event-stream", **bearer(ALICE)},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers.get_list("cache-control") == ["no-cache, no-transform"]  # the stream's own, not "no-store"
    assert_carries(response, {name: value for name, value in HEADERS.items() if name != "cache-control"})


async def test_an_mcp_client_session_works_through_the_middleware(monkeypatch, embedding_adapter, reranker_adapter):
    async with streaming(monkeypatch, embedding_adapter, reranker_adapter) as app, AuthStack(app).mcp(ALICE) as session:
        result = await session.call_tool("execute_axmem_tool", {"tool_name": "get_recent_memories", "arguments": {}})

    assert result.data == {"memories": [], "total_count": 0}
