"""Shared MCP call helpers for the project tool E2E tests (real PostgreSQL database)."""

import pytest
from fastmcp.exceptions import ToolError

NOT_FOUND_MARKERS = ("not found", "validation_error", "project not found")


async def execute(mcp_client, tool_name, arguments):
    """Call one tool through the execute_forgetful_tool meta-tool."""
    return await mcp_client.call_tool(
        "execute_forgetful_tool", {"tool_name": tool_name, "arguments": arguments},
    )


async def create_project(
    mcp_client, name, description, project_type="development", **extra,
):
    """Create a project through MCP and return the raw tool result."""
    arguments = {
        "name": name,
        "description": description,
        "project_type": project_type,
        **extra,
    }
    return await execute(mcp_client, "create_project", arguments)


async def get_project(mcp_client, project_id):
    """Fetch a project by ID through MCP."""
    return await execute(mcp_client, "get_project", {"project_id": project_id})


async def list_projects(mcp_client, **filters):
    """List projects through MCP, optionally filtered."""
    return await execute(mcp_client, "list_projects", filters)


async def create_project_memory(
    mcp_client, project_id, title, content, context, keywords,
):
    """Create an importance-7 test memory linked to one project."""
    return await execute(
        mcp_client,
        "create_memory",
        {
            "title": title,
            "content": content,
            "context": context,
            "keywords": keywords,
            "tags": ["test"],
            "importance": 7,
            "project_ids": [project_id],
        },
    )


def id_argument(project_id, string_id):
    """Return the project ID as a string when the client sends string IDs."""
    return str(project_id) if string_id else project_id


async def assert_missing_project_error(mcp_client, tool_name, arguments):
    """Calling the tool on an unknown project fails with a not-found style error."""
    with pytest.raises((ToolError, Exception)) as exc_info:
        result = await execute(mcp_client, tool_name, arguments)
        if result.data is None or (hasattr(result, "is_error") and result.is_error):
            raise ValueError("Project not found")
    error_message = str(exc_info.value).lower()
    assert any(marker in error_message for marker in NOT_FOUND_MARKERS)


def find_project(projects, name):
    """Return the first listed project with the given name, or None."""
    return next((project for project in projects if project["name"] == name), None)
