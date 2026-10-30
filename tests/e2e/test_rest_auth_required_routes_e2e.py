# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E: REST routes of the memories family reject requests that carry no credentials.

With an auth provider attached to the server, every memory route must answer ``401`` with
the authentication failure message, before reading the request body or touching a service.
The route list is read from the registered routes, so a new route that forgets the check
fails here. Runs against the in-process app.
"""
import re

import pytest
from fastmcp import Client, FastMCP
from httpx import ASGITransport, AsyncClient

from app.middleware.static_token import StaticTokenVerifier, hash_token
from app.routes.api import memories

MISSING_CREDENTIALS_MESSAGE = "Missing or invalid Authorization header"
IGNORED_METHODS = {"HEAD", "OPTIONS"}


def _protected_routes() -> list[tuple[str, str]]:
    """(method, concrete path) of every REST route registered by the memories module."""
    server = FastMCP("route-inventory")
    memories.register(server)
    found = set()
    for route in server.http_app().routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/v1/"):
            concrete = re.sub(r"\{[^}]+\}", "1", path)
            found.update((method, concrete) for method in route.methods - IGNORED_METHODS)
    return sorted(found)


PROTECTED_ROUTES = _protected_routes()


def test_inventory_covers_the_memory_routes():
    """Guard the sweep below: it must actually see the routes."""
    assert len(PROTECTED_ROUTES) >= 9


@pytest.fixture
async def unauthenticated_http_client(qdrant_app):
    """HTTP client for an app whose routes require a bearer token (none is sent)."""
    qdrant_app.auth = StaticTokenVerifier({"alice": hash_token("alice-secret-token-value")})
    async with Client(qdrant_app) as _:
        transport = ASGITransport(app=qdrant_app.http_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.mark.parametrize(
    ("method", "path"),
    PROTECTED_ROUTES,
    ids=[f"{method} {path}" for method, path in PROTECTED_ROUTES],
)
async def test_route_without_bearer_token_is_rejected_with_401(
    unauthenticated_http_client, method, path,
):
    response = await unauthenticated_http_client.request(method, path, json={})

    assert response.status_code == 401
    assert response.json() == {"error": MISSING_CREDENTIALS_MESSAGE}
