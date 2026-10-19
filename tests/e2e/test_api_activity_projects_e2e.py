"""E2E tests for Activity events of projects (PostgreSQL).

Split out of ``test_api_activity``; each test performs a REST write, waits for the
event bus and checks the recorded activity event.
"""
import pytest

from tests.e2e.activity_api_helpers import create_project

pytestmark = pytest.mark.asyncio(loop_scope="session")


# Enable activity tracking + read/query event tracking for this test module
SETTINGS_OVERRIDE = {
    "ACTIVITY_ENABLED": True,
    "ACTIVITY_TRACK_READS": True,
}


@pytest.mark.e2e
class TestActivityAPIProject:
    """Test activity tracking for Project operations."""

    async def test_project_created_event(self, http_client, wait_for_events):
        """POST /api/v1/projects generates created event."""
        create_response = await create_project(
            http_client,
            "Test Project for Activity",
            "Testing activity tracking for projects",
        )
        assert create_response.status_code == 201
        project_id = create_response.json()["id"]

        await wait_for_events()

        response = await http_client.get(
            f"/api/v1/activity?entity_type=project&entity_id={project_id}&action=created",
        )
        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) >= 1
        event = data["events"][0]
        assert event["entity_type"] == "project"
        assert event["action"] == "created"

    async def test_project_updated_event(self, http_client, wait_for_events):
        """PUT /api/v1/projects/{id} generates updated event with changes."""
        create_response = await create_project(
            http_client, "Original Project Name", "Original description",
        )
        project_id = create_response.json()["id"]

        await http_client.put(
            f"/api/v1/projects/{project_id}",
            json={"name": "Updated Project Name"},
        )

        await wait_for_events()

        response = await http_client.get(
            f"/api/v1/activity?entity_type=project&entity_id={project_id}&action=updated",
        )
        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) >= 1
        event = data["events"][0]
        assert event["action"] == "updated"
        assert event["changes"] is not None
        assert "name" in event["changes"]

    async def test_project_deleted_event(self, http_client, wait_for_events):
        """DELETE /api/v1/projects/{id} generates deleted event."""
        create_response = await create_project(
            http_client, "Project to Delete", "Will be deleted",
        )
        project_id = create_response.json()["id"]

        await http_client.delete(f"/api/v1/projects/{project_id}")

        await wait_for_events()

        response = await http_client.get(
            f"/api/v1/activity?entity_type=project&entity_id={project_id}&action=deleted",
        )
        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) >= 1
        event = data["events"][0]
        assert event["entity_type"] == "project"
        assert event["action"] == "deleted"
