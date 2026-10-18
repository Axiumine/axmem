"""E2E tests for the entity-project many-to-many relationship with in-memory SQLite."""
import pytest

from tests.e2e_sqlite.entity_tools_sqlite_helpers import (
    create_entity,
    create_project,
    get_entity,
    list_entities,
    update_entity,
)


@pytest.mark.asyncio
async def test_create_entity_with_multiple_projects_e2e(mcp_client):
    """Test creating entity with multiple project associations"""
    # Create test projects first
    project1_result = await create_project(
        mcp_client,
        name="Test Project 1 for Entity",
        description="First test project",
        project_type="development",
    )
    project1_id = project1_result.data["id"]

    project2_result = await create_project(
        mcp_client,
        name="Test Project 2 for Entity",
        description="Second test project",
        project_type="development",
    )
    project2_id = project2_result.data["id"]

    # Create entity with multiple projects
    result = await create_entity(
        mcp_client,
        name="Multi-Project Entity E2E",
        entity_type="Organization",
        notes="Associated with multiple projects",
        tags=["multi-project-e2e"],
        project_ids=[project1_id, project2_id],
    )

    assert result.data is not None
    assert result.data["id"] is not None
    assert len(result.data["project_ids"]) == 2
    assert project1_id in result.data["project_ids"]
    assert project2_id in result.data["project_ids"]


@pytest.mark.asyncio
async def test_create_entity_with_no_projects_e2e(mcp_client):
    """Test creating entity with no project associations"""
    result = await create_entity(
        mcp_client,
        name="No Project Entity E2E",
        entity_type="Individual",
        notes="No project associations",
        tags=["unassociated-e2e"],
    )

    assert result.data is not None
    assert result.data["id"] is not None
    assert len(result.data["project_ids"]) == 0


@pytest.mark.asyncio
async def test_get_entity_with_project_ids_e2e(mcp_client):
    """Test retrieving entity and verifying project_ids are loaded"""
    # Create project
    project_result = await create_project(
        mcp_client,
        name="Get Entity Test Project",
        description="Project for get entity test",
        project_type="development",
    )
    project_id = project_result.data["id"]

    # Create entity with project
    create_result = await create_entity(
        mcp_client,
        name="Get Test Entity E2E",
        entity_type="Organization",
        tags=["get-test-e2e"],
        project_ids=[project_id],
    )
    entity_id = create_result.data["id"]

    # Get entity and verify project_ids
    get_result = await get_entity(mcp_client, entity_id)

    assert get_result.data is not None
    assert get_result.data["id"] == entity_id
    assert len(get_result.data["project_ids"]) == 1
    assert project_id in get_result.data["project_ids"]


@pytest.mark.asyncio
async def test_list_entities_filter_by_project_ids_e2e(mcp_client):
    """Test filtering entities by project_ids"""
    # Create test projects
    project1_result = await create_project(
        mcp_client,
        name="Filter Test Project 1",
        description="First filter test project",
        project_type="development",
    )
    project1_id = project1_result.data["id"]

    project2_result = await create_project(
        mcp_client,
        name="Filter Test Project 2",
        description="Second filter test project",
        project_type="development",
    )
    project2_id = project2_result.data["id"]

    # Create entities with different project associations
    await create_entity(
        mcp_client,
        name="Project 1 Only Entity E2E",
        entity_type="Organization",
        tags=["proj-filter-e2e"],
        project_ids=[project1_id],
    )

    await create_entity(
        mcp_client,
        name="Project 2 Only Entity E2E",
        entity_type="Organization",
        tags=["proj-filter-e2e"],
        project_ids=[project2_id],
    )

    await create_entity(
        mcp_client,
        name="Both Projects Entity E2E",
        entity_type="Organization",
        tags=["proj-filter-e2e"],
        project_ids=[project1_id, project2_id],
    )

    # Filter by project 1
    list_result = await list_entities(mcp_client, project_ids=[project1_id])

    entities = list_result.data["entities"]
    proj_filter_entities = [e for e in entities if "proj-filter-e2e" in e["tags"]]

    # Should find 2 entities (one with project 1 only, one with both)
    assert len(proj_filter_entities) == 2

    # Filter by project 2
    list_result2 = await list_entities(mcp_client, project_ids=[project2_id])

    entities2 = list_result2.data["entities"]
    proj_filter_entities2 = [e for e in entities2 if "proj-filter-e2e" in e["tags"]]

    # Should find 2 entities (one with project 2 only, one with both)
    assert len(proj_filter_entities2) == 2


@pytest.mark.asyncio
async def test_update_entity_change_projects_e2e(mcp_client):
    """Test updating entity to change project associations"""
    # Create test projects
    project1_result = await create_project(
        mcp_client,
        name="Update Test Project 1",
        description="First update test project",
        project_type="development",
    )
    project1_id = project1_result.data["id"]

    project2_result = await create_project(
        mcp_client,
        name="Update Test Project 2",
        description="Second update test project",
        project_type="development",
    )
    project2_id = project2_result.data["id"]

    project3_result = await create_project(
        mcp_client,
        name="Update Test Project 3",
        description="Third update test project",
        project_type="development",
    )
    project3_id = project3_result.data["id"]

    # Create entity with initial projects
    create_result = await create_entity(
        mcp_client,
        name="Project Update Entity E2E",
        entity_type="Organization",
        notes="Testing project updates",
        tags=["update-test-e2e"],
        project_ids=[project1_id, project2_id],
    )
    entity_id = create_result.data["id"]

    assert len(create_result.data["project_ids"]) == 2

    # Update to change projects (remove project1, keep project2, add project3)
    update_result = await update_entity(
        mcp_client, entity_id=entity_id, project_ids=[project2_id, project3_id],
    )

    assert len(update_result.data["project_ids"]) == 2
    assert project2_id in update_result.data["project_ids"]
    assert project3_id in update_result.data["project_ids"]
    assert project1_id not in update_result.data["project_ids"]


@pytest.mark.asyncio
async def test_update_entity_clear_all_projects_e2e(mcp_client):
    """Test updating entity to clear all project associations"""
    # Create test project
    project_result = await create_project(
        mcp_client,
        name="Clear Test Project",
        description="Project for clear test",
        project_type="development",
    )
    project_id = project_result.data["id"]

    # Create entity with project
    create_result = await create_entity(
        mcp_client,
        name="Clear Projects Entity E2E",
        entity_type="Organization",
        tags=["clear-test-e2e"],
        project_ids=[project_id],
    )
    entity_id = create_result.data["id"]

    assert len(create_result.data["project_ids"]) == 1

    # Update to clear all projects
    update_result = await update_entity(
        mcp_client, entity_id=entity_id, project_ids=[],
    )

    assert len(update_result.data["project_ids"]) == 0
