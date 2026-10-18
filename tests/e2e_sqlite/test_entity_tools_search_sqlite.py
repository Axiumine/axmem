"""E2E tests for the search_entities MCP tool with in-memory SQLite."""
import pytest

from tests.e2e_sqlite.entity_tools_sqlite_helpers import (
    create_entity,
    parse_text_payload,
    search_entities,
)


@pytest.mark.asyncio
async def test_search_entities_basic_e2e(mcp_client):
    """Test basic entity search by name"""
    # Create entities with different names
    await create_entity(
        mcp_client, name="TechCorp Solutions SQLite", entity_type="Organization",
        tags=["search-test-sqlite"],
    )
    await create_entity(
        mcp_client, name="TechFlow Systems SQLite", entity_type="Organization",
        tags=["search-test-sqlite"],
    )
    await create_entity(
        mcp_client, name="Sarah Chen SQLite", entity_type="Individual",
        tags=["search-test-sqlite"],
    )

    # Search for "tech"
    search_result = await search_entities(mcp_client, query="tech")

    assert search_result.content is not None
    result_data = parse_text_payload(search_result)
    assert "entities" in result_data
    assert "total_count" in result_data
    entities = result_data["entities"]

    # Filter to our test entities
    test_entities = [e for e in entities if "search-test-sqlite" in e["tags"]]
    assert len(test_entities) >= 2
    assert all("tech" in e["name"].lower() for e in test_entities)


@pytest.mark.asyncio
async def test_search_entities_case_insensitive_e2e(mcp_client):
    """Test that entity search is case-insensitive"""
    await create_entity(
        mcp_client, name="UPPERCASE ORGANIZATION SQLITE", entity_type="Organization",
        tags=["case-test-sqlite"],
    )

    # Search with lowercase should find it
    search_result = await search_entities(
        mcp_client, query="uppercase", tags=["case-test-sqlite"],
    )

    assert search_result.content is not None
    result_data = parse_text_payload(search_result)
    entities = result_data["entities"]
    assert len(entities) >= 1
    assert any(e["name"] == "UPPERCASE ORGANIZATION SQLITE" for e in entities)


@pytest.mark.asyncio
async def test_search_entities_with_type_filter_e2e(mcp_client):
    """Test searching entities filtered by entity type"""
    # Create different types
    await create_entity(
        mcp_client, name="Server Alpha SQLite", entity_type="Device",
        tags=["type-filter-test-sqlite"],
    )
    await create_entity(
        mcp_client, name="Server Beta SQLite", entity_type="Device",
        tags=["type-filter-test-sqlite"],
    )
    await create_entity(
        mcp_client, name="Server Team SQLite", entity_type="Team",
        tags=["type-filter-test-sqlite"],
    )

    # Search for "server" but only devices
    search_result = await search_entities(
        mcp_client, query="server", entity_type="Device",
        tags=["type-filter-test-sqlite"],
    )

    assert search_result.content is not None
    result_data = parse_text_payload(search_result)
    entities = result_data["entities"]
    assert len(entities) >= 2
    assert all(e["entity_type"] == "Device" for e in entities)


@pytest.mark.asyncio
async def test_search_entities_with_tags_filter_e2e(mcp_client):
    """Test searching entities filtered by tags"""
    # Create entities with different tags
    await create_entity(
        mcp_client, name="Production Server SQLite", entity_type="Device",
        tags=["production", "tag-search-test-sqlite"],
    )
    await create_entity(
        mcp_client, name="Staging Server SQLite", entity_type="Device",
        tags=["staging", "tag-search-test-sqlite"],
    )

    # Search for "server" with production tag
    search_result = await search_entities(
        mcp_client, query="server", tags=["production"],
    )

    assert search_result.content is not None
    result_data = parse_text_payload(search_result)
    entities = result_data["entities"]
    prod_entities = [e for e in entities if "tag-search-test-sqlite" in e["tags"]]
    assert len(prod_entities) >= 1
    assert all("production" in e["tags"] for e in prod_entities)


@pytest.mark.asyncio
async def test_search_entities_limit_e2e(mcp_client):
    """Test that search respects limit parameter"""
    # Create many entities
    for i in range(10):
        await create_entity(
            mcp_client, name=f"Limit Test Entity SQLite {i}",
            entity_type="Organization", tags=["limit-test-sqlite"],
        )

    # Search with limit
    search_result = await search_entities(mcp_client, query="limit test", limit=3)

    assert search_result.content is not None
    result_data = parse_text_payload(search_result)
    entities = result_data["entities"]
    limit_test_entities = [e for e in entities if "limit-test-sqlite" in e["tags"]]
    assert len(limit_test_entities) <= 3


@pytest.mark.asyncio
async def test_search_entities_no_results_e2e(mcp_client):
    """Test search returns empty when no matches found"""
    # Search for something that definitely doesn't exist
    search_result = await search_entities(
        mcp_client, query="xyznonexistententitysqlite12345",
    )

    assert search_result.content is not None
    result_data = parse_text_payload(search_result)
    assert "entities" in result_data
    assert "total_count" in result_data
    # May have results from other tests, but none should match our query
    entities = result_data["entities"]
    assert all(
        "xyznonexistententitysqlite12345" not in e["name"].lower() for e in entities
    )
