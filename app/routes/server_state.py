"""The typed FastMCP server that carries the Forgetful runtime.

``main.lifespan`` builds the runtime (``app.bootstrap.build_runtime``) and attaches its
services, tool registry and scope ceiling to the server instance; HTTP routes and MCP
tools read them back from it. ``ForgetfulMCP`` declares every attribute that is attached,
so ``mypy`` can check both sides instead of seeing a plain ``FastMCP`` with no such
attributes.

The attributes are declared, not defaulted: they exist only once the lifespan has run,
exactly as before. The four feature-flagged services (files, skills, plans, tasks) are
declared as present because the routes that read them are registered only while the same
feature flag is on, which is also when the lifespan attaches them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastmcp import FastMCP

if TYPE_CHECKING:
    from fastmcp import Context

    from app.events import EventBus
    from app.middleware.auth import TokenCache
    from app.routes.mcp.tool_registry import ToolRegistry
    from app.services.activity_service import ActivityService
    from app.services.code_artifact_service import CodeArtifactService
    from app.services.document_service import DocumentService
    from app.services.entity_service import EntityService
    from app.services.file_service import FileService
    from app.services.memory_service import MemoryService
    from app.services.plan_service import PlanService
    from app.services.project_service import ProjectService
    from app.services.re_embedding_service import ReEmbeddingService
    from app.services.skill_service import SkillService
    from app.services.task_service import TaskService
    from app.services.user_service import UserService


class ForgetfulMCP(FastMCP[Any]):
    """FastMCP server with the services and registry that ``main.lifespan`` attaches."""

    user_service: UserService
    memory_service: MemoryService
    re_embedding_service: ReEmbeddingService
    project_service: ProjectService
    code_artifact_service: CodeArtifactService
    document_service: DocumentService
    entity_service: EntityService
    activity_service: ActivityService
    event_bus: EventBus | None

    # Feature-flagged: attached, and their routes registered, only while the flag is on
    file_service: FileService
    skill_service: SkillService
    plan_service: PlanService
    task_service: TaskService

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
