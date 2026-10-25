"""What the tests of the MCP meta-tools share: a registry with known tools, the mounted server and the log reader.

The tool implementations behind the registry are stubs that note the keyword arguments they were called with; the
meta-tools, the FastMCP server and the registry are the production ones.
"""
import logging
from typing import Any

from fastmcp import FastMCP

from app.models.tool_registry_models import ToolCategory, ToolParameter
from app.routes.mcp import meta_tools
from app.routes.mcp.tool_registry import ToolRegistry

META_LOGGER = "app.routes.mcp.meta_tools"
MEMORY_ID = "01a157bf-5d0f-77ae-9308-9af4ac2edb17"

CALLS: list[dict[str, Any]] = []

GET_MEMORY_DISCOVERY = {
    "name": "get_memory",
    "category": "memory",
    "description": "Fetch one memory",
    "mutates": False,
    "parameters": {
        "memory_id": {"type": "str", "required": True, "description": "The memory", "example": MEMORY_ID},
        "ctx": {"type": "Context", "required": True, "description": "Request context", "example": None},
    },
    "returns": "The memory",
    "example": 'get_memory({"memory_id": "..."})',
}


async def record_call(**kwargs):
    CALLS.append(kwargs)
    return {"called_with": sorted(kwargs)}


def build_registry(extra_read_tools: int = 0) -> ToolRegistry:
    """get_memory (read), create_memory (write), link_memories (write, tagged linking), then optional read tools."""
    registry = ToolRegistry()
    registry.register(
        name="get_memory",
        category=ToolCategory.MEMORY,
        description="Fetch one memory",
        parameters=[
            ToolParameter(name="memory_id", type="str", description="The memory", required=True, example=MEMORY_ID),
            ToolParameter(name="ctx", type="Context", description="Request context", required=True),
        ],
        returns="The memory",
        implementation=record_call,
        examples=['get_memory({"memory_id": "..."})'],
        tags=["memory", "query"],
    )
    registry.register(
        name="create_memory",
        category=ToolCategory.MEMORY,
        description="Store a memory",
        parameters=[ToolParameter(name="title", type="str", description="The title", required=True)],
        returns="The new memory",
        implementation=record_call,
        tags=["memory", "create"],
        mutates=True,
    )
    registry.register(
        name="link_memories",
        category=ToolCategory.MEMORY,
        description="Link memories",
        parameters=[],
        returns="The links",
        implementation=record_call,
        tags=["memory", "linking"],
        mutates=True,
    )
    for index in range(extra_read_tools):
        registry.register(
            name=f"extra_tool_{index:02d}",
            category=ToolCategory.MEMORY,
            description="Extra",
            parameters=[],
            returns="Nothing",
            implementation=record_call,
        )
    return registry


def mounted_server(
    registry: ToolRegistry, permitted: set[str] | None = None, scopes: frozenset[str] | None = None,
) -> FastMCP:
    """The mounted surface over the registry; ``permitted`` is the instance ceiling the lifespan would attach."""
    server = FastMCP("meta-tools-contract")
    server.registry = registry
    if permitted is not None:
        server._instance_permitted_tools = permitted
        server._instance_scopes = scopes or frozenset({"*"})
    meta_tools.register(server)
    return server


def payload_names(payload: dict) -> list[str]:
    return [tool["name"] for tools in payload["tools_by_category"].values() for tool in tools]


def meta_records(caplog) -> list[tuple[str, int, str]]:
    return [(record.name, record.levelno, record.getMessage()) for record in caplog.records if record.name == META_LOGGER]


def logged_errors(caplog) -> list[logging.LogRecord]:
    """The records of level ERROR that the meta-tools logged"""
    return [record for record in caplog.records if record.name == META_LOGGER and record.levelno == logging.ERROR]
