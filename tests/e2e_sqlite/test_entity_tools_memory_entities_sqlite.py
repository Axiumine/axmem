"""E2E tests for the get_memory_entities MCP tool with in-memory SQLite."""
import pytest
from fastmcp.exceptions import ToolError

from app.config.settings import settings
from tests.e2e_sqlite.entity_tools_sqlite_helpers import (
    create_entity,
    create_memory,
    get_memory_entities,
    link_entity_to_memory,
    unlink_entity_from_memory,
)


async def _create_lookup_memory(client, title):
    """Create a memory used for reverse-lookup tests."""
    return await create_memory(
        client,
        title=title,
        content=f"Memory body for {title}",
        context="Testing get_memory_entities",
        keywords=["entity", "reverse"],
        tags=["test"],
        importance=6,
    )


async def _create_lookup_entity(client, name, entity_type="Organization"):
    """Create an entity tagged for reverse-lookup tests."""
    return await create_entity(
        client, name=name, entity_type=entity_type, tags=["reverse-lookup"],
    )


async def _create_linked_entity(client, memory_id, name, entity_type="Organization"):
    """Create a reverse-lookup entity, link it to the memory, return its id."""
    entity_result = await _create_lookup_entity(client, name, entity_type)
    entity_id = entity_result.data["id"]
    await link_entity_to_memory(client, entity_id, memory_id)
    return entity_id


@pytest.mark.asyncio
async def test_get_memory_entities_returns_all_direct_links_e2e(mcp_client):
    memory_result = await _create_lookup_memory(mcp_client, "Memory with three entities")
    memory_id = memory_result.data["id"]
    expected = {}
    for name, entity_type in [("Acme", "Organization"), ("Sarah", "Individual"), ("Ops", "Team")]:
        entity_id = await _create_linked_entity(mcp_client, memory_id, name, entity_type)
        expected[entity_id] = (name, entity_type)

    result = await get_memory_entities(mcp_client, memory_id)
    data = result.data

    assert data["count"] == 3
    assert data["entity_ids"] == sorted(expected)
    assert [entity["id"] for entity in data["entities"]] == data["entity_ids"]
    assert {entity["id"]: (entity["name"], entity["entity_type"]) for entity in data["entities"]} == expected


@pytest.mark.asyncio
async def test_get_memory_entities_empty_e2e(mcp_client):
    memory_result = await _create_lookup_memory(mcp_client, "Memory without entities")
    memory_id = memory_result.data["id"]

    result = await get_memory_entities(mcp_client, memory_id)

    assert result.data == {"entity_ids": [], "count": 0, "entities": []}


@pytest.mark.asyncio
async def test_get_memory_entities_excludes_other_memories_e2e(mcp_client):
    target_result = await _create_lookup_memory(mcp_client, "Target memory")
    target_id = target_result.data["id"]
    other_result = await _create_lookup_memory(mcp_client, "Other memory")
    other_id = other_result.data["id"]
    mine_result = await _create_lookup_entity(mcp_client, "Linked to target")
    mine = mine_result.data["id"]
    theirs_result = await _create_lookup_entity(mcp_client, "Linked to other")
    theirs = theirs_result.data["id"]
    await link_entity_to_memory(mcp_client, mine, target_id)
    await link_entity_to_memory(mcp_client, theirs, other_id)

    result = await get_memory_entities(mcp_client, target_id)

    assert result.data["entity_ids"] == [mine]


@pytest.mark.asyncio
async def test_get_memory_entities_missing_memory_e2e(mcp_client):
    try:
        await get_memory_entities(mcp_client, 999999)
        assert False, "Expected error for non-existent memory"
    except Exception as e:
        assert "not found" in str(e).lower()


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_get_memory_entities_reflects_link_and_unlink_e2e(mcp_client):
    memory_result = await _create_lookup_memory(mcp_client, "Memory for link changes")
    memory_id = memory_result.data["id"]
    entity_result = await _create_lookup_entity(mcp_client, "Comes and goes")
    entity_id = entity_result.data["id"]

    await link_entity_to_memory(mcp_client, entity_id, memory_id)
    linked = await get_memory_entities(mcp_client, memory_id)
    assert linked.data["entity_ids"] == [entity_id]

    await unlink_entity_from_memory(mcp_client, entity_id, memory_id)
    unlinked = await get_memory_entities(mcp_client, memory_id)
    assert unlinked.data["entity_ids"] == []


@pytest.mark.asyncio
async def test_get_memory_entities_large_set_is_complete_e2e(mcp_client):
    memory_result = await _create_lookup_memory(mcp_client, "Memory with many entities")
    memory_id = memory_result.data["id"]
    entity_ids = [
        await _create_linked_entity(mcp_client, memory_id, f"Bulk entity {index}")
        for index in range(25)
    ]

    result = await get_memory_entities(mcp_client, memory_id)
    data = result.data

    assert data["count"] == 25
    assert data["entity_ids"] == sorted(entity_ids)


@pytest.mark.asyncio
async def test_get_memory_entities_available_with_read_scope_e2e(mcp_client, sqlite_app):
    from app.routes.mcp.scope_resolver import parse_scopes, resolve_permitted_tools

    memory_result = await _create_lookup_memory(mcp_client, "Memory for read scope")
    memory_id = memory_result.data["id"]
    entity_id = await _create_linked_entity(
        mcp_client, memory_id, "Readable entity",
    )

    instance_scopes = parse_scopes("read")
    sqlite_app._instance_permitted_tools = resolve_permitted_tools(instance_scopes, sqlite_app.registry)
    sqlite_app._instance_scopes = instance_scopes

    discovered = await mcp_client.call_tool("discover_forgetful_tools", {})
    tools = [tool for cat_tools in discovered.data["tools_by_category"].values() for tool in cat_tools]
    matches = [tool for tool in tools if tool["name"] == "get_memory_entities"]
    assert len(matches) == 1
    assert matches[0]["mutates"] is False

    result = await get_memory_entities(mcp_client, memory_id)
    assert result.data["entity_ids"] == [entity_id]
