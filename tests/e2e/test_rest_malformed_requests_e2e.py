"""E2E tests: malformed REST requests are client errors, never 500.

Covers non-numeric / out-of-range path ids, invalid or non-object JSON bodies,
and the shared request helpers every route module uses.

The sweeps enumerate every route registered by the covered API modules, so a
new route that forgets the shared request helpers fails here.
"""
import json
import re

import pytest
from fastmcp import Client, FastMCP
from httpx import ASGITransport, AsyncClient
from rest_helpers import MISSING_ID

from app.routes.api import memories

API_MODULES = (memories,)
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
MEMORY = {
    "title": "Well formed",
    "content": "A memory the sweeps can create.",
    "context": "malformed request tests",
    "keywords": ["rest"],
    "tags": ["e2e"],
    "importance": 5,
}


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


def _concrete(path: str, bad_param_index: int | None = None, bad_value: str = MISSING_ID) -> str:
    """Fill the path template with a well-formed id except for one parameter set to ``bad_value``."""
    counter = iter(range(path.count("{")))
    return re.sub(
        r"\{[^}]+\}",
        lambda _: bad_value if next(counter) == bad_param_index else MISSING_ID,
        path,
    )


@pytest.fixture
async def client(qdrant_app):
    """HTTP client that reports unhandled handler exceptions as 500 responses, like a server."""
    async with Client(qdrant_app):
        transport = ASGITransport(app=qdrant_app.http_app(), raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


def test_inventory_covers_the_rest_api():
    """Guard the sweeps below: they must actually see the routes."""
    assert len(ROUTES_WITH_IDS) > 4
    assert len(ROUTES_WITH_BODY) > 3


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

    async def test_valid_missing_id_is_still_a_plain_not_found(self, client):
        response = await client.get(f"/api/v1/memories/{MISSING_ID}")
        assert response.status_code == 404
        assert response.json() == {"error": "Memory not found"}

    async def test_uppercase_and_unhyphenated_ids_name_the_same_memory(self, client):
        memory = (await client.post("/api/v1/memories", json=MEMORY)).json()
        for spelling in (memory["id"].upper(), memory["id"].replace("-", "")):
            response = await client.get(f"/api/v1/memories/{spelling}")
            assert response.status_code == 200, spelling
            assert response.json()["id"] == memory["id"]


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
        body = json.dumps({**MEMORY, "confidence": 0.5}).replace("0.5", "5e-1").encode()
        response = await client.post("/api/v1/memories", content=body, headers=JSON_HEADERS)
        assert response.status_code == 201

    async def test_non_object_body_message(self, client):
        response = await client.post("/api/v1/memories", content=b"[1, 2]", headers=JSON_HEADERS)
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid request body: Request body must be a JSON object"}

    async def test_invalid_json_message(self, client):
        response = await client.post("/api/v1/memories", content=b"{bad", headers=JSON_HEADERS)
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid request body: Invalid JSON body"}

    async def test_valid_object_body_still_works(self, client):
        response = await client.post("/api/v1/memories", json=MEMORY)
        assert response.status_code == 201


class TestContentType:
    """A body that does not say it is JSON is 415: a web page can send ``text/plain`` without a preflight."""

    async def test_every_body_route_refuses_a_body_that_is_not_declared_as_json(self, client):
        failures = []
        for method, template in ROUTES_WITH_BODY:
            path = _concrete(template)
            for content_type in ("text/plain", "text/plain;charset=UTF-8", "application/x-www-form-urlencoded", ""):
                headers = {"content-type": content_type} if content_type else {}
                response = await client.request(method, path, content=json.dumps(MEMORY).encode(), headers=headers)
                if response.status_code != 415 or response.json() != {"error": "Content-Type must be application/json"}:
                    failures.append(f"{method} {path} {content_type!r} -> {response.status_code}")
        assert not failures, "\n".join(failures)

    async def test_a_text_plain_post_does_not_create_a_memory(self, client):
        refused = await client.post(
            "/api/v1/memories", content=json.dumps(MEMORY).encode(), headers={"content-type": "text/plain"},
        )
        listed = await client.get("/api/v1/memories")

        assert refused.status_code == 415
        assert listed.json()["memories"] == []

    async def test_a_delete_that_carries_a_text_body_is_refused(self, client):
        memory = (await client.post("/api/v1/memories", json=MEMORY)).json()

        refused = await client.request(
            "DELETE", f"/api/v1/memories/{memory['id']}", content=b"x", headers={"content-type": "text/plain"},
        )
        kept = await client.get(f"/api/v1/memories/{memory['id']}")

        assert refused.status_code == 415
        assert kept.json()["is_obsolete"] is False

    @pytest.mark.parametrize("content_type", ["application/json", "Application/JSON; charset=utf-8", "application/merge-patch+json"])
    async def test_json_media_types_are_accepted(self, client, content_type):
        response = await client.post(
            "/api/v1/memories", content=json.dumps(MEMORY).encode(), headers={"content-type": content_type},
        )

        assert response.status_code == 201

    async def test_a_chunked_body_that_is_not_declared_as_json_is_refused(self, client):
        """No ``Content-Length`` at all: the request still carries a body, and still has to say it is JSON."""

        async def chunks():
            yield json.dumps(MEMORY).encode()

        refused = await client.post("/api/v1/memories", content=chunks(), headers={"content-type": "text/plain"})
        accepted = await client.post("/api/v1/memories", content=chunks(), headers=JSON_HEADERS)

        assert refused.status_code == 415
        assert accepted.status_code == 201

    async def test_a_request_without_a_body_needs_no_content_type(self, client):
        memory = (await client.post("/api/v1/memories", json=MEMORY)).json()

        assert (await client.get(f"/api/v1/memories/{memory['id']}")).status_code == 200
        assert (await client.delete(f"/api/v1/memories/{memory['id']}")).status_code == 200
        assert (await client.post("/api/v1/memories/search")).status_code == 400  # body missing, content type irrelevant
