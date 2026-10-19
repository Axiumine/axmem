"""E2E: REST routes of the resource families reject requests that carry no credentials.

With an auth provider attached to the server, every route of the activity,
and project families must answer ``401`` with the
authentication failure message, before reading the request body or touching a
service. Runs against the in-process SQLite app.
"""
import pytest
from fastmcp import Client
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from httpx import ASGITransport, AsyncClient

MISSING_CREDENTIALS_MESSAGE = "Missing or invalid Authorization header"

ACTIVITY_ROUTES = [
    ("GET", "/api/v1/activity"),
    ("GET", "/api/v1/activity/memory/1"),
    ("GET", "/api/v1/activity/stream"),
]


def _crud_routes(collection: str) -> list[tuple[str, str]]:
    """The five CRUD routes of a REST collection."""
    base = f"/api/v1/{collection}"
    return [
        ("GET", base),
        ("GET", f"{base}/1"),
        ("POST", base),
        ("PUT", f"{base}/1"),
        ("DELETE", f"{base}/1"),
    ]


PROTECTED_ROUTES = [
    *ACTIVITY_ROUTES,
    *_crud_routes("projects"),
]


@pytest.fixture
async def unauthenticated_http_client(sqlite_app):
    """HTTP client for an app whose routes require a bearer token (none is sent)."""
    sqlite_app.auth = StaticTokenVerifier(
        tokens={"valid-token": {"client_id": "test-client", "sub": "test-user"}},
    )
    async with Client(sqlite_app) as _:
        transport = ASGITransport(app=sqlite_app.http_app())
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
