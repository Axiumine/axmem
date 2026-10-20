"""MCP - Meta-Tools
This module implements the meta-tools pattern as an alternative to bloating the context window of an LLM with all the tools available
within the MCP service. Instead of loading all tool definitions upfront, we only expose 3 meta-tools.

The three meta-tools:
1. discover_axmem_tools: List available tools by category, with enough info to allow most LLMs one-shot usage
2. how_to_use_axmem_tool: Get detailed documentation for a specific tool
3. execute_axmem_tool: Dynamically invoke any tool with arguments

Docstrings for discover and execute are built at registration time from the descriptor mode
(MCP_DESCRIPTOR_MODE).
"""

from typing import Any

from fastmcp import Context
from fastmcp.exceptions import ToolError

from app.config.logging_config import logging
from app.config.settings import settings
from app.models.tool_registry_models import ToolCategory
from app.routes.mcp.scope_resolver import get_effective_scopes, get_required_scope
from app.routes.mcp.tool_errors import client_error_detail
from app.routes.server_state import AxmemMCP, server_of

logger = logging.getLogger(__name__)


# ============================================================================
# Dynamic Docstring Sections — discover_axmem_tools
# ============================================================================

_DISCOVER_CORE_SECTIONS = """\

    **Memory Tools** - Atomic knowledge storage (<400 words per memory)
    - create_memory: Store a single concept with auto-linking to similar memories (supports provenance tracking)
    - query_memory: Semantic search across memories (use query_context for better ranking)
    - get_memory: Retrieve memory by ID
    - update_memory: Modify memory fields (PATCH semantics, supports provenance tracking)
    - link_memories: Manually connect related memories bidirectionally
    - unlink_memories: Remove link between memories
    - mark_memory_obsolete: Soft-delete with audit trail and optional superseded_by
    - unobsolete_memory: Restore a memory previously marked obsolete
    - get_recent_memories: Get newest memories (timeline view)

    **Project scope** (optional `project` argument of create_memory, update_memory, query_memory, get_recent_memories):
    - A free-form label that groups memories; it is created the first time it is used
    - query_memory and get_recent_memories only return memories in the scope they are given

    **Provenance Tracking** (optional fields for create_memory/update_memory):
    - source_repo, source_files, source_url: Track where knowledge came from
    - confidence: Reliability score (0.0-1.0) for AI-generated content
    - encoding_agent, encoding_version: Track which agent/version created the memory"""


# ============================================================================
# Dynamic Docstring Sections — execute_axmem_tool
# ============================================================================

_EXECUTE_CORE_EXAMPLES = """\

    ## Quick Start - One-Shot Examples (all required params shown)

    **Memory Operations:**
    - Search: execute_axmem_tool("query_memory", {"query": "search terms", "query_context": "why searching"})
    - Create: execute_axmem_tool("create_memory", {"title": "Short title", "content": "Memory content (<2000 chars)", "context": "Why this matters", "keywords": ["kw1", "kw2"], "tags": ["tag1"], "importance": 7, "project": "my-project"})
    - Create with provenance: execute_axmem_tool("create_memory", {"title": "...", "content": "...", "context": "...", "keywords": [...], "tags": [...], "importance": 7, "source_repo": "owner/repo", "source_files": ["src/main.py"], "confidence": 0.85, "encoding_agent": "claude-sonnet-4"})
    - Update: execute_axmem_tool("update_memory", {"memory_id": "018f2c3e-0000-7000-8000-000000000001", "content": "new content"})
    - Get: execute_axmem_tool("get_memory", {"memory_id": "018f2c3e-0000-7000-8000-000000000001"})
    - Link: execute_axmem_tool("link_memories", {"memory_id": "018f2c3e-0000-7000-8000-000000000001", "related_ids": ["018f2c3e-0000-7000-8000-000000000002", "018f2c3e-0000-7000-8000-000000000003"]})
    - Unlink: execute_axmem_tool("unlink_memories", {"source_id": "018f2c3e-0000-7000-8000-000000000001", "target_id": "018f2c3e-0000-7000-8000-000000000002"})
    - Obsolete: execute_axmem_tool("mark_memory_obsolete", {"memory_id": "018f2c3e-0000-7000-8000-000000000042", "reason": "Outdated approach", "superseded_by": "018f2c3e-0000-7000-8000-000000000100"})

    **Project scope:**
    - Query within a scope: execute_axmem_tool("query_memory", {"query": "search terms", "query_context": "why searching", "project": "my-project"})"""


# ============================================================================
# Docstring Builders
# ============================================================================


def _build_category_list() -> str:
    """Build the valid category filter list."""
    categories = ["memory", "linking"]
    return ", ".join(categories)


def _build_tool_categories_line() -> str:
    """Build the tool categories pipe-separated line for execute_axmem_tool."""
    categories = ["memory", "linking"]
    return " | ".join(categories)


def _get_mcp_descriptor_mode() -> str:
    """Return the normalized descriptor mode for meta-tool docstrings."""
    mode = str(getattr(settings, "MCP_DESCRIPTOR_MODE", "verbose")).strip().lower()
    if mode == "compact":
        return "compact"
    return "verbose"


def _build_compact_discover_docstring(categories: str) -> str:
    """Build a compact discover_axmem_tools docstring."""
    return f"""\
Discover available Axmem tools, optionally filtered by category.

    Use this tool to list the available inner tools. Call how_to_use_axmem_tool(tool_name)
    for full parameter documentation and examples before uncommon or write operations.

    Args:
        category: Optional category filter ({categories})
        ctx: FastMCP Context (automatically injected)

    Returns:
        Dictionary with tools_by_category, total_count, categories_available, and filtered_by.
    """


def _build_compact_execute_docstring(tool_categories: str) -> str:
    """Build a compact execute_axmem_tool docstring."""
    return f"""\
Execute any registered Axmem inner tool by name.

    Typical flow:
    1. Call discover_axmem_tools(category?) to find tool names.
    2. Call how_to_use_axmem_tool(tool_name) for full schemas and examples.
    3. Call execute_axmem_tool(tool_name, arguments) to run the selected tool.

    Tool categories: {tool_categories}

    Args:
        tool_name: Name of the inner tool to execute
        arguments: Dictionary of arguments for the selected tool
        ctx: FastMCP Context (automatically injected)

    Returns:
        Tool execution result (format depends on the selected tool)
    """


def _build_discover_docstring() -> str:
    """Build the discover_axmem_tools docstring."""
    categories = _build_category_list()
    if _get_mcp_descriptor_mode() == "compact":
        return _build_compact_discover_docstring(categories)

    parts = [
        f"""\
Discover available tools, optionally filtered by category

    Returns enough information for LLMs to call tools directly without needing how_to_use.

    Args:
        category: Optional category filter ({categories})
        ctx: FastMCP Context (automatically injected)

    Returns:
        Dictionary with:
        - tools_by_category: Tools grouped by category
        - total_count: Total number of tools
        - categories_available: List of available categories
        - filtered_by: Category filter applied (if any)

    ## All Available Tools
{_DISCOVER_CORE_SECTIONS}""",
    ]

    parts.append("""

    ## Workflow
    1. Call discover_axmem_tools() to see this catalog
    2. Call execute_axmem_tool(tool_name, {args}) to run any tool
    3. If needed, call how_to_use_axmem_tool(tool_name) for full parameter docs
    """)

    return "".join(parts)


def _build_execute_docstring() -> str:
    """Build the execute_axmem_tool docstring."""
    tool_categories = _build_tool_categories_line()
    if _get_mcp_descriptor_mode() == "compact":
        return _build_compact_execute_docstring(tool_categories)

    parts = [
        f"""\
Execute any registered tool dynamically. Axmem is a semantic memory system for LLMs.
{_EXECUTE_CORE_EXAMPLES}""",
    ]

    parts.append(f"""

    ## Linking Best Practices
    **Always link related items for discoverability:**
    - Link memories to each other: `link_memories(memory_id="018f2c3e-0000-7000-8000-000000000001", related_ids=["018f2c3e-0000-7000-8000-000000000002", "018f2c3e-0000-7000-8000-000000000003"])`

    ## Tool Categories
    {tool_categories}

    Use discover_axmem_tools(category?) for full parameter details and optional params.

    ---

    Args:
        tool_name: Name of the tool to execute
        arguments: Dictionary of arguments to pass to the tool
        ctx: FastMCP Context (automatically injected)

    Returns:
        Tool execution result (format depends on the specific tool)
    """)

    return "".join(parts)


# ============================================================================
# Shared payload builders and access checks
# Used by both the meta-tools below and the CLI executors (app/routes/cli/),
# so the MCP surface and the terminal can never drift apart.
# ============================================================================


def build_discovery_payload(registry, permitted: set, category: str | None = None) -> dict[str, Any]:
    """Build the discover_axmem_tools payload for a permitted tool set."""
    if category:
        try:
            cat_enum = ToolCategory(category.lower())
        except ValueError:
            valid_categories = [c.value for c in ToolCategory]
            raise ToolError(
                f"Invalid category {category!r}. "
                f"Available categories: {', '.join(valid_categories)}",
            )
        tools_metadata = registry.get_permitted_by_category(cat_enum, permitted)
        filtered_by = category
    else:
        tools_metadata = registry.get_permitted_tools(permitted)
        filtered_by = None

    # Convert to discovery format (minimal metadata)
    tools = [meta.to_discovery_dict() for meta in tools_metadata]

    # Group by category
    tools_by_category: dict[str, list[dict[str, Any]]] = {}
    for tool in tools:
        cat = tool["category"]
        if cat not in tools_by_category:
            tools_by_category[cat] = []
        tools_by_category[cat].append(tool)

    return {
        "tools_by_category": tools_by_category,
        "total_count": len(tools),
        "categories_available": list(registry.get_permitted_categories(permitted).keys()),
        "filtered_by": filtered_by,
    }


def build_tool_documentation(registry, permitted: set, tool_name: str) -> dict[str, Any]:
    """Build the how_to_use_axmem_tool payload, enforcing existence and scope."""
    tool = registry.get_tool(tool_name)
    if not tool:
        available_tools = [m.name for m in registry.get_permitted_tools(permitted)[:10]]
        raise ToolError(
            f"Tool {tool_name!r} not found in registry. "
            f"Available tools (first 10): {', '.join(available_tools)}",
        )

    if not registry.is_permitted(tool_name, permitted):
        required_scope = get_required_scope(tool_name, registry)
        raise ToolError(
            f"Tool '{tool_name}' is not permitted under current scopes. "
            f"Required scope: '{required_scope}'",
        )

    return tool.metadata.to_detailed_dict()


# The longest tool or category name a caller may send. The registry's names are far shorter; the cap keeps a
# hostile name out of the logs and out of the error text that echoes it back.
NAME_MAX_LENGTH = 100


def ensure_name_length(value: str | None, what: str) -> None:
    """Raise ToolError for a tool or category name longer than ``NAME_MAX_LENGTH``, before anything logs it."""
    if value is not None and len(value) > NAME_MAX_LENGTH:
        raise ToolError(f"{what} must be at most {NAME_MAX_LENGTH} characters")


def reject_invalid_argument_names(tool_name: str, arguments: dict[str, Any]) -> None:
    """Raise ToolError if an argument name could not be a Python keyword argument.

    Arguments are splatted into the tool adapters as keyword arguments. CPython copies an
    unknown keyword verbatim into the TypeError text, so a name carrying CR, LF or ESC would
    reach the logs (message and traceback) and the client unescaped. No tool parameter is
    named like that, so such a call can only be malformed or hostile: it is rejected before
    dispatch, and the offending name is never echoed or logged raw.
    """
    if all(isinstance(name, str) and name.isidentifier() for name in arguments):
        return
    logger.warning("execute_axmem_tool: %r rejected, invalid argument name", tool_name)
    raise ToolError(
        "Invalid argument name: argument names must be identifiers "
        "(letters, digits and underscores, not starting with a digit)",
    )


def ensure_tool_executable(registry, permitted: set, tool_name: str) -> None:
    """Raise ToolError unless tool_name exists and is permitted (execute-path checks)."""
    if not registry.tool_exists(tool_name):
        available_tools = [m.name for m in registry.get_permitted_tools(permitted)[:10]]
        raise ToolError(
            f"Tool {tool_name!r} not found in registry. "
            f"Available tools (first 10): {', '.join(available_tools)}",
        )

    if tool_name not in permitted:
        required_scope = get_required_scope(tool_name, registry)
        raise ToolError(
            f"Tool '{tool_name}' is not permitted under current scopes. "
            f"Required scope: '{required_scope}'",
        )


# ============================================================================
# Tool Registration
# ============================================================================


def register(mcp: AxmemMCP):
    """Register meta-tools with the provided FastMCP instance - registry accessed via ctx pattern"""

    # Build the docstrings for the configured descriptor mode
    discover_doc = _build_discover_docstring()
    execute_doc = _build_execute_docstring()

    # -- discover_axmem_tools --
    # Defined without @mcp.tool() so we can set docstring before registration

    async def discover_axmem_tools(
        category: str | None = None,
        *,
        ctx: Context,
    ) -> dict[str, Any]:
        """Placeholder - set dynamically below"""
        try:
            ensure_name_length(category, "category")
            registry = server_of(ctx).registry
            permitted, _ = get_effective_scopes(ctx)
            logger.info("discover_axmem_tools: category=%r", category)

            result = build_discovery_payload(registry, permitted, category)

            logger.info("discover_axmem_tools: returned %d tools", result["total_count"])
            return result

        except ToolError:
            raise
        except Exception as e:
            logger.error("discover_axmem_tools failed: %r", e, exc_info=True)
            raise ToolError(f"Failed to discover tools: {client_error_detail(e)}")

    discover_axmem_tools.__doc__ = discover_doc
    mcp.tool()(discover_axmem_tools)

    # -- how_to_use_axmem_tool --
    # Static docstring, no dynamic content needed

    @mcp.tool()
    async def how_to_use_axmem_tool(
        tool_name: str,
        ctx: Context,
    ) -> dict[str, Any]:
        """Get detailed documentation for a specific tool

        Returns complete documentation including JSON schema, multiple examples, and full parameter details.

        Args:
            tool_name: Name of the tool to get documentation for
            ctx: FastMCP Context (automatically injected)

        Returns:
            Detailed tool documentation with JSON schema
        """
        try:
            ensure_name_length(tool_name, "tool_name")
            registry = server_of(ctx).registry
            permitted, _ = get_effective_scopes(ctx)
            logger.info("how_to_use_axmem_tool: tool_name=%r", tool_name)

            detailed_info = build_tool_documentation(registry, permitted, tool_name)
            logger.info("how_to_use_axmem_tool: returned docs for %r", tool_name)
            return detailed_info

        except ToolError:
            raise
        except Exception as e:
            logger.error("how_to_use_axmem_tool failed for %r: %r", tool_name, e, exc_info=True)
            raise ToolError(
                f"Failed to get documentation for {tool_name!r}: {client_error_detail(e)}",
            )

    # -- execute_axmem_tool --
    # Defined without @mcp.tool() so we can set docstring before registration

    async def execute_axmem_tool(
        tool_name: str,
        arguments: dict[str, Any],
        ctx: Context,
    ) -> Any:
        """Placeholder - set dynamically below"""
        try:
            ensure_name_length(tool_name, "tool_name")
            reject_invalid_argument_names(tool_name, arguments)

            registry = server_of(ctx).registry
            permitted, _ = get_effective_scopes(ctx)
            logger.info("execute_axmem_tool: %r, args=%r", tool_name, list(arguments.keys()))

            ensure_tool_executable(registry, permitted, tool_name)

            # Inject context into arguments for adapters to extract the tenant
            arguments["ctx"] = ctx

            # Execute through registry
            result = await registry.execute(
                name=tool_name,
                arguments=arguments,
            )

            logger.info("execute_axmem_tool: %r executed successfully", tool_name)
            return result

        except ToolError:
            raise
        except Exception as e:
            logger.error("execute_axmem_tool failed for %r: %r", tool_name, e, exc_info=True)
            raise ToolError(f"Failed to execute {tool_name!r}: {client_error_detail(e)}")

    execute_axmem_tool.__doc__ = execute_doc
    mcp.tool()(execute_axmem_tool)
