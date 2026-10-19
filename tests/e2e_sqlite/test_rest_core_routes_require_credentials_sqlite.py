"""SQLite end-to-end test: the memory and entity REST routes need credentials.

With an auth provider configured, every endpoint must answer 401 with a JSON error before
it touches any data, and a valid bearer token must still be accepted.
"""
import pytest
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

VALID_TOKEN = "valid-core-routes-token"

# (method, path) of every route served by the memories and entities modules
PROTECTED_ROUTES = [
    ("GET", "/api/v1/memories"),
    ("GET", "/api/v1/memories/1"),
    ("POST", "/api/v1/memories"),
    ("PUT", "/api/v1/memories/1"),
    ("DELETE", "/api/v1/memories/1"),
    ("POST", "/api/v1/memories/search"),
    ("POST", "/api/v1/memories/1/links"),
    ("GET", "/api/v1/memories/1/links"),
    ("DELETE", "/api/v1/memories/1/links/2"),
    ("GET", "/api/v1/entities"),
    ("GET", "/api/v1/entities/1"),
    ("POST", "/api/v1/entities"),
    ("PUT", "/api/v1/entities/1"),
    ("DELETE", "/api/v1/entities/1"),
    ("POST", "/api/v1/entities/search"),
    ("POST", "/api/v1/entities/1/memories"),
    ("DELETE", "/api/v1/entities/1/memories/2"),
    ("GET", "/api/v1/entities/1/memories"),
    ("GET", "/api/v1/entities/1/relationships"),
    ("POST", "/api/v1/entities/1/relationships"),
    ("PUT", "/api/v1/entities/relationships/1"),
    ("DELETE", "/api/v1/entities/relationships/1"),
]


@pytest.fixture
def credentials_required(sqlite_app, monkeypatch):
    """Turn on bearer-token auth for the running app (one static token is valid)."""
    verifier = StaticTokenVerifier(
        tokens={VALID_TOKEN: {"client_id": "core-routes-client", "sub": "core-routes-user"}},
    )
    monkeypatch.setattr(sqlite_app, "auth", verifier)


@pytest.mark.parametrize(
    ("method", "path"), PROTECTED_ROUTES, ids=[f"{m} {p}" for m, p in PROTECTED_ROUTES],
)
async def test_route_rejects_request_without_credentials(
    http_client, credentials_required, method, path,
):
    response = await http_client.request(method, path)

    assert response.status_code == 401
    assert response.json() == {"error": "Missing or invalid Authorization header"}


async def test_route_still_serves_request_with_valid_token(http_client, credentials_required):
    response = await http_client.get(
        "/api/v1/entities", headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )

    assert response.status_code == 200
    assert response.json()["entities"] == []
