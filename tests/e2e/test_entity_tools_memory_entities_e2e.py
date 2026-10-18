"""E2E tests for the get_memory_entities MCP tool with real PostgreSQL database"""
import pytest
from fastmcp.exceptions import ToolError

from app.config.settings import settings
from tests.e2e.entity_tools_e2e_helpers import (
    create_entity,
    create_memory,
    get_memory_entities,
    link_entity_to_memory,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.e2e
async def test_get_memory_entities_basic_e2e(mcp_client):
    """Test getting entities linked to a memory via MCP tool"""
    memory_result = await create_memory(
        mcp_client,
        title="Memory for Entity Reverse Query PG",
        content="Content for reverse lookup test",
        context="Testing get_memory_entities",
        keywords=["test"],
        tags=["memory-entity-reverse-e2e-pg"],
        importance=7,
    )
    memory_id = memory_result.data["id"]

    entity_ids = []
    for name in ("Entity A PG", "Entity B PG"):
        entity_result = await create_entity(
            mcp_client,
            name=name,
            entity_type="Organization",
            tags=["memory-entity-reverse-e2e-pg"],
        )
        entity_ids.append(entity_result.data["id"])
        await link_entity_to_memory(mcp_client, entity_result.data["id"], memory_id)

    result = await get_memory_entities(mcp_client, memory_id)

    assert result.data is not None
    assert result.data["count"] == 2
    assert result.data["entity_ids"] == sorted(entity_ids)
    assert [entity["id"] for entity in result.data["entities"]] == result.data["entity_ids"]
    entities_by_id = {entity["id"]: entity for entity in result.data["entities"]}
    for entity_id in entity_ids:
        assert entities_by_id[entity_id]["name"] in ("Entity A PG", "Entity B PG")
        assert entities_by_id[entity_id]["entity_type"] == "Organization"


@pytest.mark.e2e
async def test_get_memory_entities_not_found_e2e(mcp_client):
    """Test error handling for non-existent memory"""
    try:
        await get_memory_entities(mcp_client, 999999)
        assert False, "Expected error for non-existent memory"
    except Exception as e:
        assert "not found" in str(e).lower()


@pytest.mark.e2e
@pytest.mark.parametrize("has_linked_entity", [False, True])
async def test_get_memory_entities_user_isolation_e2e(
    mcp_client, monkeypatch, has_linked_entity,
):
    """Another user's memory is inaccessible, whether or not it has entity links."""
    # Arrange: create a memory and verify its owner can read the associations.
    memory_result = await create_memory(
        mcp_client,
        title="Private memory",
        content="Only the owner may inspect this memory's entity links",
        context="Testing cross-user memory entity lookup",
        keywords=["entity", "ownership"],
        tags=["test"],
        importance=6,
    )
    memory_id = memory_result.data["id"]
    expected_entity_ids = []
    if has_linked_entity:
        entity_result = await create_entity(
            mcp_client, name="Private entity", entity_type="Organization", tags=["test"],
        )
        entity_id = entity_result.data["id"]
        await link_entity_to_memory(mcp_client, entity_id, memory_id)
        expected_entity_ids = [entity_id]

    owner_result = await get_memory_entities(mcp_client, memory_id)
    assert owner_result.data["entity_ids"] == expected_entity_ids

    # Act and assert: a different user must get an error, not an empty success.
    with monkeypatch.context() as other_user:
        other_user.setattr(settings, "DEFAULT_USER_ID", "memory-entities-other-user")
        other_user.setattr(settings, "DEFAULT_USER_EMAIL", "other-entity-user@example.test")
        with pytest.raises(ToolError, match=f"Memory {memory_id} not found"):
            await get_memory_entities(mcp_client, memory_id)
