# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract of the three MCP meta-tools: the descriptions a client reads.

The tool descriptions are what an agent is told about the surface. Each is pinned word for word through the
mounted FastMCP server, with stubbed tool implementations behind a real ``ToolRegistry``.
"""
import inspect
from types import SimpleNamespace

import pytest
from fastmcp import Client

from app.config.settings import settings
from app.routes.mcp import meta_tools
from tests.mcp_meta_tools_support import build_registry, mounted_server

# ---------------------------------------------------------------------------
# tool descriptions
# ---------------------------------------------------------------------------

DISCOVER_VERBOSE_HEAD = (
    "Discover available tools, optionally filtered by category\n"
    "\n"
    "    Returns enough information for LLMs to call tools directly without needing how_to_use.\n"
    "\n"
    "    Args:\n"
    "        category: Optional category filter (memory, linking)\n"
    "        ctx: FastMCP Context (automatically injected)\n"
    "\n"
    "    Returns:\n"
    "        Dictionary with:\n"
    "        - tools_by_category: Tools grouped by category\n"
    "        - total_count: Total number of tools\n"
    "        - categories_available: List of available categories\n"
    "        - filtered_by: Category filter applied (if any)\n"
    "\n"
    "    ## All Available Tools\n"
)
DISCOVER_VERBOSE_TAIL = (
    "\n"
    "\n"
    "    ## Workflow\n"
    "    1. Call discover_axmem_tools() to see this catalog\n"
    "    2. Call execute_axmem_tool(tool_name, {args}) to run any tool\n"
    "    3. If needed, call how_to_use_axmem_tool(tool_name) for full parameter docs\n"
    "    "
)
DISCOVER_COMPACT = (
    "Discover available Axmem tools, optionally filtered by category.\n"
    "\n"
    "    Use this tool to list the available inner tools. Call how_to_use_axmem_tool(tool_name)\n"
    "    for full parameter documentation and examples before uncommon or write operations.\n"
    "\n"
    "    Args:\n"
    "        category: Optional category filter (memory, linking)\n"
    "        ctx: FastMCP Context (automatically injected)\n"
    "\n"
    "    Returns:\n"
    "        Dictionary with tools_by_category, total_count, categories_available, and filtered_by.\n"
    "    "
)
EXECUTE_VERBOSE_HEAD = (
    "Execute any registered tool dynamically. Axmem is a semantic memory system for LLMs.\n"
)
EXECUTE_VERBOSE_TAIL = (
    "\n"
    "\n"
    "    ## Linking Best Practices\n"
    "    **Always link related items for discoverability:**\n"
    '    - Link memories to each other: `link_memories(memory_id="018f2c3e-0000-7000-8000-000000000001", '
    'related_ids=["018f2c3e-0000-7000-8000-000000000002", "018f2c3e-0000-7000-8000-000000000003"])`\n'
    "\n"
    "    ## Tool Categories\n"
    "    memory | linking\n"
    "\n"
    "    Use discover_axmem_tools(category?) for full parameter details and optional params.\n"
    "\n"
    "    ---\n"
    "\n"
    "    Args:\n"
    "        tool_name: Name of the tool to execute\n"
    "        arguments: Dictionary of arguments to pass to the tool\n"
    "        ctx: FastMCP Context (automatically injected)\n"
    "\n"
    "    Returns:\n"
    "        Tool execution result (format depends on the specific tool)\n"
    "    "
)
EXECUTE_COMPACT = (
    "Execute any registered Axmem inner tool by name.\n"
    "\n"
    "    Typical flow:\n"
    "    1. Call discover_axmem_tools(category?) to find tool names.\n"
    "    2. Call how_to_use_axmem_tool(tool_name) for full schemas and examples.\n"
    "    3. Call execute_axmem_tool(tool_name, arguments) to run the selected tool.\n"
    "\n"
    "    Tool categories: memory | linking\n"
    "\n"
    "    Args:\n"
    "        tool_name: Name of the inner tool to execute\n"
    "        arguments: Dictionary of arguments for the selected tool\n"
    "        ctx: FastMCP Context (automatically injected)\n"
    "\n"
    "    Returns:\n"
    "        Tool execution result (format depends on the selected tool)\n"
    "    "
)
HOW_TO_USE_DESCRIPTION = (
    "Get detailed documentation for a specific tool\n"
    "\n"
    "Returns complete documentation including JSON schema, multiple examples, and full parameter details.\n"
    "\n"
    "Args:\n"
    "    tool_name: Name of the tool to get documentation for\n"
    "    ctx: FastMCP Context (automatically injected)\n"
    "\n"
    "Returns:\n"
    "    Detailed tool documentation with JSON schema"
)


def _discover_verbose() -> str:
    return DISCOVER_VERBOSE_HEAD + meta_tools._DISCOVER_CORE_SECTIONS + DISCOVER_VERBOSE_TAIL


def _execute_verbose() -> str:
    return EXECUTE_VERBOSE_HEAD + meta_tools._EXECUTE_CORE_EXAMPLES + EXECUTE_VERBOSE_TAIL


@pytest.mark.parametrize(
    ("mode", "discover", "execute"),
    [
        ("verbose", _discover_verbose, _execute_verbose),
        ("compact", lambda: DISCOVER_COMPACT, lambda: EXECUTE_COMPACT),
    ],
)
def test_the_docstring_builders_write_the_text_of_the_configured_descriptor_mode(monkeypatch, mode, discover, execute):
    monkeypatch.setattr(settings, "MCP_DESCRIPTOR_MODE", mode)

    assert meta_tools._build_discover_docstring() == discover()
    assert meta_tools._build_execute_docstring() == execute()


@pytest.mark.parametrize("mode", ["compact", "Compact", " COMPACT\n", "\tcompact "])
def test_the_compact_descriptor_mode_ignores_case_and_surrounding_whitespace(monkeypatch, mode):
    monkeypatch.setattr(settings, "MCP_DESCRIPTOR_MODE", mode)

    assert meta_tools._get_mcp_descriptor_mode() == "compact"


@pytest.mark.parametrize("mode", ["verbose", "VERBOSE", "", "  ", "minimal", "compact-ish", None])
def test_any_other_descriptor_mode_is_verbose(monkeypatch, mode):
    monkeypatch.setattr(settings, "MCP_DESCRIPTOR_MODE", mode)

    assert meta_tools._get_mcp_descriptor_mode() == "verbose"


def test_a_settings_object_without_a_descriptor_mode_gives_the_verbose_form(monkeypatch):
    monkeypatch.setattr(meta_tools, "settings", SimpleNamespace())

    assert meta_tools._get_mcp_descriptor_mode() == "verbose"


def test_the_category_lists_name_the_categories_a_client_can_filter_by():
    assert meta_tools._build_category_list() == "memory, linking"
    assert meta_tools._build_tool_categories_line() == "memory | linking"


@pytest.mark.parametrize("mode", ["verbose", "compact"])
async def test_a_client_is_shown_the_three_tools_with_their_descriptions(monkeypatch, mode):
    monkeypatch.setattr(settings, "MCP_DESCRIPTOR_MODE", mode)
    discover, execute = (_discover_verbose(), _execute_verbose()) if mode == "verbose" else (DISCOVER_COMPACT, EXECUTE_COMPACT)

    async with Client(mounted_server(build_registry())) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    assert list(tools) == ["discover_axmem_tools", "how_to_use_axmem_tool", "execute_axmem_tool"]
    assert tools["discover_axmem_tools"].description == inspect.cleandoc(discover)
    assert tools["how_to_use_axmem_tool"].description == HOW_TO_USE_DESCRIPTION
    assert tools["execute_axmem_tool"].description == inspect.cleandoc(execute)
    # FastMCP 3.4 copies the text of each argument from the docstring into the input schema
    assert tools["discover_axmem_tools"].inputSchema["properties"] == {
        "category": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "default": None,
            "description": "Optional category filter (memory, linking)",
        },
    }
    assert tools["how_to_use_axmem_tool"].inputSchema["required"] == ["tool_name"]
    assert tools["execute_axmem_tool"].inputSchema["required"] == ["tool_name", "arguments"]
