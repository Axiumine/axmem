"""Integration tests for EntityService search (name, type, tags, aka) with in-memory stubs.
"""
from uuid import uuid4

import pytest

from app.models.entity_models import (
    EntityCreate,
    EntityType,
)


@pytest.mark.asyncio
async def test_search_entities_basic(test_entity_service):
    """Test basic entity search by name"""
    user_id = uuid4()

    # Create entities with different names
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Tech Corp", entity_type=EntityType.ORGANIZATION, tags=[]),
    )
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="TechFlow Inc", entity_type=EntityType.ORGANIZATION, tags=[]),
    )
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Sarah Chen", entity_type=EntityType.INDIVIDUAL, tags=[]),
    )

    # Search for "tech"
    results = await test_entity_service.search_entities(user_id, "tech")

    assert len(results) == 2
    assert all("tech" in e.name.lower() for e in results)


@pytest.mark.asyncio
async def test_search_entities_case_insensitive(test_entity_service):
    """Test that entity search is case-insensitive"""
    user_id = uuid4()

    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="UPPERCASE ORG", entity_type=EntityType.ORGANIZATION, tags=[]),
    )

    # Search with lowercase should still find it
    results = await test_entity_service.search_entities(user_id, "uppercase")

    assert len(results) == 1
    assert results[0].name == "UPPERCASE ORG"


@pytest.mark.asyncio
async def test_search_entities_with_type_filter(test_entity_service):
    """Test searching entities filtered by entity type"""
    user_id = uuid4()

    # Create different types
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Server Alpha", entity_type=EntityType.DEVICE, tags=[]),
    )
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Server Beta", entity_type=EntityType.DEVICE, tags=[]),
    )
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Server Team", entity_type=EntityType.TEAM, tags=[]),
    )

    # Search for "server" but only devices
    results = await test_entity_service.search_entities(
        user_id,
        "server",
        entity_type=EntityType.DEVICE,
    )

    assert len(results) == 2
    assert all(e.entity_type == EntityType.DEVICE for e in results)


@pytest.mark.asyncio
async def test_search_entities_with_system_type_filter(test_entity_service):
    """Test searching entities filtered by the System entity type"""
    user_id = uuid4()

    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="payments-service", entity_type=EntityType.SYSTEM, tags=[]),
    )
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="payments-team", entity_type=EntityType.TEAM, tags=[]),
    )

    results = await test_entity_service.search_entities(
        user_id,
        "payments",
        entity_type=EntityType.SYSTEM,
    )

    assert len(results) == 1
    assert results[0].entity_type == EntityType.SYSTEM


@pytest.mark.asyncio
async def test_search_entities_with_tags_filter(test_entity_service):
    """Test searching entities filtered by tags"""
    user_id = uuid4()

    # Create entities with different tags
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Dev Server", entity_type=EntityType.DEVICE, tags=["production"]),
    )
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Test Server", entity_type=EntityType.DEVICE, tags=["staging"]),
    )

    # Search for "server" with production tag
    results = await test_entity_service.search_entities(
        user_id,
        "server",
        tags=["production"],
    )

    assert len(results) == 1
    assert "production" in results[0].tags


@pytest.mark.asyncio
async def test_search_entities_limit(test_entity_service):
    """Test that search respects limit parameter"""
    user_id = uuid4()

    # Create many entities
    for i in range(10):
        await test_entity_service.create_entity(
            user_id,
            EntityCreate(name=f"Test Entity {i}", entity_type=EntityType.ORGANIZATION, tags=[]),
        )

    # Search with limit
    results = await test_entity_service.search_entities(user_id, "test", limit=3)

    assert len(results) == 3


@pytest.mark.asyncio
async def test_search_entities_no_results(test_entity_service):
    """Test search returns empty list when no matches"""
    user_id = uuid4()

    await test_entity_service.create_entity(
        user_id,
        EntityCreate(name="Acme Corp", entity_type=EntityType.ORGANIZATION, tags=[]),
    )

    # Search for non-existent name
    results = await test_entity_service.search_entities(user_id, "nonexistent")

    assert len(results) == 0


@pytest.mark.asyncio
async def test_search_entities_by_aka(test_entity_service):
    """Test searching entities by alternative name"""
    user_id = uuid4()

    # Create entity with aka
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(
            name="Microsoft Corporation",
            entity_type=EntityType.ORGANIZATION,
            tags=["aka-search-test"],
            aka=["MSFT", "Microsoft"],
        ),
    )

    # Search by aka - should find the entity
    results = await test_entity_service.search_entities(user_id, "MSFT")

    assert len(results) >= 1
    matching = [e for e in results if "aka-search-test" in e.tags]
    assert len(matching) == 1
    assert matching[0].name == "Microsoft Corporation"


@pytest.mark.asyncio
async def test_search_entities_by_partial_aka(test_entity_service):
    """Test searching entities by partial match in alternative names"""
    user_id = uuid4()

    # Create entity with aka
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(
            name="John Smith",
            entity_type=EntityType.INDIVIDUAL,
            tags=["partial-aka-test"],
            aka=["Johnny", "J.S."],
        ),
    )

    # Search by partial aka match
    results = await test_entity_service.search_entities(user_id, "john")

    assert len(results) >= 1
    matching = [e for e in results if "partial-aka-test" in e.tags]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_search_entities_name_and_aka(test_entity_service):
    """Test that search finds entities by both name and aka"""
    user_id = uuid4()

    # Create entity found by name
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(
            name="Tech Corp",
            entity_type=EntityType.ORGANIZATION,
            tags=["name-aka-test"],
            aka=["TC"],
        ),
    )

    # Create entity found by aka
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(
            name="Other Inc",
            entity_type=EntityType.ORGANIZATION,
            tags=["name-aka-test"],
            aka=["Tech Alias"],
        ),
    )

    # Search for "tech" - should find both (one by name, one by aka)
    results = await test_entity_service.search_entities(user_id, "tech")

    matching = [e for e in results if "name-aka-test" in e.tags]
    assert len(matching) == 2
    names = {e.name for e in matching}
    assert "Tech Corp" in names
    assert "Other Inc" in names
