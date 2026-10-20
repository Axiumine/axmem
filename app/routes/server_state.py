"""The typed FastMCP server that carries the Forgetful runtime.

``main.lifespan`` builds the runtime (``app.bootstrap.build_runtime``) and attaches its
services, tool registry and scope ceiling to the server instance; HTTP routes and MCP
tools read them back from it. ``ForgetfulMCP`` declares every attribute that is attached,
so ``mypy`` can check both sides instead of seeing a plain ``FastMCP`` with no such
attributes.

The attributes are declared, not defaulted: they exist only once the lifespan has run,
exactly as before.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastmcp import FastMCP

if TYPE_CHECKING:
    from fastmcp import Context

    from app.middleware.auth import TokenCache
    from app.routes.mcp.tool_registry import ToolRegistry
    from app.services.memory_service import MemoryService
    from app.services.re_embedding_service import ReEmbeddingService
    from app.services.tenant_service import TenantService


class ForgetfulMCP(FastMCP[Any]):
    """FastMCP server with the services and registry that ``main.lifespan`` attaches."""

    tenant_service: TenantService
    memory_service: MemoryService
    re_embedding_service: ReEmbeddingService

    token_cache: TokenCache | None
    registry: ToolRegistry
    _instance_permitted_tools: set[str]
    _instance_scopes: frozenset[str]


def server_of(ctx: Context) -> ForgetfulMCP:
    """The ``ForgetfulMCP`` that a request context belongs to.

    ``ctx.fastmcp`` is typed as a plain ``FastMCP``. It is the ``ForgetfulMCP`` that
    ``main.py`` creates, and the in-process CLI hands adapters a duck-typed shim that
    carries the attributes they read. This is a typing-only narrowing, with no runtime check.
    """
    return cast("ForgetfulMCP", ctx.fastmcp)
