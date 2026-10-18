"""E2E tests for entity relationship MCP tools with real PostgreSQL database"""
import pytest

from tests.e2e.entity_tools_e2e_helpers import (
    call_tool,
    create_entity,
    create_relationship,
    get_entity_relationships,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.e2e
async def test_create_entity_relationship_e2e(mcp_client):
    """Test creating relationship between entities"""
    entity1_result = await create_entity(
        mcp_client, name="Company A", entity_type="Organization", tags=[],
    )
    entity1_id = entity1_result.data["id"]
    entity2_result = await create_entity(
        mcp_client, name="Person B", entity_type="Individual", tags=[],
    )
    entity2_id = entity2_result.data["id"]
    rel_result = await create_relationship(
        mcp_client,
        source_entity_id=entity1_id,
        target_entity_id=entity2_id,
        relationship_type="employs",
        strength=0.9,
        confidence=0.85,
        metadata={"role": "engineer", "department": "R&D"},
    )
    assert rel_result.data is not None
    assert rel_result.data["id"] is not None
    assert rel_result.data["source_entity_id"] == entity1_id
    assert rel_result.data["target_entity_id"] == entity2_id
    assert rel_result.data["relationship_type"] == "employs"
    assert rel_result.data["strength"] == 0.9
    assert rel_result.data["confidence"] == 0.85
    assert rel_result.data["metadata"] == {"role": "engineer", "department": "R&D"}


@pytest.mark.e2e
async def test_get_entity_relationships_e2e(mcp_client):
    """Test retrieving entity relationships"""
    entity1_result = await create_entity(
        mcp_client, name="Org X", entity_type="Organization", tags=[],
    )
    entity1_id = entity1_result.data["id"]
    entity2_result = await create_entity(
        mcp_client, name="Person Y", entity_type="Individual", tags=[],
    )
    entity2_id = entity2_result.data["id"]
    await create_relationship(
        mcp_client,
        source_entity_id=entity1_id,
        target_entity_id=entity2_id,
        relationship_type="collaborates_with",
    )
    rel_result = await get_entity_relationships(mcp_client, entity_id=entity1_id)
    assert rel_result.data is not None
    assert "relationships" in rel_result.data
    relationships = rel_result.data["relationships"]
    assert len(relationships) >= 1
    assert any(r["source_entity_id"] == entity1_id for r in relationships)


@pytest.mark.e2e
async def test_get_entity_relationships_filter_direction_e2e(mcp_client):
    """Test filtering relationships by direction"""
    entity1_result = await create_entity(
        mcp_client, name="Entity 1", entity_type="Organization", tags=[],
    )
    entity1_id = entity1_result.data["id"]
    entity2_result = await create_entity(
        mcp_client, name="Entity 2", entity_type="Individual", tags=[],
    )
    entity2_id = entity2_result.data["id"]
    await create_relationship(
        mcp_client,
        source_entity_id=entity1_id,
        target_entity_id=entity2_id,
        relationship_type="manages",
    )
    outgoing_result = await get_entity_relationships(
        mcp_client, entity_id=entity1_id, direction="outgoing",
    )
    relationships = outgoing_result.data["relationships"]
    assert len(relationships) >= 1
    assert all(r["source_entity_id"] == entity1_id for r in relationships)
    incoming_result = await get_entity_relationships(
        mcp_client, entity_id=entity1_id, direction="incoming",
    )
    incoming_rels = incoming_result.data["relationships"]
    incoming_test_rels = [
        r for r in incoming_rels
        if r["target_entity_id"] == entity1_id and r["source_entity_id"] == entity2_id
    ]
    assert len(incoming_test_rels) == 0


@pytest.mark.e2e
async def test_update_entity_relationship_e2e(mcp_client):
    """Test updating entity relationship"""
    entity1_result = await create_entity(
        mcp_client, name="Entity A", entity_type="Organization", tags=[],
    )
    entity1_id = entity1_result.data["id"]
    entity2_result = await create_entity(
        mcp_client, name="Entity B", entity_type="Individual", tags=[],
    )
    entity2_id = entity2_result.data["id"]
    rel_result = await create_relationship(
        mcp_client,
        source_entity_id=entity1_id,
        target_entity_id=entity2_id,
        relationship_type="partners_with",
        strength=0.5,
    )
    rel_id = rel_result.data["id"]
    update_result = await call_tool(mcp_client, "update_entity_relationship", {
        "relationship_id": rel_id,
        "strength": 0.95,
        "confidence": 0.9,
        "metadata": {"updated": True},
    })
    assert update_result.data["strength"] == 0.95
    assert update_result.data["confidence"] == 0.9
    assert update_result.data["metadata"] == {"updated": True}
    assert update_result.data["relationship_type"] == "partners_with"


@pytest.mark.e2e
async def test_delete_entity_relationship_e2e(mcp_client):
    """Test deleting entity relationship"""
    entity1_result = await create_entity(
        mcp_client, name="Entity X", entity_type="Organization", tags=[],
    )
    entity1_id = entity1_result.data["id"]
    entity2_result = await create_entity(
        mcp_client, name="Entity Y", entity_type="Individual", tags=[],
    )
    entity2_id = entity2_result.data["id"]
    rel_result = await create_relationship(
        mcp_client,
        source_entity_id=entity1_id,
        target_entity_id=entity2_id,
        relationship_type="works_with",
    )
    rel_id = rel_result.data["id"]
    delete_result = await call_tool(mcp_client, "delete_entity_relationship", {
        "relationship_id": rel_id,
    })
    assert delete_result.data is not None
    assert delete_result.data["deleted_id"] == rel_id
    rel_list = await get_entity_relationships(mcp_client, entity_id=entity1_id)
    relationships = rel_list.data["relationships"]
    assert not any(r["id"] == rel_id for r in relationships)
