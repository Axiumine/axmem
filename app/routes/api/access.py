"""Which tool each REST route stands for, and the check that a request may use it.

The three MCP meta-tools refuse a tool that the instance ceiling (``AXMEM_SCOPES``) or the scopes of the
caller's token do not permit. The REST routes reach the same services, so they answer to the same rules:
every route that touches memories is registered through ``rest_route``, which looks up the tool the route
corresponds to in ``ROUTE_TOOLS``, resolves the permitted tools exactly as the meta-tools do
(``effective_permitted_tools``) and answers 403 when the tool is not among them. A route without an entry
cannot be registered this way (``KeyError`` at start-up), and ``tests/e2e/test_rest_scopes_e2e.py`` fails for
any route of the application that is neither mapped nor listed as public.

Public routes (the service info, ``/health`` and ``/api/v1/auth/info``) read no memory and need no credential.
"""
import functools
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from app.middleware.auth import get_identity_from_request
from app.models.tenant_models import Tenant
from app.routes.api.request_utils import (
    RequestError,
    handle_request_errors,
    require_json_content_type,
)
from app.routes.mcp.scope_resolver import effective_permitted_tools, get_required_scope
from app.routes.server_state import AxmemMCP

type TenantHandler = Callable[[Request, Tenant], Awaitable[Response]]

# (HTTP method, path template) -> name of the registry tool the route corresponds to. The tool decides
# whether the route is a read or a write: the same scope that permits the tool over MCP permits the route.
ROUTE_TOOLS: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/memories"): "get_recent_memories",
    ("GET", "/api/v1/memories/{memory_id}"): "get_memory",
    ("POST", "/api/v1/memories"): "create_memory",
    ("PUT", "/api/v1/memories/{memory_id}"): "update_memory",
    ("DELETE", "/api/v1/memories/{memory_id}"): "mark_memory_obsolete",
    ("POST", "/api/v1/memories/search"): "query_memory",
    ("POST", "/api/v1/memories/{memory_id}/links"): "link_memories",
    ("GET", "/api/v1/memories/{memory_id}/links"): "get_memory",
    ("DELETE", "/api/v1/memories/{memory_id}/links/{target_id}"): "unlink_memories",
}


async def authorize_request(request: Request, mcp: AxmemMCP, tool_name: str) -> Tenant:
    """The tenant of a request, once its credential is valid and its scopes permit ``tool_name``.

    Raises:
        RequestError: 401 when the credential is missing or invalid, 403 when the instance ceiling or the
            scopes of the token do not permit the tool (the answer an MCP call to that tool would get)
    """
    try:
        identity = await get_identity_from_request(request, mcp)
    except ValueError as exc:
        raise RequestError(str(exc), status_code=401) from None

    permitted, _ = effective_permitted_tools(mcp, identity.token_scopes)
    if tool_name not in permitted:
        raise RequestError(
            f"Tool '{tool_name}' is not permitted under current scopes. "
            f"Required scope: '{get_required_scope(tool_name, mcp.registry)}'",
            status_code=403,
        )
    return identity.tenant


def rest_route(mcp: AxmemMCP, method: str, path: str) -> Callable[[TenantHandler], TenantHandler]:
    """Register ``handler(request, tenant)`` for ``method path``, behind the credential, scope and body checks.

    In this order: the credential (401), the tool the route stands for (403), the content type of a request
    that carries a body (415). The handler runs only for an authorised tenant, and a ``RequestError`` it
    raises is answered as JSON.

    Raises:
        KeyError: When ``ROUTE_TOOLS`` has no entry for the route, so a route cannot skip the scope check
    """
    tool_name = ROUTE_TOOLS[(method, path)]

    def register(handler: TenantHandler) -> TenantHandler:
        @functools.wraps(handler)
        async def route(request: Request) -> Response:
            tenant = await authorize_request(request, mcp, tool_name)
            require_json_content_type(request)
            return await handler(request, tenant)

        mcp.custom_route(path, methods=[method])(handle_request_errors(route))
        return handler

    return register
