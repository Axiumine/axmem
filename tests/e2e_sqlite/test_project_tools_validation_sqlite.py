"""E2E tests for project MCP tool discovery and ID validation (SQLite)."""

import pytest
from fastmcp.exceptions import ToolError

from tests.e2e_sqlite.project_tools_sqlite_helpers import (
    assert_missing_project_error,
    create_project,
    execute,
    get_project,
)


@pytest.mark.asyncio
async def test_project_name_lookup_is_discoverable(mcp_client):
    """Discovery and detailed help guide name lookups to list_projects."""
    discovery = await mcp_client.call_tool("discover_forgetful_tools", {"category": "project"})
    project_tools = {
        tool["name"]: tool for tool in discovery.data["tools_by_category"]["project"]
    }

    name_parameter = project_tools["list_projects"]["parameters"]["name"]
    assert name_parameter["required"] is False
    assert "case-insensitive" in name_parameter["description"]
    assert "partial" in name_parameter["description"]
    assert "list_projects" in project_tools["get_project"]["description"]
    assert "name" not in project_tools["get_project"]["parameters"]

    documentation = await mcp_client.call_tool(
        "how_to_use_forgetful_tool", {"tool_name": "list_projects"},
    )
    assert documentation.data["json_schema"]["properties"]["name"]["type"] == "string"
    assert "name" not in documentation.data["json_schema"].get("required", [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments", [{"name": "example"}, {"project_id": 1, "name": "example"}],
)
async def test_get_project_rejects_name_argument(mcp_client, arguments):
    """A name is not silently substituted for the required project ID."""
    with pytest.raises(ToolError, match="unexpected keyword argument 'name'"):
        await execute(mcp_client, "get_project", arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["get_project", "update_project", "delete_project"])
@pytest.mark.parametrize("invalid_id", ["not-an-id", True, 1.9, None])
async def test_project_tools_reject_invalid_ids(mcp_client, tool_name, invalid_id):
    """Invalid IDs give a parameter error and leave the existing project unchanged."""
    created = await create_project(
        mcp_client, "unchanged-project", "Project protected from invalid IDs",
    )
    arguments = {"project_id": invalid_id}
    if tool_name == "update_project":
        arguments["name"] = "should-not-change"

    with pytest.raises(ToolError, match="project_id (must|cannot)"):
        await execute(mcp_client, tool_name, arguments)

    retrieved = await get_project(mcp_client, created.data["id"])
    assert retrieved.data["name"] == "unchanged-project"


@pytest.mark.asyncio
async def test_get_project_invalid_id_e2e(mcp_client):
    """Test error handling when getting non-existent project"""
    with pytest.raises(ToolError, match="not found"):
        await get_project(mcp_client, 999999)


@pytest.mark.asyncio
async def test_update_project_invalid_id_e2e(mcp_client):
    """Test error handling when updating non-existent project"""
    await assert_missing_project_error(
        mcp_client,
        "update_project",
        {"project_id": 999999, "name": "This Should Fail"},
    )


@pytest.mark.asyncio
async def test_delete_project_invalid_id_e2e(mcp_client):
    """Test error handling when deleting non-existent project"""
    result = await execute(mcp_client, "delete_project", {"project_id": 999999})
    # Delete returns success=False for non-existent projects
    assert result.data is not None
    assert result.data["success"] is False
