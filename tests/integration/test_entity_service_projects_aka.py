"""Integration tests for EntityService project scoping and aka (alias) handling with in-memory stubs.
"""
from uuid import uuid4

import pytest

from app.models.entity_models import (
    EntityCreate,
    EntityType,
    EntityUpdate,
)


@pytest.mark.asyncio
async def test_create_entity_with_multiple_projects(test_entity_service):
    """Test creating entity with multiple project associations"""
    user_id = uuid4()

    # Create entity with multiple projects
    entity_data = EntityCreate(
        name="Multi-Project Entity",
        entity_type=EntityType.ORGANIZATION,
        notes="Associated with multiple projects",
        tags=["multi-project"],
        project_ids=[1, 2, 3],
    )

    entity = await test_entity_service.create_entity(user_id, entity_data)

    assert entity.id is not None
    assert len(entity.project_ids) == 3
    assert 1 in entity.project_ids
    assert 2 in entity.project_ids
    assert 3 in entity.project_ids


@pytest.mark.asyncio
async def test_create_entity_with_no_projects(test_entity_service):
    """Test creating entity with no project associations"""
    user_id = uuid4()

    # Create entity without projects (None)
    entity_data = EntityCreate(
        name="No Project Entity",
        entity_type=EntityType.INDIVIDUAL,
        notes="No project associations",
        tags=["unassociated"],
        project_ids=None,
    )

    entity = await test_entity_service.create_entity(user_id, entity_data)

    assert entity.id is not None
    assert len(entity.project_ids) == 0


@pytest.mark.asyncio
async def test_get_entity_with_project_ids(test_entity_service):
    """Test retrieving entity and verifying project_ids are loaded"""
    user_id = uuid4()

    # Create entity with projects
    entity_data = EntityCreate(
        name="Test Entity",
        entity_type=EntityType.ORGANIZATION,
        tags=["test"],
        project_ids=[10, 20],
    )
    created = await test_entity_service.create_entity(user_id, entity_data)

    # Get entity and verify project_ids
    retrieved = await test_entity_service.get_entity(user_id, created.id)

    assert retrieved.id == created.id
    assert len(retrieved.project_ids) == 2
    assert 10 in retrieved.project_ids
    assert 20 in retrieved.project_ids


@pytest.mark.asyncio
async def test_list_entities_filter_by_project_ids(test_entity_service):
    """Test filtering entities by project_ids"""
    user_id = uuid4()

    # Create entities with different project associations
    await test_entity_service.create_entity(
        user_id,
        EntityCreate(
            name="Project 1 Entity",
            entity_type=EntityType.ORGANIZATION,
            tags=["proj-filter"],
            project_ids=[100],
        ),
    )

    await test_entity_service.create_entity(
        user_id,
        EntityCreate(
            name="Project 2 Entity",
            entity_type=EntityType.ORGANIZATION,
            tags=["proj-filter"],
            project_ids=[200],
        ),
    )

    await test_entity_service.create_entity(
        user_id,
        EntityCreate(
            name="Multi Project Entity",
            entity_type=EntityType.ORGANIZATION,
            tags=["proj-filter"],
            project_ids=[100, 200],
        ),
    )

    # Filter by project 100
    entities_in_proj100, total_100 = await test_entity_service.list_entities(
        user_id,
        project_ids=[100],
    )

    # Should find 2 entities (one with project 100, one with both 100 and 200)
    proj_filter_entities = [e for e in entities_in_proj100 if "proj-filter" in e.tags]
    assert len(proj_filter_entities) == 2

    # Filter by project 200
    entities_in_proj200, total_200 = await test_entity_service.list_entities(
        user_id,
        project_ids=[200],
    )

    proj_filter_entities_200 = [e for e in entities_in_proj200 if "proj-filter" in e.tags]
    assert len(proj_filter_entities_200) == 2


@pytest.mark.asyncio
async def test_update_entity_change_projects(test_entity_service):
    """Test updating entity to change project associations"""
    user_id = uuid4()

    # Create entity with initial projects
    entity_data = EntityCreate(
        name="Project Update Entity",
        entity_type=EntityType.ORGANIZATION,
        notes="Testing project updates",
        tags=["update-test"],
        project_ids=[1, 2],
    )
    created = await test_entity_service.create_entity(user_id, entity_data)

    assert len(created.project_ids) == 2

    # Update to change projects (remove 1, keep 2, add 3)
    update_data = EntityUpdate(project_ids=[2, 3])
    updated = await test_entity_service.update_entity(user_id, created.id, update_data)

    assert len(updated.project_ids) == 2
    assert 2 in updated.project_ids
    assert 3 in updated.project_ids
    assert 1 not in updated.project_ids


@pytest.mark.asyncio
async def test_update_entity_clear_all_projects(test_entity_service):
    """Test updating entity to clear all project associations"""
    user_id = uuid4()

    # Create entity with projects
    entity_data = EntityCreate(
        name="Clear Projects Entity",
        entity_type=EntityType.ORGANIZATION,
        tags=["clear-test"],
        project_ids=[1, 2, 3],
    )
    created = await test_entity_service.create_entity(user_id, entity_data)

    assert len(created.project_ids) == 3

    # Update to clear all projects
    update_data = EntityUpdate(project_ids=[])
    updated = await test_entity_service.update_entity(user_id, created.id, update_data)

    assert len(updated.project_ids) == 0


@pytest.mark.asyncio
async def test_create_entity_with_aka(test_entity_service):
    """Test creating entity with alternative names"""
    user_id = uuid4()

    entity_data = EntityCreate(
        name="John Smith",
        entity_type=EntityType.INDIVIDUAL,
        notes="A person with multiple nicknames",
        tags=["employee"],
        aka=["Johnny", "J.S.", "John S."],
    )

    entity = await test_entity_service.create_entity(user_id, entity_data)

    assert entity.id is not None
    assert entity.name == "John Smith"
    assert entity.aka == ["Johnny", "J.S.", "John S."]
    assert len(entity.aka) == 3


@pytest.mark.asyncio
async def test_create_entity_without_aka(test_entity_service):
    """Test that entity without aka has empty list"""
    user_id = uuid4()

    entity_data = EntityCreate(
        name="No Aliases Entity",
        entity_type=EntityType.ORGANIZATION,
        tags=[],
    )

    entity = await test_entity_service.create_entity(user_id, entity_data)

    assert entity.id is not None
    assert entity.aka == []


@pytest.mark.asyncio
async def test_update_entity_aka(test_entity_service):
    """Test updating entity's alternative names"""
    user_id = uuid4()

    # Create with initial aka
    entity_data = EntityCreate(
        name="Microsoft Corporation",
        entity_type=EntityType.ORGANIZATION,
        tags=["tech"],
        aka=["MSFT"],
    )
    created = await test_entity_service.create_entity(user_id, entity_data)

    assert created.aka == ["MSFT"]

    # Update aka
    update_data = EntityUpdate(aka=["MSFT", "Microsoft", "MS"])
    updated = await test_entity_service.update_entity(user_id, created.id, update_data)

    assert updated.aka == ["MSFT", "Microsoft", "MS"]
    assert len(updated.aka) == 3
    assert updated.name == "Microsoft Corporation"  # Unchanged


@pytest.mark.asyncio
async def test_update_entity_clear_aka(test_entity_service):
    """Test clearing all alternative names"""
    user_id = uuid4()

    # Create with aka
    entity_data = EntityCreate(
        name="Test Entity",
        entity_type=EntityType.ORGANIZATION,
        tags=[],
        aka=["Alias1", "Alias2"],
    )
    created = await test_entity_service.create_entity(user_id, entity_data)

    assert len(created.aka) == 2

    # Clear aka
    update_data = EntityUpdate(aka=[])
    updated = await test_entity_service.update_entity(user_id, created.id, update_data)

    assert updated.aka == []
