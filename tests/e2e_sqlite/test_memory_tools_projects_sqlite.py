"""End-to-end tests for MCP memory tools: project_ids updates (SQLite backend)

Covers adding one or several projects to a memory and removing them again via
update_memory.
"""
import pytest

from tests.e2e_sqlite.memory_tools_helpers_sqlite import (
    create_memory,
    create_project,
    get_memory,
    update_memory,
)


@pytest.mark.asyncio
async def test_update_memory_add_project_ids_e2e(mcp_client):
    """Test adding project_ids to a memory using update_memory tool"""
    project_result = await create_project(
        mcp_client,
        "update-memory-project-test",
        "Project for testing memory updates with project_ids",
    )
    project_id = project_result.data["id"]
    memory_result = await create_memory(
        mcp_client,
        title="Memory without project",
        content="This memory will be updated to add a project",
        context="Testing project_ids updates",
        keywords=["test", "project", "update"],
        tags=["test"],
        importance=7,
    )
    memory_id = memory_result.data["id"]
    assert memory_result.data["project_ids"] == []
    update_result = await update_memory(
        mcp_client, memory_id, project_ids=[project_id],
    )
    assert update_result.data is not None
    assert project_id in update_result.data["project_ids"]
    get_result = await get_memory(mcp_client, memory_id)
    assert project_id in get_result.data["project_ids"]


@pytest.mark.asyncio
async def test_update_memory_add_multiple_projects_e2e(mcp_client):
    """Test adding multiple project_ids to a memory"""
    project1_result = await create_project(
        mcp_client, "multi-project-1", "First project for multi-project test",
    )
    project1_id = project1_result.data["id"]
    project2_result = await create_project(
        mcp_client,
        "multi-project-2",
        "Second project for multi-project test",
        project_type="work",
    )
    project2_id = project2_result.data["id"]
    memory_result = await create_memory(
        mcp_client,
        title="Memory for multiple projects",
        content="This memory belongs to multiple projects",
        context="Testing multiple project_ids",
        keywords=["test", "multi-project"],
        tags=["test"],
        importance=7,
    )
    memory_id = memory_result.data["id"]
    update_result = await update_memory(
        mcp_client, memory_id, project_ids=[project1_id, project2_id],
    )
    assert update_result.data is not None
    assert project1_id in update_result.data["project_ids"]
    assert project2_id in update_result.data["project_ids"]
    assert len(update_result.data["project_ids"]) == 2


@pytest.mark.asyncio
async def test_update_memory_remove_project_ids_e2e(mcp_client):
    """Test removing project_ids from a memory"""
    project_result = await create_project(
        mcp_client, "remove-project-test", "Project to be removed from memory",
    )
    project_id = project_result.data["id"]
    memory_result = await create_memory(
        mcp_client,
        title="Memory with project to remove",
        content="This memory will have its project removed",
        context="Testing project_ids removal",
        keywords=["test", "remove", "project"],
        tags=["test"],
        importance=7,
        project_ids=[project_id],
    )
    memory_id = memory_result.data["id"]
    assert project_id in memory_result.data["project_ids"]
    update_result = await update_memory(mcp_client, memory_id, project_ids=[])
    assert update_result.data is not None
    assert update_result.data["project_ids"] == []
    get_result = await get_memory(mcp_client, memory_id)
    assert get_result.data["project_ids"] == []
