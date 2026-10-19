"""E2E tests (SQLite): malformed REST requests are client errors, never 500.

Covers non-numeric / out-of-range path ids, invalid or non-object JSON bodies,
and the shared request helpers every route module uses.

The sweeps enumerate every route registered by the covered API modules, so a
new route that forgets the shared request helpers fails here.
"""
import re

import pytest
from fastmcp import Client, FastMCP
from httpx import ASGITransport, AsyncClient

from app.routes.api import (
    code_artifacts,
    documents,
    entities,
    files,
    memories,
    projects,
    skills,
)

API_MODULES = (
    code_artifacts, documents, entities, files, memories, projects, skills,
)
IGNORED_METHODS = {"HEAD", "OPTIONS"}
BAD_IDS = [
    "abc",
    "1.5",
    "-1",
    "1_0",
    "99999999999999999999",
    "2147483648",
    "9" * 5000,
]
BAD_BODIES = [
    b"{bad",
    b"",
    b"[1, 2]",
    b"null",
    b'"abc"',
    b"\xff\xfe\xfd",
    b"[" * 100_000,
]
# NaN and infinite numbers (not JSON) inside an otherwise well-formed object
NON_FINITE_BODIES = [
    b'{"title": NaN}',
    b'{"title": Infinity}',
    b'{"title": [NaN]}',
    b'{"a": {"b": -Infinity}}',
    b'{"title": 1e999}',
    b'{"title": -1e999}',
]
JSON_HEADERS = {"content-type": "application/json"}


def _inventory() -> list[tuple[str, str]]:
    """(method, path template) of every REST route registered by the covered modules."""
    server = FastMCP("route-inventory")
    for module in API_MODULES:
        module.register(server)
    found = set()
    for route in server.http_app().routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/v1/"):
            found.update((method, path) for method in route.methods - IGNORED_METHODS)
    return sorted(found)


ROUTES = _inventory()
ROUTES_WITH_IDS = [(m, p) for m, p in ROUTES if "{" in p]
ROUTES_WITH_BODY = [(m, p) for m, p in ROUTES if m in {"POST", "PUT"}]


def _concrete(path: str, bad_param_index: int | None = None, bad_value: str = "1") -> str:
    """Fill the path template with ``1`` except for one parameter set to ``bad_value``."""
    counter = iter(range(path.count("{")))
    return re.sub(
        r"\{[^}]+\}",
        lambda _: bad_value if next(counter) == bad_param_index else "1",
        path,
    )


@pytest.fixture
async def client(sqlite_app):
    """HTTP client that reports unhandled handler exceptions as 500 responses, like a server."""
    async with Client(sqlite_app):
        transport = ASGITransport(app=sqlite_app.http_app(), raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


def test_inventory_covers_the_rest_api():
    """Guard the sweeps below: they must actually see the routes."""
    assert len(ROUTES_WITH_IDS) > 35
    assert len(ROUTES_WITH_BODY) > 20


class TestPathIds:
    """Path ids that cannot name a resource are 404, not 500."""

    async def test_every_id_route_rejects_bad_ids(self, client):
        failures = []
        for method, template in ROUTES_WITH_IDS:
            for index in range(template.count("{")):
                for bad in BAD_IDS:
                    path = _concrete(template, index, bad)
                    response = await client.request(method, path, json={})
                    if response.status_code != 404 or not response.json()["error"].startswith("Invalid "):
                        failures.append(f"{method} {path[:80]} -> {response.status_code}")
        assert not failures, "\n".join(failures)

    async def test_entity_search_is_not_swallowed_as_an_entity_id(self, client):
        response = await client.get("/api/v1/entities/search")
        assert response.status_code == 404

    async def test_valid_missing_id_is_still_a_plain_not_found(self, client):
        response = await client.get("/api/v1/projects/999999")
        assert response.status_code == 404
        assert response.json() == {"error": "Project not found"}

    async def test_leading_zero_id_is_accepted(self, client):
        project = (await client.post(
            "/api/v1/projects",
            json={"name": "zero", "description": "p", "project_type": "development"},
        )).json()
        response = await client.get(f"/api/v1/projects/00{project['id']}")
        assert response.status_code == 200


class TestJsonBodies:
    """Invalid, empty, non-object and deeply nested JSON bodies are 400, not 500."""

    async def test_every_body_route_rejects_bad_bodies(self, client):
        failures = []
        for method, template in ROUTES_WITH_BODY:
            path = _concrete(template)
            for body in BAD_BODIES:
                response = await client.request(method, path, content=body, headers=JSON_HEADERS)
                if response.status_code != 400 or "error" not in response.json():
                    failures.append(f"{method} {path} body={body[:20]!r} -> {response.status_code}")
        assert not failures, "\n".join(failures)

    async def test_every_body_route_rejects_non_finite_numbers(self, client):
        """NaN/infinity must not reach pydantic: echoing them in its error made a 500."""
        failures = []
        for method, template in ROUTES_WITH_BODY:
            path = _concrete(template)
            for body in NON_FINITE_BODIES:
                response = await client.request(method, path, content=body, headers=JSON_HEADERS)
                if response.status_code != 400 or "error" not in response.json():
                    failures.append(f"{method} {path} body={body[:30]!r} -> {response.status_code}")
        assert not failures, "\n".join(failures)

    async def test_finite_floats_are_still_accepted(self, client):
        response = await client.post(
            "/api/v1/projects",
            content=b'{"name": "f", "description": "p", "project_type": "development", "x": 1.5e3}',
        )
        assert response.status_code == 201

    async def test_non_object_body_message(self, client):
        response = await client.post("/api/v1/projects", content=b"[1, 2]")
        assert response.status_code == 400
        assert response.json() == {"error": "Request body must be a JSON object"}

    async def test_invalid_json_message(self, client):
        response = await client.post("/api/v1/projects", content=b"{bad")
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid JSON body"}

    async def test_valid_object_body_still_works(self, client):
        response = await client.post(
            "/api/v1/projects",
            json={"name": "ok", "description": "p", "project_type": "development"},
        )
        assert response.status_code == 201
