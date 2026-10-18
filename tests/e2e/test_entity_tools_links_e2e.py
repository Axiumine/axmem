"""E2E tests for linking entities to memories with real PostgreSQL database"""
import pytest

from tests.e2e.entity_tools_e2e_helpers import (
    create_entity,
    create_memory,
    link_entity_to_memory,
    unlink_entity_from_memory,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.e2e
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


@pytest.mark.e2e
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
