# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests: the credential-free server cannot be driven by a web page.

``AUTH_MODE=none`` serves every request as the one local tenant, and the loopback bind is its only protection.
A page can still reach a loopback address: by DNS rebinding (the request carries the page's own host name) or by
a cross-origin ``no-cors`` request (no preflight; the body is ``text/plain``). The HTTP app is built the way
``main._serve`` builds it, with the middleware of ``main._http_middleware``, over the real routes and a real
Qdrant node.
"""
import json

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from httpx import ASGITransport, AsyncClient

import main

LOCAL = "http://127.0.0.1:8020"
MEMORY = {
    "title": "Guard probe",
    "content": "A memory that only the local operator may create or read.",
    "context": "loopback guard e2e test",
    "keywords": ["guard"],
    "tags": ["e2e"],
    "importance": 5,
}
PAGE = "https://evil.example"


@pytest.fixture
async def guarded(qdrant_app):
    """The HTTP app of ``AUTH_MODE=none`` with its middleware, and a client that reaches it as the local operator."""
    async with Client(qdrant_app):
        app = qdrant_app.http_app(middleware=main._http_middleware())
        async with AsyncClient(transport=ASGITransport(app=app), base_url=LOCAL) as client:
            yield client


async def test_the_local_operator_is_served(guarded):
    created = await guarded.post("/api/v1/memories", json=MEMORY)
    listed = await guarded.get("/api/v1/memories")

    assert created.status_code == 201
    assert [memory["id"] for memory in listed.json()["memories"]] == [created.json()["id"]]


async def test_a_local_page_on_another_port_is_served(guarded):
    response = await guarded.get("/api/v1/memories", headers={"Origin": "http://localhost:5173"})

    assert response.status_code == 200


async def test_a_rebound_host_cannot_read_the_memories(guarded):
    await guarded.post("/api/v1/memories", json=MEMORY)

    rebound = await guarded.get("/api/v1/memories", headers={"Host": "rebind.evil.example:8020"})

    assert rebound.status_code == 421
    assert "memories" not in rebound.json()


async def test_a_rebound_host_cannot_write(guarded):
    refused = await guarded.post("/api/v1/memories", json=MEMORY, headers={"Host": "rebind.evil.example:8020"})
    listed = await guarded.get("/api/v1/memories")

    assert refused.status_code == 421
    assert listed.json()["memories"] == []


async def test_a_no_cors_post_from_another_site_plants_nothing(guarded):
    """What ``fetch(url, {method: 'POST', mode: 'no-cors', body})`` sends: no preflight, ``text/plain``, an Origin."""
    forged = await guarded.post(
        "/api/v1/memories",
        content=json.dumps(MEMORY).encode(),
        headers={"Origin": PAGE, "Content-Type": "text/plain;charset=UTF-8"},
    )
    listed = await guarded.get("/api/v1/memories")

    assert forged.status_code == 403
    assert listed.json()["memories"] == []


async def test_a_cross_origin_delete_removes_nothing(guarded):
    created = (await guarded.post("/api/v1/memories", json=MEMORY)).json()

    refused = await guarded.delete(f"/api/v1/memories/{created['id']}", headers={"Origin": PAGE})
    kept = await guarded.get(f"/api/v1/memories/{created['id']}")

    assert refused.status_code == 403
    assert kept.json()["is_obsolete"] is False


async def test_a_text_body_without_an_origin_is_refused_by_the_route_itself(guarded):
    """Not a browser (it sends no Origin): the guard lets it pass, and the content type check refuses it."""
    response = await guarded.post(
        "/api/v1/memories", content=json.dumps(MEMORY).encode(), headers={"Content-Type": "text/plain"},
    )

    assert response.status_code == 415


@pytest.mark.parametrize(("headers", "status"), [
    ({"Host": "rebind.evil.example:8020"}, 421),
    ({"Origin": PAGE}, 403),
    ({"Origin": "null"}, 403),
])
async def test_the_mcp_endpoint_is_guarded_like_the_rest_routes(guarded, headers, status):
    initialize = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "page", "version": "1"}},
    }

    response = await guarded.post(
        "/mcp", json=initialize, headers={"Accept": "application/json, text/event-stream", **headers},
    )

    assert response.status_code == status


async def test_an_mcp_client_on_the_loopback_address_still_connects(qdrant_app):
    app = qdrant_app.http_app(middleware=main._http_middleware())

    def client_factory(headers=None, timeout=None, auth=None, **_ignored):
        return AsyncClient(
            transport=ASGITransport(app=app), base_url=LOCAL, headers=headers, timeout=timeout, auth=auth,
        )

    async with app.router.lifespan_context(app):
        transport = StreamableHttpTransport(f"{LOCAL}/mcp", httpx_client_factory=client_factory)
        async with Client(transport) as client:
            result = await client.call_tool(
                "execute_axmem_tool", {"tool_name": "get_recent_memories", "arguments": {}},
            )

    assert result.data == {"memories": [], "total_count": 0}
