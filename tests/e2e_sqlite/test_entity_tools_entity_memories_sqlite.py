"""E2E tests for the get_entity_memories MCP tool with in-memory SQLite."""
import pytest

from tests.e2e_sqlite.entity_tools_sqlite_helpers import (
    create_entity,
    create_memory,
    get_entity_memories,
    link_entity_to_memory,
    unlink_entity_from_memory,
)


async def _create_linked_memory(client, entity_id, **memory_fields):
    """Create a memory with the given fields, link it to the entity, return its id."""
    memory_result = await create_memory(client, **memory_fields)
    memory_id = memory_result.data["id"]
    await link_entity_to_memory(client, entity_id, memory_id)
    return memory_id


def _assert_memory_ids_payload(data, memory_ids):
    """The memory_ids/count part of the response lists exactly the linked ids."""
    assert "memory_ids" in data
    assert "count" in data
    assert data["count"] == 3
    assert len(data["memory_ids"]) == 3
    for mid in memory_ids:
        assert mid in data["memory_ids"]


def _assert_memories_payload(data, memory_ids):
    """'memories' must carry {id, title} objects matching memory_ids."""
    assert "memories" in data
    assert len(data["memories"]) == 3
    returned_ids = {m["id"] for m in data["memories"]}
    assert returned_ids == set(memory_ids)
    titles_by_id = {m["id"]: m["title"] for m in data["memories"]}
    for i, mid in enumerate(memory_ids):
        assert titles_by_id[mid] == f"Memory for Entity Query Test SQLite {i}"


@pytest.mark.asyncio
async def test_get_entity_memories_basic_e2e(mcp_client):
    """Test getting memories linked to an entity via MCP tool"""
    # Create entity
    entity_result = await create_entity(
        mcp_client,
        name="Entity for Memory Query SQLite",
        entity_type="Organization",
        tags=["memory-query-e2e-sqlite"],
    )
    entity_id = entity_result.data["id"]

    # Create some memories and link them to the entity
    memory_ids = [
        await _create_linked_memory(
            mcp_client,
            entity_id,
            title=f"Memory for Entity Query Test SQLite {i}",
            content=f"Content for memory {i}",
            context="Testing get_entity_memories",
            keywords=["test"],
            tags=["memory-query-e2e-sqlite"],
            importance=7,
        )
        for i in range(3)
    ]

    # Get entity memories
    result = await get_entity_memories(mcp_client, entity_id)

    assert result.data is not None
    _assert_memory_ids_payload(result.data, memory_ids)
    _assert_memories_payload(result.data, memory_ids)


@pytest.mark.asyncio
async def test_get_entity_memories_empty_e2e(mcp_client):
    """Test getting memories for entity with no linked memories"""
    # Create entity with no memory links
    entity_result = await create_entity(
        mcp_client,
        name="Entity With No Memories SQLite",
        entity_type="Individual",
        tags=["empty-memory-e2e-sqlite"],
    )
    entity_id = entity_result.data["id"]

    # Get entity memories (should be empty)
    result = await get_entity_memories(mcp_client, entity_id)

    assert result.data is not None
    assert result.data["count"] == 0
    assert result.data["memory_ids"] == []
    assert result.data["memories"] == []


@pytest.mark.asyncio
async def test_get_entity_memories_not_found_e2e(mcp_client):
    """Test error handling for non-existent entity"""
    try:
        await get_entity_memories(mcp_client, 999999)
        assert False, "Expected error for non-existent entity"
    except Exception as e:
        assert "not found" in str(e).lower()


@pytest.mark.asyncio
async def test_get_entity_memories_after_unlink_e2e(mcp_client):
    """Test that unlinking removes memory from entity's memory list"""
    # Create entity
    entity_result = await create_entity(
        mcp_client,
        name="Entity for Unlink Test SQLite",
        entity_type="Device",
        tags=["unlink-memory-e2e-sqlite"],
    )
    entity_id = entity_result.data["id"]

    # Create and link 2 memories
    memory_ids = [
        await _create_linked_memory(
            mcp_client,
            entity_id,
            title=f"Memory for Unlink Test SQLite {i}",
            content=f"Content {i}",
            context="Testing unlink",
            keywords=["test"],
            tags=["unlink-memory-e2e-sqlite"],
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
    assert len(result.data["memories"]) == 1
    assert result.data["memories"][0]["id"] == memory_ids[1]
    assert result.data["memories"][0]["title"] == "Memory for Unlink Test SQLite 1"
