"""Integration tests for activity event emission by the project service.

Tests verify that services emit the correct events when:
- CRUD operations occur (CREATED, UPDATED, DELETED)
- Read operations occur when ACTIVITY_TRACK_READS is enabled (READ, QUERIED)
"""
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.models.activity_models import ActionType, EntityType
from app.models.project_models import ProjectCreate, ProjectType, ProjectUpdate

# ============================================================================
# Project Service Activity Event Tests
# ============================================================================


@pytest.mark.asyncio
async def test_project_create_emits_event(test_project_service_with_event_bus):
    """create_project() emits CREATED event."""
    service, event_bus = test_project_service_with_event_bus
    user_id = uuid4()

    project_data = ProjectCreate(
        name="Test Project",
        description="Test description",
        project_type=ProjectType.DEVELOPMENT,
    )
    project = await service.create_project(user_id, project_data)

    assert len(event_bus.collected_events) == 1
    event = event_bus.collected_events[0]
    assert event.entity_type == EntityType.PROJECT
    assert event.entity_id == project.id
    assert event.action == ActionType.CREATED
    assert event.snapshot["name"] == "Test Project"


@pytest.mark.asyncio
async def test_project_update_emits_event_with_changes(test_project_service_with_event_bus):
    """update_project() emits UPDATED event with changes dict."""
    service, event_bus = test_project_service_with_event_bus
    user_id = uuid4()

    # Create project
    project_data = ProjectCreate(
        name="Original Name",
        description="Original description",
        project_type=ProjectType.DEVELOPMENT,
    )
    project = await service.create_project(user_id, project_data)
    event_bus.collected_events.clear()

    # Update project
    update_data = ProjectUpdate(name="Updated Name")
    await service.update_project(user_id, project.id, update_data)

    assert len(event_bus.collected_events) == 1
    event = event_bus.collected_events[0]
    assert event.entity_type == EntityType.PROJECT
    assert event.action == ActionType.UPDATED
    assert event.changes is not None
    assert "name" in event.changes
    assert event.changes["name"]["old"] == "Original Name"
    assert event.changes["name"]["new"] == "Updated Name"


@pytest.mark.asyncio
async def test_project_delete_emits_event(test_project_service_with_event_bus):
    """delete_project() emits DELETED event with pre-deletion snapshot."""
    service, event_bus = test_project_service_with_event_bus
    user_id = uuid4()

    # Create project
    project_data = ProjectCreate(
        name="To Delete",
        description="Will be deleted",
        project_type=ProjectType.DEVELOPMENT,
    )
    project = await service.create_project(user_id, project_data)
    event_bus.collected_events.clear()

    # Delete project
    await service.delete_project(user_id, project.id)

    assert len(event_bus.collected_events) == 1
    event = event_bus.collected_events[0]
    assert event.entity_type == EntityType.PROJECT
    assert event.action == ActionType.DELETED
    assert event.snapshot["name"] == "To Delete"


@pytest.mark.asyncio
async def test_project_read_emits_event_when_tracking_enabled(test_project_service_with_event_bus):
    """get_project() emits READ event when ACTIVITY_TRACK_READS=True."""
    service, event_bus = test_project_service_with_event_bus
    user_id = uuid4()

    # Create project
    project_data = ProjectCreate(
        name="Test Project",
        description="Test",
        project_type=ProjectType.DEVELOPMENT,
    )
    project = await service.create_project(user_id, project_data)
    event_bus.collected_events.clear()

    # Read with tracking enabled
    with patch.object(settings, "ACTIVITY_TRACK_READS", True):
        await service.get_project(user_id, project.id)

    assert len(event_bus.collected_events) == 1
    event = event_bus.collected_events[0]
    assert event.entity_type == EntityType.PROJECT
    assert event.action == ActionType.READ


@pytest.mark.asyncio
async def test_project_list_emits_queried_event(test_project_service_with_event_bus):
    """list_projects() emits QUERIED event when ACTIVITY_TRACK_READS=True."""
    service, event_bus = test_project_service_with_event_bus
    user_id = uuid4()

    # Create project
    project_data = ProjectCreate(
        name="Test Project",
        description="Test",
        project_type=ProjectType.DEVELOPMENT,
    )
    await service.create_project(user_id, project_data)
    event_bus.collected_events.clear()

    # List with tracking enabled
    with patch.object(settings, "ACTIVITY_TRACK_READS", True):
        await service.list_projects(user_id)

    assert len(event_bus.collected_events) == 1
    event = event_bus.collected_events[0]
    assert event.entity_type == EntityType.PROJECT
    assert event.action == ActionType.QUERIED
    assert event.entity_id == 0  # Query spans multiple
