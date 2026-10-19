"""E2E tests for Activity REST API endpoints (PostgreSQL).

Uses in-process FastMCP server with real PostgreSQL to test the activity tracking system.
Tests the /api/v1/activity endpoints via ASGI transport (async http_client).

Related modules: ``test_api_activity_stream_e2e`` (SSE stream) and
``test_api_activity_projects_e2e`` / ``test_api_activity_entities_e2e``
(events per resource type).
"""
import pytest

from tests.e2e.activity_api_helpers import create_memory

pytestmark = pytest.mark.asyncio(loop_scope="session")


# Enable activity tracking + read/query event tracking for this test module
SETTINGS_OVERRIDE = {
    "ACTIVITY_ENABLED": True,
    "ACTIVITY_TRACK_READS": True,
}


@pytest.mark.e2e
class TestActivityAPIList:
    """Test GET /api/v1/activity endpoint."""

    async def test_list_activity_empty(self, http_client):
        """GET /api/v1/activity returns empty list initially."""
        response = await http_client.get("/api/v1/activity")
        assert response.status_code == 200
        data = response.json()
        assert data["events"] == []
        assert data["total"] == 0
        assert data["limit"] == 50
        assert data["offset"] == 0

    async def test_list_activity_after_memory_created(self, http_client, wait_for_events):
        """GET /api/v1/activity returns events after memory creation."""
        create_response = await create_memory(
            http_client,
            "Test Memory for Activity",
            "This is test content for activity tracking.",
            "Testing activity API",
            ["test", "activity"],
        )
        assert create_response.status_code == 201

        # Wait for async event processing
        await wait_for_events()

        response = await http_client.get("/api/v1/activity")
        assert response.status_code == 200
        data = response.json()
        assert len(data["events"]) >= 1
        assert data["total"] >= 1

        created_events = [e for e in data["events"] if e["action"] == "created"]
        assert len(created_events) >= 1

    async def test_list_activity_filter_by_entity_type(self, http_client, wait_for_events):
        """GET /api/v1/activity filters by entity_type."""
        await create_memory(
            http_client,
            "Memory for Entity Type Filter",
            "Content for filter test",
            "Filter test",
            ["filter"],
        )

        await wait_for_events()

        response = await http_client.get("/api/v1/activity?entity_type=memory")
        assert response.status_code == 200
        data = response.json()

        for event in data["events"]:
            assert event["entity_type"] == "memory"

    async def test_list_activity_filter_by_action(self, http_client, wait_for_events):
        """GET /api/v1/activity filters by action."""
        create_response = await create_memory(
            http_client,
            "Memory for Action Filter",
            "Content for action filter test",
            "Filter test",
            ["filter"],
        )
        memory_id = create_response.json()["id"]

        await http_client.put(
            f"/api/v1/memories/{memory_id}",
            json={"title": "Updated Title"},
        )

        await wait_for_events()

        response = await http_client.get("/api/v1/activity?action=created")
        assert response.status_code == 200
        data = response.json()

        for event in data["events"]:
            assert event["action"] == "created"


@pytest.mark.e2e
class TestActivityAPIUpdates:
    """Test activity tracking for update operations."""

    async def test_update_generates_event_with_changes(self, http_client, wait_for_events):
        """Memory update generates event with changes diff."""
        create_response = await create_memory(
            http_client,
            "Original Title",
            "Original content",
            "Update test",
            ["update"],
            importance=5,
        )
        memory_id = create_response.json()["id"]

        await http_client.put(
            f"/api/v1/memories/{memory_id}",
            json={"title": "New Title", "importance": 9},
        )

        await wait_for_events()

        response = await http_client.get(
            f"/api/v1/activity?entity_id={memory_id}&action=updated",
        )
        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) >= 1
        updated_event = data["events"][0]
        assert updated_event["action"] == "updated"
        assert updated_event["changes"] is not None

        changes = updated_event["changes"]
        assert "title" in changes
        assert changes["title"]["old"] == "Original Title"
        assert changes["title"]["new"] == "New Title"


@pytest.mark.e2e
class TestActivityAPIDelete:
    """Test activity tracking for delete operations."""

    async def test_delete_generates_event(self, http_client, wait_for_events):
        """Memory deletion generates deleted event."""
        create_response = await create_memory(
            http_client,
            "Memory to Delete",
            "Content to delete",
            "Delete test",
            ["delete"],
        )
        memory_id = create_response.json()["id"]

        await http_client.request(
            "DELETE",
            f"/api/v1/memories/{memory_id}",
            json={"reason": "Test deletion"},
        )

        await wait_for_events()

        response = await http_client.get(
            f"/api/v1/activity?entity_id={memory_id}&action=deleted",
        )
        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) >= 1
        deleted_event = data["events"][0]
        assert deleted_event["action"] == "deleted"
        assert deleted_event["entity_type"] == "memory"


@pytest.mark.e2e
class TestActivityAPIReads:
    """Test activity tracking for read operations."""

    async def test_get_memory_generates_read_event(self, http_client, wait_for_events):
        """GET /api/v1/memories/{id} generates read event when tracking enabled."""
        create_response = await create_memory(
            http_client,
            "Memory for Read Test",
            "Content for read event test",
            "Read test",
            ["read"],
        )
        memory_id = create_response.json()["id"]

        await http_client.get(f"/api/v1/memories/{memory_id}")

        await wait_for_events()

        response = await http_client.get(
            f"/api/v1/activity?entity_id={memory_id}&action=read",
        )
        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) >= 1
        read_event = data["events"][0]
        assert read_event["action"] == "read"
        assert read_event["entity_type"] == "memory"


@pytest.mark.e2e
class TestActivityAPIEntityHistory:
    """Test GET /api/v1/activity/{entity_type}/{entity_id} endpoint."""

    async def test_get_entity_history(self, http_client, wait_for_events):
        """GET entity history returns all events for a specific entity."""
        create_response = await create_memory(
            http_client,
            "Memory for History Test",
            "Content for history test",
            "History test",
            ["history"],
            importance=5,
        )
        memory_id = create_response.json()["id"]

        await http_client.put(
            f"/api/v1/memories/{memory_id}",
            json={"title": "Updated Title", "importance": 8},
        )

        await wait_for_events()

        response = await http_client.get(f"/api/v1/activity/memory/{memory_id}")
        assert response.status_code == 200
        data = response.json()

        actions = [e["action"] for e in data["events"]]
        assert "created" in actions
        assert "updated" in actions

    async def test_get_entity_history_invalid_type(self, http_client):
        """GET entity history returns 400 for invalid entity type."""
        response = await http_client.get("/api/v1/activity/invalid_type/1")
        assert response.status_code == 400
        assert "entity_type" in response.json()["error"].lower()


@pytest.mark.e2e
class TestActivityAPIValidation:
    """Test validation of activity API parameters."""

    async def test_invalid_limit_returns_400(self, http_client):
        """GET /api/v1/activity?limit=0 returns 400."""
        response = await http_client.get("/api/v1/activity?limit=0")
        assert response.status_code == 400
        assert "limit" in response.json()["error"].lower()

    async def test_limit_over_max_returns_400(self, http_client):
        """GET /api/v1/activity?limit=200 returns 400."""
        response = await http_client.get("/api/v1/activity?limit=200")
        assert response.status_code == 400
        assert "limit" in response.json()["error"].lower()

    async def test_invalid_offset_returns_400(self, http_client):
        """GET /api/v1/activity?offset=-1 returns 400."""
        response = await http_client.get("/api/v1/activity?offset=-1")
        assert response.status_code == 400
        assert "offset" in response.json()["error"].lower()

    async def test_invalid_action_returns_400(self, http_client):
        """GET /api/v1/activity?action=invalid returns 400."""
        response = await http_client.get("/api/v1/activity?action=invalid")
        assert response.status_code == 400
        assert "action" in response.json()["error"].lower()

    async def test_invalid_entity_type_returns_400(self, http_client):
        """GET /api/v1/activity?entity_type=invalid returns 400."""
        response = await http_client.get("/api/v1/activity?entity_type=invalid")
        assert response.status_code == 400
        assert "entity_type" in response.json()["error"].lower()

    async def test_invalid_actor_returns_400(self, http_client):
        """GET /api/v1/activity?actor=invalid returns 400."""
        response = await http_client.get("/api/v1/activity?actor=invalid")
        assert response.status_code == 400
        assert "actor" in response.json()["error"].lower()

    async def test_invalid_entity_id_returns_400(self, http_client):
        """GET /api/v1/activity?entity_id=abc returns 400."""
        response = await http_client.get("/api/v1/activity?entity_id=abc")
        assert response.status_code == 400
        assert "entity_id" in response.json()["error"].lower()
