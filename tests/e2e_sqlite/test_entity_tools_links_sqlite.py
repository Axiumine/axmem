"""E2E tests for linking entities to memories and projects with in-memory SQLite."""
import pytest

from tests.e2e_sqlite.entity_tools_sqlite_helpers import (
    create_entity,
    create_memory,
    create_project,
    link_entity_to_memory,
    link_entity_to_project,
    unlink_entity_from_memory,
    unlink_entity_from_project,
)


@pytest.mark.asyncio
async def test_link_entity_to_memory_e2e(mcp_client):
    """Test linking entity to memory"""
    entity_result = await create_entity(
        mcp_client, name="Test Entity", entity_type="Organization", tags=[],
    )
    entity_id = entity_result.data["id"]
    memory_result = await create_memory(
        mcp_client,
        title="Test Memory",
        content="Memory for linking test",
        context="Testing entity-memory linking",
        keywords=["test"],
        tags=[],
        importance=7,
    )
    memory_id = memory_result.data["id"]
    link_result = await link_entity_to_memory(mcp_client, entity_id, memory_id)
    assert link_result.data is not None
    assert link_result.data["success"] is True


@pytest.mark.asyncio
async def test_unlink_entity_from_memory_e2e(mcp_client):
    """Test unlinking entity from memory"""
    entity_result = await create_entity(
        mcp_client, name="Test Entity", entity_type="Organization", tags=[],
    )
    entity_id = entity_result.data["id"]
    memory_result = await create_memory(
        mcp_client,
        title="Test Memory",
        content="Memory for unlink test",
        context="Testing entity-memory unlinking",
        keywords=["test"],
        tags=[],
        importance=7,
    )
    memory_id = memory_result.data["id"]
    await link_entity_to_memory(mcp_client, entity_id, memory_id)
    unlink_result = await unlink_entity_from_memory(mcp_client, entity_id, memory_id)
    assert unlink_result.data is not None
    assert unlink_result.data["success"] is True


@pytest.mark.asyncio
async def test_link_entity_to_project_e2e(mcp_client):
    """Test linking entity to project"""
    # Create entity
    entity_result = await create_entity(
        mcp_client, name="Test Entity", entity_type="Organization", tags=[],
    )
    entity_id = entity_result.data["id"]

    # Create project
    project_result = await create_project(
        mcp_client,
        name="Test Project",
        description="Project for linking test",
        project_type="development",
    )
    project_id = project_result.data["id"]

    # Link entity to project
    link_result = await link_entity_to_project(mcp_client, entity_id, project_id)
    assert link_result.data is not None
    assert link_result.data["success"] is True


@pytest.mark.asyncio
async def test_unlink_entity_from_project_e2e(mcp_client):
    """Test unlinking entity from project"""
    # Create entity
    entity_result = await create_entity(
        mcp_client, name="Test Entity", entity_type="Organization", tags=[],
    )
    entity_id = entity_result.data["id"]

    # Create project
    project_result = await create_project(
        mcp_client,
        name="Test Project",
        description="Project for unlink test",
        project_type="development",
    )
    project_id = project_result.data["id"]

    # Link first
    await link_entity_to_project(mcp_client, entity_id, project_id)

    # Unlink
    unlink_result = await unlink_entity_from_project(mcp_client, entity_id, project_id)
    assert unlink_result.data is not None
    assert unlink_result.data["success"] is True
