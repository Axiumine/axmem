"""E2E tests: the REST routes obey ``AXMEM_SCOPES`` and the scopes of the caller's token.

The MCP meta-tools always refused a tool that the instance ceiling or the token's scopes did not permit; the REST
routes reach the same services and used to ignore both. A route now stands for the registry tool that does the same
work (``ROUTE_TOOLS``), and answers 403 exactly when a call to that tool over MCP would be refused.

Real Qdrant, real tool registry, real scope resolver, and for the token cases real signed JWTs checked by the
verifier of ``AUTH_MODE=jwt``.
"""
import re
from contextlib import asynccontextmanager

import pytest
from fastmcp import Client
from fastmcp.server.auth.providers.jwt import RSAKeyPair
from rest_helpers import MISSING_ID, create_memory

from app.config.settings import settings
from app.middleware.auth import TokenCache
from app.middleware.token_verifiers import ExpiringJWTVerifier
from app.routes.api.access import ROUTE_TOOLS
from app.routes.mcp.scope_resolver import parse_scopes, resolve_permitted_tools
from tests.e2e.conftest import AuthStack, auth_stack, build_app

ISSUER = "https://idp.example.test"
AUDIENCE = "axmem-test"
MEMORY = {
    "title": "Scope probe",
    "content": "A memory that the REST scope checks protect.",
    "context": "rest scope e2e test",
    "keywords": ["scope"],
    "tags": ["e2e"],
    "importance": 5,
}
# Routes that read no memory: no credential and no scope is needed
PUBLIC_ROUTES = {"/", "/health", "/api/v1/auth/info"}
IGNORED_METHODS = {"HEAD", "OPTIONS"}

READS = [
    ("GET", "/api/v1/memories", None),
    ("GET", "/api/v1/memories/{id}", None),
    ("GET", "/api/v1/memories/{id}/links", None),
    ("POST", "/api/v1/memories/search", {"query": "scope probe", "query_context": "rest scope e2e test"}),
]
WRITES = [
    ("POST", "/api/v1/memories", MEMORY),
    ("PUT", "/api/v1/memories/{id}", {"title": "Changed"}),
    ("DELETE", "/api/v1/memories/{id}", None),
    ("POST", "/api/v1/memories/{id}/links", {"related_ids": [MISSING_ID]}),
    ("DELETE", "/api/v1/memories/{id}/links/" + MISSING_ID, None),
]


def restrict(app, scopes: str) -> None:
    """Set the instance ceiling of a running app, the way its lifespan does from ``AXMEM_SCOPES``."""
    ceiling = parse_scopes(scopes)
    app._instance_permitted_tools = resolve_permitted_tools(ceiling, app.registry)
    app._instance_scopes = ceiling


async def send(client, method, path, body, memory_id, headers=None):
    """Send one request of ``READS`` or ``WRITES`` for the memory ``memory_id``."""
    target = path.replace("{id}", memory_id)
    if body is None:
        return await client.request(method, target, headers=headers)
    return await client.request(method, target, json=body, headers=headers)


@pytest.fixture
async def stored(qdrant_app, http_client):
    """The id of a memory created while the ceiling still allowed everything."""
    return await create_memory(http_client, **{k: v for k, v in MEMORY.items() if k != "importance"})


# ============================================
# The instance ceiling (AXMEM_SCOPES)
# ============================================


@pytest.mark.parametrize(("method", "path", "body"), READS, ids=[f"{m} {p}" for m, p, _ in READS])
async def test_a_write_only_ceiling_refuses_the_read_routes(qdrant_app, http_client, stored, method, path, body):
    restrict(qdrant_app, "write:memories")

    response = await send(http_client, method, path, body, stored)

    assert response.status_code == 403
    assert "not permitted under current scopes" in response.json()["error"]
    assert "read:memories" in response.json()["error"]


@pytest.mark.parametrize(("method", "path", "body"), WRITES, ids=[f"{m} {p}" for m, p, _ in WRITES])
async def test_a_read_only_ceiling_refuses_the_write_routes(qdrant_app, http_client, stored, method, path, body):
    restrict(qdrant_app, "read")

    response = await send(http_client, method, path, body, stored)

    assert response.status_code == 403
    assert "not permitted under current scopes" in response.json()["error"]
    assert "write:memories" in response.json()["error"]


async def test_a_read_only_ceiling_leaves_the_data_as_it_was(qdrant_app, http_client, stored):
    restrict(qdrant_app, "read")

    for method, path, body in WRITES:
        assert (await send(http_client, method, path, body, stored)).status_code == 403

    listed = (await http_client.get("/api/v1/memories")).json()
    assert [memory["id"] for memory in listed["memories"]] == [stored]
    memory = (await http_client.get(f"/api/v1/memories/{stored}")).json()
    assert memory["title"] == MEMORY["title"]
    assert memory["is_obsolete"] is False


@pytest.mark.parametrize(("method", "path", "body"), READS, ids=[f"{m} {p}" for m, p, _ in READS])
async def test_a_read_only_ceiling_serves_the_read_routes(qdrant_app, http_client, stored, method, path, body):
    restrict(qdrant_app, "read")

    response = await send(http_client, method, path, body, stored)

    assert response.status_code == 200, response.text


async def test_a_write_only_ceiling_serves_the_write_routes(qdrant_app, http_client):
    restrict(qdrant_app, "write")

    created = await http_client.post("/api/v1/memories", json=MEMORY)
    changed = await http_client.put(f"/api/v1/memories/{created.json()['id']}", json={"title": "Changed"})
    obsolete = await http_client.delete(f"/api/v1/memories/{created.json()['id']}")

    assert (created.status_code, changed.status_code, obsolete.status_code) == (201, 200, 200)


async def test_the_default_ceiling_serves_everything(qdrant_app, http_client):
    created = await http_client.post("/api/v1/memories", json=MEMORY)

    assert created.status_code == 201
    assert (await http_client.get(f"/api/v1/memories/{created.json()['id']}")).status_code == 200


async def test_the_rest_route_and_the_mcp_tool_are_refused_alike(qdrant_app, http_client):
    restrict(qdrant_app, "read")

    over_rest = await http_client.post("/api/v1/memories", json=MEMORY)
    async with Client(qdrant_app) as client:
        over_mcp = await client.call_tool(
            "execute_axmem_tool", {"tool_name": "create_memory", "arguments": dict(MEMORY)}, raise_on_error=False,
        )

    assert over_rest.status_code == 403
    assert over_mcp.is_error
    assert over_rest.json()["error"] in str(over_mcp.content)


async def test_the_credential_is_checked_before_the_scopes(qdrant_app, http_client, monkeypatch):
    """A caller without a valid credential learns nothing about the ceiling: 401, not 403."""
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    restrict(qdrant_app, "read")
    monkeypatch.setattr(qdrant_app, "auth", StaticTokenVerifier(tokens={"known": {"client_id": "c", "sub": "u"}}))

    response = await http_client.post("/api/v1/memories", json=MEMORY)

    assert response.status_code == 401


# ============================================
# Every route is covered
# ============================================


def _application_routes(app) -> list[tuple[str, str]]:
    """(method, path template) of every route of the HTTP app that serves anything but the MCP endpoint."""
    found = set()
    for route in app.http_app().routes:
        path = getattr(route, "path", "")
        if path == "/mcp" or path in PUBLIC_ROUTES:
            continue
        found.update((method, path) for method in route.methods - IGNORED_METHODS)
    return sorted(found)


async def test_every_route_of_the_application_is_mapped_to_a_tool(qdrant_app):
    unmapped = [route for route in _application_routes(qdrant_app) if route not in ROUTE_TOOLS]

    assert not unmapped, f"routes that skip the scope check: {unmapped}"
    assert set(ROUTE_TOOLS) == set(_application_routes(qdrant_app)), "ROUTE_TOOLS names a route that does not exist"


async def test_every_route_answers_403_when_the_ceiling_permits_an_unrelated_tool_only(qdrant_app, http_client):
    """A new route that forgot ``rest_route`` would answer something else here (or serve the request)."""
    qdrant_app._instance_permitted_tools = {"rebuild_embeddings"}
    qdrant_app._instance_scopes = frozenset({"write"})
    answers = {}

    for method, template in _application_routes(qdrant_app):
        path = re.sub(r"\{[^}]+\}", MISSING_ID, template)
        response = await http_client.request(method, path, json={} if method in {"POST", "PUT"} else None)
        answers[f"{method} {template}"] = response.status_code

    assert answers
    assert set(answers.values()) == {403}, answers


async def test_the_public_routes_need_no_scope(qdrant_app, http_client):
    qdrant_app._instance_permitted_tools = {"rebuild_embeddings"}
    qdrant_app._instance_scopes = frozenset({"write"})

    assert (await http_client.get("/health")).status_code == 200
    assert (await http_client.get("/api/v1/auth/info")).status_code == 200


async def test_the_mapped_tools_are_registered_and_read_or_write_as_their_route_does(qdrant_app, http_client):
    registry = qdrant_app.registry
    for (method, path), tool in ROUTE_TOOLS.items():
        registered = registry.get_tool(tool)
        assert registered is not None, f"{method} {path} names a tool that does not exist: {tool}"
        reads = method == "GET" or path.endswith("/search")
        assert registered.metadata.mutates is not reads, f"{method} {path} -> {tool}"


# ============================================
# The scopes of the token narrow the ceiling
# ============================================


@pytest.fixture(scope="module")
def keys() -> RSAKeyPair:
    return RSAKeyPair.generate()


def _token(keys: RSAKeyPair, subject: str = "alice", **claims) -> str:
    return keys.create_token(subject=subject, issuer=ISSUER, audience=AUDIENCE, additional_claims=claims)


def _verifier(keys: RSAKeyPair) -> ExpiringJWTVerifier:
    return ExpiringJWTVerifier(public_key=keys.public_key, issuer=ISSUER, audience=AUDIENCE)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _statuses(stack, token, memory_id) -> dict[str, int]:
    """The status of each route group for a bearer token, one entry per route."""
    out = {}
    async with stack.rest(token) as client:
        for method, path, body in READS + WRITES:
            if method == "DELETE" and "links" not in path:
                continue  # keep the memory in place for the routes that follow
            response = await send(client, method, path, body, memory_id)
            out[f"{method} {path}"] = response.status_code
    return out


@pytest.fixture
def jwt_stack(embedding_adapter, reranker_adapter, keys):
    """Opens an app behind ``AUTH_MODE=jwt``: ``async with jwt_stack() as stack``.

    The stack is opened in the test itself: the lifespan of the HTTP app must be left by the task that entered it.
    """
    return lambda: auth_stack(embedding_adapter, reranker_adapter, _verifier(keys))


@asynccontextmanager
async def _create_in(stack, token):
    async with stack.rest(token) as client:
        response = await client.post("/api/v1/memories", json=MEMORY)
    assert response.status_code == 201, response.text
    yield response.json()["id"]


async def test_a_read_scoped_token_reads_but_does_not_write(jwt_stack, keys):
    async with jwt_stack() as stack:
        async with _create_in(stack, _token(keys)) as memory_id:
            statuses = await _statuses(stack, _token(keys, scope="read"), memory_id)

    reads = {f"{m} {p}" for m, p, _ in READS}
    assert {route for route, status in statuses.items() if status == 200} == reads
    assert {status for route, status in statuses.items() if route not in reads} == {403}


async def test_a_write_scoped_token_writes_but_does_not_read(jwt_stack, keys):
    async with jwt_stack() as stack:
        async with _create_in(stack, _token(keys)) as memory_id:
            statuses = await _statuses(stack, _token(keys, scope="write:memories"), memory_id)

    reads = {f"{m} {p}" for m, p, _ in READS}
    assert {status for route, status in statuses.items() if route in reads} == {403}
    assert 403 not in {status for route, status in statuses.items() if route not in reads}


@pytest.mark.parametrize("claims", [
    pytest.param({"scope": "read:memories"}, id="scope-string"),
    pytest.param({"scp": ["read"]}, id="scp-list"),
    pytest.param({"scope": "openid email read"}, id="with-oidc-scopes"),
])
async def test_the_scope_claim_may_take_each_shape_a_provider_uses(jwt_stack, keys, claims):
    async with jwt_stack() as stack, stack.rest(_token(keys, **claims)) as client:
        read = await client.get("/api/v1/memories")
        write = await client.post("/api/v1/memories", json=MEMORY)

    assert (read.status_code, write.status_code) == (200, 403)


async def test_a_token_without_a_scope_claim_gets_the_whole_ceiling(jwt_stack, keys):
    async with jwt_stack() as stack, stack.rest(_token(keys)) as client:
        read = await client.get("/api/v1/memories")
        write = await client.post("/api/v1/memories", json=MEMORY)

    assert (read.status_code, write.status_code) == (200, 201)


async def test_a_token_whose_scopes_are_none_of_axmems_permits_nothing(jwt_stack, keys):
    async with jwt_stack() as stack, stack.rest(_token(keys, scope="openid profile")) as client:
        read = await client.get("/api/v1/memories")
        write = await client.post("/api/v1/memories", json=MEMORY)

    assert (read.status_code, write.status_code) == (403, 403)


async def test_a_token_cannot_widen_the_instance_ceiling(embedding_adapter, reranker_adapter, keys, monkeypatch):
    monkeypatch.setattr(settings, "AXMEM_SCOPES", "read")
    async with auth_stack(embedding_adapter, reranker_adapter, _verifier(keys)) as stack:
        async with stack.rest(_token(keys, scope="read write")) as client:
            read = await client.get("/api/v1/memories")
            write = await client.post("/api/v1/memories", json=MEMORY)
        async with stack.rest(_token(keys, scope="write")) as client:
            only_write = await client.get("/api/v1/memories")

    assert (read.status_code, write.status_code) == (200, 403)
    assert only_write.status_code == 403


@asynccontextmanager
async def _cached_jwt_stack(embedding_adapter, reranker_adapter, keys):
    """A JWT app with the REST token cache of the server attached (``main.lifespan`` does this for jwt)."""
    async for app in build_app(embedding_adapter, reranker_adapter, auth_provider=_verifier(keys)):
        app.token_cache = TokenCache()
        http_app = app.http_app()
        async with http_app.router.lifespan_context(http_app):
            yield AuthStack(http_app), app.token_cache


async def test_a_cached_token_keeps_its_scopes(embedding_adapter, reranker_adapter, keys):
    """The cache kept the tenant only, so a hit would have stood for a token with every scope of the ceiling."""
    reader = _token(keys, scope="read")
    writer = _token(keys, scope="read write")

    async with _cached_jwt_stack(embedding_adapter, reranker_adapter, keys) as (stack, cache):
        async with stack.rest() as client:
            answers = [
                (await client.post("/api/v1/memories", json=MEMORY, headers=_bearer(token))).status_code
                for token in (reader, reader, writer, reader, writer)
            ]
            listed = await client.get("/api/v1/memories", headers=_bearer(reader))

    assert answers == [403, 403, 201, 403, 201]
    assert listed.status_code == 200
    assert cache.stats["hits"] >= 3  # the repeated requests were served from the cache


async def test_the_scoped_rest_route_and_the_mcp_tool_agree_for_the_same_token(jwt_stack, keys):
    token = _token(keys, scope="read")

    async with jwt_stack() as stack:
        async with stack.rest(token) as client:
            over_rest = await client.post("/api/v1/memories", json=MEMORY)
        async with stack.mcp(token) as client:
            over_mcp = await client.call_tool(
                "execute_axmem_tool", {"tool_name": "create_memory", "arguments": dict(MEMORY)}, raise_on_error=False,
            )

    assert over_rest.status_code == 403
    assert over_mcp.is_error
