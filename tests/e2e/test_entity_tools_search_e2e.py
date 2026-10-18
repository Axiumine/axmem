"""E2E tests for the search_entities MCP tool with real PostgreSQL database"""
import pytest

from tests.e2e.entity_tools_e2e_helpers import create_entity, search_entities

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.e2e
async def test_search_entities_basic_e2e(mcp_client):
    """Test basic entity search by name"""
    # Create entities with different names
    await create_entity(
        mcp_client, name="TechCorp Solutions", entity_type="Organization",
        tags=["search-test"],
    )
    await create_entity(
        mcp_client, name="TechFlow Systems", entity_type="Organization",
        tags=["search-test"],
    )
    await create_entity(
        mcp_client, name="Sarah Chen", entity_type="Individual",
        tags=["search-test"],
    )

    # Search for "tech"
    search_result = await search_entities(mcp_client, query="tech")

    assert search_result.data is not None
    assert "entities" in search_result.data
    assert "total_count" in search_result.data
    entities = search_result.data["entities"]

    # Filter to our test entities
    test_entities = [e for e in entities if "search-test" in e["tags"]]
    assert len(test_entities) >= 2
    assert all("tech" in e["name"].lower() for e in test_entities)


@pytest.mark.e2e
async def test_search_entities_case_insensitive_e2e(mcp_client):
    """Test that entity search is case-insensitive"""
    await create_entity(
        mcp_client, name="UPPERCASE ORGANIZATION", entity_type="Organization",
        tags=["case-test"],
    )

    # Search with lowercase should find it
    search_result = await search_entities(
        mcp_client, query="uppercase", tags=["case-test"],
    )

    assert search_result.data is not None
    entities = search_result.data["entities"]
    assert len(entities) >= 1
    assert any(e["name"] == "UPPERCASE ORGANIZATION" for e in entities)


@pytest.mark.e2e
async def test_search_entities_with_type_filter_e2e(mcp_client):
    """Test searching entities filtered by entity type"""
    # Create different types
    await create_entity(
        mcp_client, name="Server Alpha", entity_type="Device",
        tags=["type-filter-test"],
    )
    await create_entity(
        mcp_client, name="Server Beta", entity_type="Device",
        tags=["type-filter-test"],
    )
    await create_entity(
        mcp_client, name="Server Team", entity_type="Team",
        tags=["type-filter-test"],
    )

    # Search for "server" but only devices
    search_result = await search_entities(
        mcp_client, query="server", entity_type="Device", tags=["type-filter-test"],
    )

    assert search_result.data is not None
    entities = search_result.data["entities"]
    assert len(entities) >= 2
    assert all(e["entity_type"] == "Device" for e in entities)


@pytest.mark.e2e
async def test_search_entities_with_tags_filter_e2e(mcp_client):
    """Test searching entities filtered by tags"""
    # Create entities with different tags
    await create_entity(
        mcp_client, name="Production Server", entity_type="Device",
        tags=["production", "tag-search-test"],
    )
    await create_entity(
        mcp_client, name="Staging Server", entity_type="Device",
        tags=["staging", "tag-search-test"],
    )

    # Search for "server" with production tag
    search_result = await search_entities(
        mcp_client, query="server", tags=["production"],
    )

    assert search_result.data is not None
    entities = search_result.data["entities"]
    prod_entities = [e for e in entities if "tag-search-test" in e["tags"]]
    assert len(prod_entities) >= 1
    assert all("production" in e["tags"] for e in prod_entities)


@pytest.mark.e2e
async def test_search_entities_limit_e2e(mcp_client):
    """Test that search respects limit parameter"""
    # Create many entities
    for i in range(10):
        await create_entity(
            mcp_client, name=f"Limit Test Entity {i}", entity_type="Organization",
            tags=["limit-test"],
        )

    # Search with limit
    search_result = await search_entities(mcp_client, query="limit test", limit=3)

    assert search_result.data is not None
    entities = search_result.data["entities"]
    limit_test_entities = [e for e in entities if "limit-test" in e["tags"]]
    assert len(limit_test_entities) <= 3


@pytest.mark.e2e
async def test_search_entities_no_results_e2e(mcp_client):
    """Test search returns empty when no matches found"""
    # Search for something that definitely doesn't exist
    search_result = await search_entities(
        mcp_client, query="xyznonexistententity12345",
    )

    assert search_result.data is not None
    assert "entities" in search_result.data
    assert "total_count" in search_result.data
    assert search_result.data["entities"] == []
    assert search_result.data["total_count"] == 0
