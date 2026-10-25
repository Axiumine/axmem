"""Integration tests for the access checks of the REST routes (app/routes/api/access.py)

A REST route stands for the registry tool that does the same work, and answers like a call to that tool over MCP:
401 for a missing or invalid credential, 403 when the instance ceiling (``AXMEM_SCOPES``) or the scopes of the
token do not permit the tool, 415 when a body is not declared as JSON. The routes are registered through
``rest_route`` on a real server with the real tool registry, and driven through the ASGI application.
"""
from uuid import uuid4

import httpx
import pytest
from fastmcp.server.auth import AccessToken, TokenVerifier
from starlette.responses import JSONResponse

from app.config.settings import settings
from app.exceptions import ConflictError
from app.routes.api.access import ROUTE_TOOLS, authorize_request, rest_route
from app.routes.api.request_utils import RequestError
from app.routes.mcp.scope_resolver import parse_scopes, resolve_permitted_tools
from app.routes.mcp.tool_metadata_registry import register_all_tools_metadata
from app.routes.mcp.tool_registry import ToolRegistry
from app.routes.server_state import AxmemMCP
from app.services.memory_service import MemoryService
from app.services.re_embedding_service import ReEmbeddingService

# (method, path, the tool the route stands for, the scope that tool needs)
ROUTES = [
    ("GET", "/api/v1/memories", "get_recent_memories", "read:memories"),
    ("GET", "/api/v1/memories/{memory_id}", "get_memory", "read:memories"),
    ("POST", "/api/v1/memories", "create_memory", "write:memories"),
    ("PUT", "/api/v1/memories/{memory_id}", "update_memory", "write:memories"),
    ("DELETE", "/api/v1/memories/{memory_id}", "mark_memory_obsolete", "write:memories"),
    ("POST", "/api/v1/memories/search", "query_memory", "read:memories"),
    ("POST", "/api/v1/memories/{memory_id}/links", "link_memories", "write:memories"),
    ("GET", "/api/v1/memories/{memory_id}/links", "get_memory", "read:memories"),
    ("DELETE", "/api/v1/memories/{memory_id}/links/{target_id}", "unlink_memories", "write:memories"),
]
IDS = [f"{method} {path}" for method, path, _, _ in ROUTES]

TOKENS = {
    "everything": {"tenant_id": "acme"},
    "reader": {"tenant_id": "acme", "scope": "read"},
    "memory-writer": {"tenant": "acme", "scp": ["write:memories"]},
    "foreign-scopes": {"sub": "acme", "scope": "openid profile"},
    "no-tenant": {"scope": "read"},
}


class FixedTokens(TokenVerifier):
    """The auth provider of the server: a fixed table of the claims that each bearer token carries."""

    async def verify_token(self, token: str) -> AccessToken | None:
        claims = TOKENS.get(token)
        if claims is None:
            return None
        return AccessToken(token=token, client_id="client", scopes=[], expires_at=None, claims=claims)


@pytest.fixture
def registry(memory_repository, tensorrt_embedder):
    """The tool registry of the application, over the real Qdrant repository."""
    tools = ToolRegistry()
    register_all_tools_metadata(
        registry=tools,
        memory_service=MemoryService(memory_repository),
        re_embedding_service=ReEmbeddingService(memory_repository, tensorrt_embedder),
    )
    return tools


def _base(registry: ToolRegistry, ceiling: str = "*", auth: TokenVerifier | None = None) -> AxmemMCP:
    """A server with the registry and the instance ceiling the lifespan attaches, and no route yet."""
    mcp = AxmemMCP("rest-access-contract", auth=auth)
    mcp.registry = registry
    scopes = parse_scopes(ceiling)
    mcp._instance_permitted_tools = resolve_permitted_tools(scopes, registry)
    mcp._instance_scopes = scopes
    return mcp


def _answering(tool: str):
    async def handler(request, tenant):
        return JSONResponse({"tool": tool, "tenant": tenant.name})

    return handler


def _server(registry: ToolRegistry, ceiling: str = "*", auth: TokenVerifier | None = None) -> AxmemMCP:
    """A server with every REST route behind ``rest_route``; each handler answers with the tenant it was given."""
    mcp = _base(registry, ceiling, auth)
    for method, path, tool, _ in ROUTES:
        rest_route(mcp, method, path)(_answering(tool))
    return mcp


def _client(mcp: AxmemMCP) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp.http_app()), base_url="http://127.0.0.1:8123")


def _url(path: str) -> str:
    return path.format(memory_id=uuid4(), target_id=uuid4())


def _bearer(token: str | None) -> dict[str, str]:
    return {} if token is None else {"Authorization": f"Bearer {token}"}


def _refusal(tool: str, scope: str) -> dict[str, str]:
    return {"error": f"Tool '{tool}' is not permitted under current scopes. Required scope: '{scope}'"}


def _served(tool: str) -> tuple[int, dict[str, str]]:
    return 200, {"tool": tool, "tenant": "acme"}


async def _answers(mcp: AxmemMCP, headers: dict[str, str]) -> dict[str, tuple[int, dict]]:
    """What every route of the table answers to a request with ``headers``, by route."""
    answers = {}
    async with _client(mcp) as http:
        for method, path, _, _ in ROUTES:
            response = await http.request(method, _url(path), headers=headers)
            answers[f"{method} {path}"] = (response.status_code, response.json())
    return answers


def _expected(permitted_scopes: set[str]) -> dict[str, tuple[int, dict]]:
    """Each route is served when the scope its tool needs is among ``permitted_scopes``, and refused otherwise."""
    return {
        f"{method} {path}": _served(tool) if scope in permitted_scopes else (403, _refusal(tool, scope))
        for method, path, tool, scope in ROUTES
    }


def test_every_route_of_the_table_stands_for_a_tool_of_the_registry():
    assert ROUTE_TOOLS == {(method, path): tool for method, path, tool, _ in ROUTES}


def test_a_route_without_an_entry_cannot_be_registered(registry):
    with pytest.raises(KeyError):
        rest_route(_base(registry), "PATCH", "/api/v1/memories")
    with pytest.raises(KeyError):
        rest_route(_base(registry), "GET", "/api/v1/forgotten")


# ============================================
# The credential: 401
# ============================================


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        ({}, "Missing or invalid Authorization header"),
        ({"Authorization": "Basic dXNlcjpwYXNz"}, "Missing or invalid Authorization header"),
        ({"Authorization": "Bearer unknown-token"}, "Invalid or expired token"),
        ({"Authorization": "Bearer no-tenant"}, "Token contains no 'tenant_id', 'tenant' or 'sub' claim"),
    ],
    ids=["no-header", "other-scheme", "unknown-token", "token-without-tenant"],
)
async def test_a_request_without_a_valid_credential_is_a_401_that_says_why_on_every_route(registry, headers, message):
    answers = await _answers(_server(registry, auth=FixedTokens()), headers)

    assert answers == {f"{method} {path}": (401, {"error": message}) for method, path, _, _ in ROUTES}


async def test_the_credential_is_checked_before_the_tool_and_the_body(registry):
    async with _client(_server(registry, ceiling="read", auth=FixedTokens())) as http:
        response = await http.post("/api/v1/memories", content=b"plain", headers={"Content-Type": "text/plain"})

    assert (response.status_code, response.json()) == (401, {"error": "Missing or invalid Authorization header"})


# ============================================
# The tool: 403
# ============================================


async def test_a_credential_without_a_scope_claim_gets_the_whole_instance_ceiling(registry):
    answers = await _answers(_server(registry, auth=FixedTokens()), _bearer("everything"))

    assert answers == _expected({"read:memories", "write:memories"})


@pytest.mark.parametrize(
    ("ceiling", "permitted"),
    [("read", {"read:memories"}), ("write", {"write:memories"}), ("read:memories", {"read:memories"}), ("write:memories", {"write:memories"})],
)
async def test_the_instance_ceiling_decides_which_routes_answer(registry, ceiling, permitted):
    answers = await _answers(_server(registry, ceiling=ceiling, auth=FixedTokens()), _bearer("everything"))

    assert answers == _expected(permitted)


@pytest.mark.parametrize(
    ("token", "permitted"),
    [
        ("reader", {"read:memories"}),
        ("memory-writer", {"write:memories"}),
        ("foreign-scopes", set()),
    ],
)
async def test_the_scopes_of_the_token_narrow_the_instance_ceiling(registry, token, permitted):
    answers = await _answers(_server(registry, auth=FixedTokens()), _bearer(token))

    assert answers == _expected(permitted)


async def test_a_token_cannot_widen_the_instance_ceiling(registry):
    answers = await _answers(_server(registry, ceiling="read", auth=FixedTokens()), _bearer("memory-writer"))

    assert answers == _expected(set())


async def test_the_tool_is_checked_before_the_body(registry):
    async with _client(_server(registry, ceiling="read", auth=FixedTokens())) as http:
        response = await http.post(
            "/api/v1/memories", content=b"plain", headers={**_bearer("everything"), "Content-Type": "text/plain"},
        )

    assert (response.status_code, response.json()) == (403, _refusal("create_memory", "write:memories"))


# ============================================
# The body: 415, and the handler
# ============================================


async def test_a_body_that_is_not_declared_as_json_is_a_415_for_a_permitted_route(registry):
    async with _client(_server(registry, auth=FixedTokens())) as http:
        refused = await http.post(
            "/api/v1/memories", content=b"plain", headers={**_bearer("everything"), "Content-Type": "text/plain"},
        )
        accepted = await http.post(
            "/api/v1/memories", content=b"{}", headers={**_bearer("everything"), "Content-Type": "application/json"},
        )

    assert (refused.status_code, refused.json()) == (415, {"error": "Content-Type must be application/json"})
    assert (accepted.status_code, accepted.json()) == _served("create_memory")


async def test_without_an_auth_provider_the_local_tenant_is_served_under_the_instance_ceiling(registry, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "local-person")

    async with _client(_server(registry, ceiling="read")) as http:
        read = await http.get("/api/v1/memories")
        write = await http.post("/api/v1/memories")

    assert (read.status_code, read.json()) == (200, {"tool": "get_recent_memories", "tenant": "local-person"})
    assert (write.status_code, write.json()) == (403, _refusal("create_memory", "write:memories"))


async def test_a_server_without_an_auth_provider_refuses_every_mode_but_none(registry, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "static")

    async with _client(_server(registry)) as http:
        response = await http.get("/api/v1/memories")

    assert (response.status_code, response.json()) == (401, {"error": "Authentication is not configured: set AUTH_MODE"})


async def test_a_route_answers_only_to_its_own_method(registry):
    async with _client(_server(registry, auth=FixedTokens())) as http:
        wrong = await http.put("/api/v1/memories", headers=_bearer("everything"))
        right = await http.post("/api/v1/memories/search", headers=_bearer("everything"))

    assert wrong.status_code == 405
    assert (right.status_code, right.json()) == _served("query_memory")


@pytest.mark.parametrize(
    ("error", "status", "message"),
    [
        (RequestError("too much", status_code=422), 422, "too much"),
        (RequestError("malformed"), 400, "malformed"),
        (ConflictError("the memory kept changing"), 409, "the memory kept changing"),
    ],
)
async def test_an_error_of_the_handler_is_answered_as_json(registry, error, status, message):
    mcp = _base(registry, auth=FixedTokens())

    @rest_route(mcp, "GET", "/api/v1/memories")
    async def failing(request, tenant):
        raise error

    async with _client(mcp) as http:
        response = await http.get("/api/v1/memories", headers=_bearer("everything"))

    assert (response.status_code, response.json()) == (status, {"error": message})


def test_the_decorator_returns_the_handler_it_was_given(registry):
    handler = _answering("get_recent_memories")

    assert rest_route(_base(registry), "GET", "/api/v1/memories")(handler) is handler


async def test_authorize_request_answers_with_the_tenant_of_a_permitted_request(registry):
    mcp = _base(registry, auth=FixedTokens())

    class Request:
        headers = {"Authorization": "Bearer reader"}

    assert (await authorize_request(Request(), mcp, "get_memory")).name == "acme"
    with pytest.raises(RequestError) as refused:
        await authorize_request(Request(), mcp, "create_memory")
    assert (refused.value.status_code, refused.value.message) == (403, _refusal("create_memory", "write:memories")["error"])
