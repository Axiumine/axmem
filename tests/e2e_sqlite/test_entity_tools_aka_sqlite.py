"""E2E tests for entity AKA (Also Known As) names with in-memory SQLite."""
import pytest

from tests.e2e_sqlite.entity_tools_sqlite_helpers import (
    create_entity,
    parse_text_payload,
    search_entities,
    update_entity,
)


@pytest.mark.asyncio
async def test_create_entity_with_aka_e2e(mcp_client):
    """Test creating entity with alternative names via MCP tool"""
    result = await create_entity(
        mcp_client,
        name="John Smith E2E",
        entity_type="Individual",
        notes="Person with aliases",
        tags=["aka-e2e-test"],
        aka=["Johnny", "J.S.", "John S."],
    )

    assert result.data is not None
    assert result.data["id"] is not None
    assert result.data["name"] == "John Smith E2E"
    assert result.data["aka"] == ["Johnny", "J.S.", "John S."]
    assert len(result.data["aka"]) == 3


@pytest.mark.asyncio
async def test_update_entity_aka_e2e(mcp_client):
    """Test updating entity's alternative names via MCP tool"""
    # Create entity with initial aka
    create_result = await create_entity(
        mcp_client,
        name="Microsoft E2E",
        entity_type="Organization",
        tags=["update-aka-e2e"],
        aka=["MSFT"],
    )
    entity_id = create_result.data["id"]

    assert create_result.data["aka"] == ["MSFT"]

    # Update aka
    update_result = await update_entity(
        mcp_client, entity_id=entity_id, aka=["MSFT", "Microsoft", "MS"],
    )

    assert update_result.data["aka"] == ["MSFT", "Microsoft", "MS"]
    assert len(update_result.data["aka"]) == 3
    assert update_result.data["name"] == "Microsoft E2E"  # Unchanged


@pytest.mark.asyncio
async def test_search_entities_by_aka_e2e(mcp_client):
    """Test searching entities by alternative name via MCP tool"""
    # Create entity with aka
    await create_entity(
        mcp_client,
        name="Apple Inc E2E",
        entity_type="Organization",
        tags=["search-aka-e2e"],
        aka=["AAPL", "Apple"],
    )

    # Search by aka
    search_result = await search_entities(mcp_client, query="AAPL")

    assert search_result.content is not None
    result_data = parse_text_payload(search_result)
    entities = result_data["entities"]

    # Filter to our test entity
    test_entities = [e for e in entities if "search-aka-e2e" in e["tags"]]
    assert len(test_entities) >= 1
    assert test_entities[0]["name"] == "Apple Inc E2E"


@pytest.mark.asyncio
async def test_search_entities_by_aka_case_insensitive_e2e(mcp_client):
    """Test that entity AKA search is case-insensitive"""
    # Create entity with uppercase aka
    await create_entity(
        mcp_client,
        name="Case Test Entity E2E",
        entity_type="Organization",
        tags=["case-aka-e2e"],
        aka=["UPPERCASE", "MixedCase"],
    )

    # Search with lowercase should find it
    search_result = await search_entities(mcp_client, query="uppercase")

    assert search_result.content is not None
    result_data = parse_text_payload(search_result)
    entities = result_data["entities"]

    test_entities = [e for e in entities if "case-aka-e2e" in e["tags"]]
    assert len(test_entities) >= 1
