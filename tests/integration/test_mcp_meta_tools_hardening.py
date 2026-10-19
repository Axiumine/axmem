"""Hardening of MCP tool error text and category discovery.

Covers, with stubbed services and no database:
- ``client_error_detail`` keeps authored messages and hides driver details.
- ``execute_forgetful_tool`` never puts SQL, bound parameters or driver text from a project
  operation in a ToolError.
- ``discover_forgetful_tools`` honours the advertised ``linking`` category.
- ``execute_forgetful_tool`` rejects argument names that cannot be keyword arguments.
- an unknown tool or category name is quoted with ``repr`` inside the error text, so ESC, CR and
  LF cannot reach the client or the server log (FastMCP logs every ToolError with a traceback).
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from sqlalchemy.exc import IntegrityError

from app.exceptions import (
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
)
from app.models.tool_registry_models import ToolCategory, ToolParameter
from app.routes.mcp import meta_tools
from app.routes.mcp.meta_tools import (
    build_discovery_payload,
    build_tool_documentation,
    ensure_tool_executable,
    reject_invalid_argument_names,
)
from app.routes.mcp.tool_adapters import create_project_adapters
from app.routes.mcp.tool_errors import (
    CONSTRAINT_FAILURE,
    GENERIC_FAILURE,
    client_error_detail,
)
from app.routes.mcp.tool_metadata_registry import register_project_tools_metadata
from app.routes.mcp.tool_registry import ToolRegistry

SECRET_USER_ID = "7f1c2b9e-0000-4000-8000-00000000abcd"
SQL_TEXT = "INSERT INTO projects (user_id, name) VALUES (?, ?)"
LEAK_MARKERS = (
    "INSERT INTO",
    "[SQL:",
    "[parameters:",
    "sqlalche.me",
    "UNIQUE constraint failed",
    "sqlite3.",
    SECRET_USER_ID,
)


def _integrity_error() -> IntegrityError:
    return IntegrityError(
        SQL_TEXT,
        (SECRET_USER_ID, 1),
        Exception("UNIQUE constraint failed: projects.user_id, projects.name"),
    )


class TestClientErrorDetail:
    """Authored messages pass through; driver and unexpected errors are reduced."""

    @pytest.mark.parametrize(
        "error",
        [
            NotFoundError("Memory 7 not found"),
            ConflictError("A project with this name already exists."),
            InvalidStateTransitionError("memory is already obsolete"),
            ValueError("project_id must be an integer"),
            TypeError("create_project() got an unexpected keyword argument 'name'"),
            ToolError("Project 4 not found"),
        ],
    )
    def test_authored_messages_are_kept(self, error):
        assert client_error_detail(error) == str(error)

    def test_integrity_error_becomes_constraint_message(self):
        error = _integrity_error()

        detail = client_error_detail(error)

        assert detail == CONSTRAINT_FAILURE
        assert not [marker for marker in LEAK_MARKERS if marker in detail]
        assert "INSERT INTO" in str(error), "precondition: the raw text is what leaks"

    @pytest.mark.parametrize(
        "error",
        [
            RuntimeError(f"connection to {SECRET_USER_ID} dropped"),
            OverflowError("Python int too large to convert to SQLite INTEGER"),
            KeyError("password"),
        ],
    )
    def test_unexpected_errors_are_generic(self, error):
        assert client_error_detail(error) == GENERIC_FAILURE


class _StubUserService:
    async def get_or_create_user(self, user):
        return SimpleNamespace(id=uuid4())


class _FailingProjectService:
    """Every service method raises the configured error."""

    def __init__(self, error: Exception):
        self._error = error

    def __getattr__(self, name):
        async def _raise(**_kwargs):
            raise self._error

        return _raise


def _project_tools_server(error: Exception) -> FastMCP:
    """The mounted surface (the three meta-tools) over project adapters whose service fails."""
    user_service = _StubUserService()
    registry = ToolRegistry()
    register_project_tools_metadata(
        registry, create_project_adapters(_FailingProjectService(error), user_service),
    )
    server = FastMCP("project-tools-error-test")
    server.user_service = user_service
    server.registry = registry
    meta_tools.register(server)
    return server


async def _execute(client: Client, tool_name: str, arguments: dict):
    return await client.call_tool(
        "execute_forgetful_tool", {"tool_name": tool_name, "arguments": arguments},
    )


PROJECT_TOOL_CALLS = [
    ("create_project", {"name": "Atlas", "description": "d", "project_type": "development"}),
    ("get_project", {"project_id": 1}),
    ("list_projects", {}),
    ("update_project", {"project_id": 1, "name": "S"}),
    ("delete_project", {"project_id": 1}),
]


class TestProjectToolErrors:
    """Project operations return a short message for database errors and keep authored ones."""

    @pytest.mark.parametrize(
        ("tool_name", "arguments"),
        PROJECT_TOOL_CALLS,
        ids=[call[0] for call in PROJECT_TOOL_CALLS],
    )
    async def test_database_error_text_is_not_returned(self, tool_name, arguments):
        async with Client(_project_tools_server(_integrity_error())) as client:
            with pytest.raises(ToolError) as caught:
                await _execute(client, tool_name, arguments)

        message = str(caught.value)
        assert f"Failed to execute {tool_name!r}" in message
        assert CONSTRAINT_FAILURE in message
        assert not [marker for marker in LEAK_MARKERS if marker in message], message

    async def test_unexpected_error_text_is_not_returned(self):
        server = _project_tools_server(RuntimeError(f"pool exhausted for {SECRET_USER_ID}"))

        async with Client(server) as client:
            with pytest.raises(ToolError) as caught:
                await _execute(client, "get_project", {"project_id": 1})

        assert SECRET_USER_ID not in str(caught.value)
        assert GENERIC_FAILURE in str(caught.value)

    async def test_domain_message_is_kept(self):
        server = _project_tools_server(NotFoundError("Project 1 not found"))

        async with Client(server) as client:
            with pytest.raises(ToolError, match="Project 1 not found"):
                await _execute(client, "get_project", {"project_id": 1})

    async def test_conflict_message_is_kept(self):
        server = _project_tools_server(ConflictError("A project with this name already exists."))

        async with Client(server) as client:
            with pytest.raises(ToolError, match="A project with this name already exists."):
                await _execute(
                    client,
                    "create_project",
                    {"name": "Atlas", "description": "d", "project_type": "development"},
                )


async def _noop(**_kwargs):
    return {}


def _registry_with_link_tools() -> ToolRegistry:
    registry = ToolRegistry()
    param = [ToolParameter(name="x", type="int", description="x", required=True)]
    for name, category, tags in [
        ("link_memories", ToolCategory.MEMORY, ["memory", "linking"]),
        # a stand-in from another category, to show the filter follows the tag, not the category
        ("tag_project_links", ToolCategory.PROJECT, ["project", "linking"]),
        ("get_memory", ToolCategory.MEMORY, ["memory", "query"]),
        ("create_project", ToolCategory.PROJECT, ["project", "create"]),
    ]:
        registry.register(
            name=name,
            category=category,
            description=name,
            parameters=param,
            returns="dict",
            implementation=_noop,
            tags=tags,
        )
    return registry


class TestLinkingCategory:
    """The advertised ``linking`` category lists the tools tagged ``linking``."""

    def test_linking_category_returns_tagged_tools_across_categories(self):
        registry = _registry_with_link_tools()
        permitted = {meta.name for meta in registry.list_all_tools()}

        payload = build_discovery_payload(registry, permitted, "linking")

        names = {tool["name"] for tools in payload["tools_by_category"].values() for tool in tools}
        assert names == {"link_memories", "tag_project_links"}
        assert payload["total_count"] == 2
        assert payload["filtered_by"] == "linking"

    def test_linking_category_respects_permitted_tools(self):
        registry = _registry_with_link_tools()

        payload = build_discovery_payload(registry, {"link_memories", "get_memory"}, "linking")

        names = {tool["name"] for tools in payload["tools_by_category"].values() for tool in tools}
        assert names == {"link_memories"}

    def test_linking_is_case_insensitive_like_other_categories(self):
        registry = _registry_with_link_tools()
        permitted = {meta.name for meta in registry.list_all_tools()}

        payload = build_discovery_payload(registry, permitted, "LINKING")

        assert payload["total_count"] == 2

    def test_real_categories_are_unchanged(self):
        registry = _registry_with_link_tools()
        permitted = {meta.name for meta in registry.list_all_tools()}

        payload = build_discovery_payload(registry, permitted, "memory")

        names = {tool["name"] for tools in payload["tools_by_category"].values() for tool in tools}
        assert names == {"link_memories", "get_memory"}
        assert "linking" not in payload["categories_available"]


class TestRejectInvalidArgumentNames:
    """Argument names become keyword arguments; anything else is refused unseen."""

    @pytest.mark.parametrize(
        "arguments",
        [{}, {"query": 1}, {"memory_id": 1, "_private": 2, "ctx": 3}, {"naïve": 1}],
        ids=["empty", "plain", "underscore_and_ctx", "unicode_identifier"],
    )
    def test_valid_names_pass(self, arguments):
        reject_invalid_argument_names("get_memory", arguments)

    @pytest.mark.parametrize(
        "name",
        ["a\nb", "a\rb", "a\x1bb", "a b", "", "1a", "a-b", "a.b", 7, None],
        ids=["lf", "cr", "esc", "space", "empty", "digit_first", "dash", "dot", "int", "none"],
    )
    def test_invalid_names_are_refused_without_echo(self, name):
        arguments = {"ok": 1, name: 2}

        with pytest.raises(ToolError) as caught:
            reject_invalid_argument_names("get_memory", arguments)

        message = str(caught.value)
        assert "Invalid argument name" in message
        assert repr(name) not in message
        if isinstance(name, str) and name:
            assert name not in message


HOSTILE_NAME = "x\x1b[2J\n2026-10-18 12:00:00 - app.middleware.auth - INFO - forged\rsecond"


def _assert_escaped(message: str) -> None:
    assert "\x1b" not in message
    assert "\n" not in message
    assert "\r" not in message
    assert repr(HOSTILE_NAME) in message


class TestUnknownNamesAreQuoted:
    """Caller-supplied names are written into error text with ``repr``."""

    def test_unknown_category_message_is_escaped(self):
        registry = _registry_with_link_tools()

        with pytest.raises(ToolError) as caught:
            build_discovery_payload(registry, set(), HOSTILE_NAME)

        _assert_escaped(str(caught.value))
        assert "Available categories:" in str(caught.value)

    def test_unknown_tool_documentation_message_is_escaped(self):
        registry = _registry_with_link_tools()

        with pytest.raises(ToolError) as caught:
            build_tool_documentation(registry, {"get_memory"}, HOSTILE_NAME)

        _assert_escaped(str(caught.value))
        assert "not found in registry" in str(caught.value)
        assert "get_memory" in str(caught.value)

    def test_unknown_tool_execution_message_is_escaped(self):
        registry = _registry_with_link_tools()

        with pytest.raises(ToolError) as caught:
            ensure_tool_executable(registry, {"get_memory"}, HOSTILE_NAME)

        _assert_escaped(str(caught.value))
        assert "not found in registry" in str(caught.value)

    async def test_registry_execute_error_is_escaped(self):
        """The CLI local executor reaches ToolRegistry.execute with an operator-typed name."""
        registry = _registry_with_link_tools()

        with pytest.raises(ValueError) as caught:
            await registry.execute(name=HOSTILE_NAME, arguments={})

        _assert_escaped(str(caught.value))
        assert "not found in registry" in str(caught.value)

    def test_ordinary_names_keep_their_message(self):
        registry = _registry_with_link_tools()

        with pytest.raises(ToolError, match="Invalid category 'nothing'. Available categories: "):
            build_discovery_payload(registry, set(), "nothing")
        with pytest.raises(ToolError, match="Tool 'nothing' not found in registry. "):
            build_tool_documentation(registry, set(), "nothing")
        with pytest.raises(ToolError, match="Tool 'nothing' not found in registry. "):
            ensure_tool_executable(registry, set(), "nothing")
