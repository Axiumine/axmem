"""E2E tests for project MCP tool ID validation (PostgreSQL)."""

import pytest
from fastmcp.exceptions import ToolError

from tests.e2e.project_tools_e2e_helpers import (
    assert_missing_project_error,
    create_project,
    execute,
    get_project,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.e2e
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


@pytest.mark.e2e
async def test_get_project_invalid_id_e2e(mcp_client):
    """Test error handling when getting non-existent project"""
    with pytest.raises(ToolError, match="not found"):
        await get_project(mcp_client, 999999)


@pytest.mark.e2e
async def test_update_project_invalid_id_e2e(mcp_client):
    """Test error handling when updating non-existent project"""
    await assert_missing_project_error(
        mcp_client,
        "update_project",
        {"project_id": 999999, "name": "This Should Fail"},
    )


@pytest.mark.e2e
async def test_delete_project_invalid_id_e2e(mcp_client):
    """Test error handling when deleting non-existent project"""
    result = await execute(mcp_client, "delete_project", {"project_id": 999999})
    # Delete returns success=False for non-existent projects
    assert result.data is not None
    assert result.data["success"] is False
