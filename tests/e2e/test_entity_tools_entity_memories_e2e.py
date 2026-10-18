"""E2E tests for the get_entity_memories MCP tool with real PostgreSQL database"""
import pytest

from tests.e2e.entity_tools_e2e_helpers import (
    create_entity,
    create_memory,
    get_entity_memories,
    link_entity_to_memory,
    unlink_entity_from_memory,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_linked_memory(client, entity_id, **memory_fields):
    """Create a memory with the given fields, link it to the entity, return its id."""
    memory_result = await create_memory(client, **memory_fields)
    memory_id = memory_result.data["id"]
    await link_entity_to_memory(client, entity_id, memory_id)
    return memory_id


@pytest.mark.e2e
async def test_get_entity_memories_basic_e2e(mcp_client):
    """Test getting memories linked to an entity via MCP tool"""
    # Create entity
    entity_result = await create_entity(
        mcp_client,
        name="Entity for Memory Query PG",
        entity_type="Organization",
        tags=["memory-query-e2e-pg"],
    )
    entity_id = entity_result.data["id"]

    # Create some memories and link them to the entity
    memory_ids = [
        await _create_linked_memory(
            mcp_client,
            entity_id,
            title=f"Memory for Entity Query Test PG {i}",
            content=f"Content for memory {i}",
            context="Testing get_entity_memories",
            keywords=["test"],
            tags=["memory-query-e2e-pg"],
            importance=7,
        )
        for i in range(3)
    ]

    # Get entity memories
    result = await get_entity_memories(mcp_client, entity_id)

    assert result.data is not None
    assert "memory_ids" in result.data
    assert "count" in result.data
    assert result.data["count"] == 3
    assert len(result.data["memory_ids"]) == 3
    for mid in memory_ids:
        assert mid in result.data["memory_ids"]


@pytest.mark.e2e
async def test_get_entity_memories_empty_e2e(mcp_client):
    """Test getting memories for entity with no linked memories"""
    # Create entity with no memory links
    entity_result = await create_entity(
        mcp_client,
        name="Entity With No Memories PG",
        entity_type="Individual",
        tags=["empty-memory-e2e-pg"],
    )
    entity_id = entity_result.data["id"]

    # Get entity memories (should be empty)
    result = await get_entity_memories(mcp_client, entity_id)

    assert result.data is not None
    assert result.data["count"] == 0
    assert result.data["memory_ids"] == []


@pytest.mark.e2e
async def test_get_entity_memories_not_found_e2e(mcp_client):
    """Test error handling for non-existent entity"""
    try:
        await get_entity_memories(mcp_client, 999999)
        assert False, "Expected error for non-existent entity"
    except Exception as e:
        assert "not found" in str(e).lower()


@pytest.mark.e2e
async def test_get_entity_memories_after_unlink_e2e(mcp_client):
    """Test that unlinking removes memory from entity's memory list"""
    # Create entity
    entity_result = await create_entity(
        mcp_client,
        name="Entity for Unlink Test PG",
        entity_type="Device",
        tags=["unlink-memory-e2e-pg"],
    )
    entity_id = entity_result.data["id"]

    # Create and link 2 memories
    memory_ids = [
        await _create_linked_memory(
            mcp_client,
            entity_id,
            title=f"Memory for Unlink Test PG {i}",
            content=f"Content {i}",
            context="Testing unlink",
            keywords=["test"],
            tags=["unlink-memory-e2e-pg"],
            importance=7,
        )
        for i in range(2)
    ]

    # Verify initial state
    result = await get_entity_memories(mcp_client, entity_id)
    assert result.data["count"] == 2

    # Unlink one memory
    await unlink_entity_from_memory(mcp_client, entity_id, memory_ids[0])

    # Verify memory was removed
    result = await get_entity_memories(mcp_client, entity_id)
    assert result.data["count"] == 1
    assert memory_ids[1] in result.data["memory_ids"]
    assert memory_ids[0] not in result.data["memory_ids"]
