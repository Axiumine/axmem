"""E2E tests for entity CRUD MCP tools with real PostgreSQL database

Related entity topics live in sibling modules: links, relationships, search,
projects and the entity/memory queries.
"""
import pytest

from tests.e2e.entity_tools_e2e_helpers import (
    call_tool,
    create_entity,
    get_entity,
    list_entities,
    update_entity,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.e2e
async def test_create_entity_basic_e2e(mcp_client):
    """Test creating an entity with all fields"""
    result = await create_entity(
        mcp_client,
        name="Acme Corporation",
        entity_type="Organization",
        notes="A leading software company",
        tags=["tech", "b2b", "enterprise"],
    )
    assert result.data is not None
    assert result.data["id"] is not None
    assert result.data["name"] == "Acme Corporation"
    assert result.data["entity_type"] == "Organization"
    assert result.data["notes"] == "A leading software company"
    assert result.data["tags"] == ["tech", "b2b", "enterprise"]
    assert result.data["created_at"] is not None
    assert result.data["updated_at"] is not None


@pytest.mark.e2e
async def test_create_entity_with_custom_type_e2e(mcp_client):
    """Test creating entity with custom type"""
    result = await create_entity(
        mcp_client,
        name="Temperature Sensor A1",
        entity_type="Other",
        custom_type="IoT Sensor",
        notes="Temperature monitoring device",
        tags=["hardware", "iot"],
    )
    assert result.data is not None
    assert result.data["entity_type"] == "Other"
    assert result.data["custom_type"] == "IoT Sensor"


@pytest.mark.e2e
async def test_get_entity_e2e(mcp_client):
    """Test creating then retrieving an entity"""
    create_result = await create_entity(
        mcp_client, name="Test Entity", entity_type="Individual", tags=["test"],
    )
    entity_id = create_result.data["id"]
    get_result = await get_entity(mcp_client, entity_id)
    assert get_result.data is not None
    assert get_result.data["id"] == entity_id
    assert get_result.data["name"] == "Test Entity"


@pytest.mark.e2e
async def test_list_entities_e2e(mcp_client):
    """Test listing entities"""
    entity_names = ["entity-list-1", "entity-list-2", "entity-list-3"]
    for name in entity_names:
        await create_entity(
            mcp_client, name=name, entity_type="Organization", tags=["list-test"],
        )
    list_result = await list_entities(mcp_client)
    assert list_result.data is not None
    assert "entities" in list_result.data
    assert "total_count" in list_result.data
    entities = list_result.data["entities"]
    assert len(entities) == 3
    assert list_result.data["total_count"] == 3
    entity_names_in_result = [e["name"] for e in entities]
    for name in entity_names:
        assert name in entity_names_in_result


@pytest.mark.e2e
async def test_list_entities_filter_by_type_e2e(mcp_client):
    """Test filtering entities by type"""
    await create_entity(
        mcp_client, name="Test Company", entity_type="Organization",
        tags=["filter-test"],
    )
    await create_entity(
        mcp_client, name="Test Person", entity_type="Individual",
        tags=["filter-test"],
    )
    list_result = await list_entities(mcp_client, entity_type="Organization")
    entities = list_result.data["entities"]
    org_entities = [e for e in entities if "filter-test" in e["tags"]]
    assert len(org_entities) >= 1
    assert all(e["entity_type"] == "Organization" for e in org_entities)


@pytest.mark.e2e
async def test_list_entities_filter_by_tags_e2e(mcp_client):
    """Test filtering entities by tags"""
    await create_entity(
        mcp_client, name="Engineering Team", entity_type="Team",
        tags=["engineering", "tag-filter-test"],
    )
    await create_entity(
        mcp_client, name="Sales Team", entity_type="Team",
        tags=["sales", "tag-filter-test"],
    )
    list_result = await list_entities(mcp_client, tags=["engineering"])
    entities = list_result.data["entities"]
    eng_entities = [e for e in entities if "tag-filter-test" in e["tags"]]
    assert len(eng_entities) >= 1
    assert all("engineering" in e["tags"] for e in eng_entities)


@pytest.mark.e2e
async def test_update_entity_e2e(mcp_client):
    """Test updating an entity (PATCH semantics)"""
    create_result = await create_entity(
        mcp_client, name="Original Name", entity_type="Organization",
        notes="Original notes", tags=["original"],
    )
    entity_id = create_result.data["id"]
    update_result = await update_entity(
        mcp_client, entity_id=entity_id, name="Updated Name", notes="Updated notes",
    )
    assert update_result.data["name"] == "Updated Name"
    assert update_result.data["notes"] == "Updated notes"
    assert update_result.data["tags"] == ["original"]


@pytest.mark.e2e
async def test_delete_entity_e2e(mcp_client):
    """Test deleting an entity"""
    create_result = await create_entity(
        mcp_client, name="To Delete", entity_type="Organization", tags=[],
    )
    entity_id = create_result.data["id"]
    delete_result = await call_tool(
        mcp_client, "delete_entity", {"entity_id": entity_id},
    )
    assert delete_result.data is not None
    assert delete_result.data["deleted_id"] == entity_id
    try:
        await get_entity(mcp_client, entity_id)
        assert False, "Expected error for deleted entity"
    except Exception as e:
        assert "not found" in str(e).lower()


@pytest.mark.e2e
async def test_get_entity_not_found_e2e(mcp_client):
    """Test error handling for non-existent entity"""
    try:
        await get_entity(mcp_client, 999999)
        assert False, "Expected error for non-existent entity"
    except Exception as e:
        assert "not found" in str(e).lower()
