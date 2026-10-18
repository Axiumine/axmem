"""E2E tests for Activity events of entities, links and relationships (PostgreSQL).

Split out of ``test_api_activity``; each test performs a REST write, waits for the
event bus and checks the recorded activity event.
"""
import pytest

from tests.e2e.activity_api_helpers import (
    create_entity,
    create_memory,
    create_relationship,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


# Enable activity tracking + read/query event tracking for this test module
SETTINGS_OVERRIDE = {
    "ACTIVITY_ENABLED": True,
    "ACTIVITY_TRACK_READS": True,
}


async def _create_related_entities(http_client, person_name, company_name):
    """Create a person and a company; return (person_id, company_id)."""
    person = await create_entity(http_client, person_name)
    company = await create_entity(http_client, company_name, "Organization")
    return person.json()["id"], company.json()["id"]


@pytest.mark.e2e
class TestActivityAPIEntity:
    """Test activity tracking for Entity operations."""

    async def test_entity_created_event(self, http_client, wait_for_events):
        """POST /api/v1/entities generates created event."""
        create_response = await create_entity(
            http_client,
            "Test Entity for Activity",
            notes="Testing activity tracking",
        )
        assert create_response.status_code == 201
        entity_id = create_response.json()["id"]

        await wait_for_events()

        response = await http_client.get(
            f"/api/v1/activity?entity_type=entity&entity_id={entity_id}&action=created",
        )
        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) >= 1
        event = data["events"][0]
        assert event["entity_type"] == "entity"
        assert event["action"] == "created"

    async def test_entity_deleted_event(self, http_client, wait_for_events):
        """DELETE /api/v1/entities/{id} generates deleted event."""
        create_response = await create_entity(http_client, "Entity to Delete")
        entity_id = create_response.json()["id"]

        await http_client.delete(f"/api/v1/entities/{entity_id}")

        await wait_for_events()

        response = await http_client.get(
            f"/api/v1/activity?entity_type=entity&entity_id={entity_id}&action=deleted",
        )
        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) >= 1
        event = data["events"][0]
        assert event["entity_type"] == "entity"
        assert event["action"] == "deleted"

    async def test_entity_memory_link_created_event(self, http_client, wait_for_events):
        """POST /api/v1/entities/{id}/memories generates entity_memory_link created event."""
        memory_response = await create_memory(
            http_client,
            "Memory for Link Test",
            "Content for entity-memory link test",
            "Link test",
            ["link"],
        )
        memory_id = memory_response.json()["id"]

        entity_response = await create_entity(http_client, "Entity for Link Test")
        entity_id = entity_response.json()["id"]

        await http_client.post(
            f"/api/v1/entities/{entity_id}/memories",
            json={"memory_id": memory_id},
        )

        await wait_for_events()

        response = await http_client.get(
            "/api/v1/activity?entity_type=entity_memory_link&action=created",
        )
        assert response.status_code == 200
        data = response.json()

        link_events = [
            e for e in data["events"]
            if e["snapshot"].get("entity_id") == entity_id
            and e["snapshot"].get("memory_id") == memory_id
        ]
        assert len(link_events) >= 1
        event = link_events[0]
        assert event["entity_type"] == "entity_memory_link"
        assert event["action"] == "created"

    async def test_entity_relationship_created_event(self, http_client, wait_for_events):
        """POST /api/v1/entities/{id}/relationships generates entity_relationship created event."""
        entity1_id, entity2_id = await _create_related_entities(
            http_client, "Person for Relationship", "Company for Relationship",
        )

        rel_response = await create_relationship(http_client, entity1_id, entity2_id)
        assert rel_response.status_code == 201
        relationship_id = rel_response.json()["id"]

        await wait_for_events()

        response = await http_client.get(
            f"/api/v1/activity?entity_type=entity_relationship&entity_id={relationship_id}&action=created",
        )
        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) >= 1
        event = data["events"][0]
        assert event["entity_type"] == "entity_relationship"
        assert event["action"] == "created"

    async def test_entity_relationship_deleted_event(self, http_client, wait_for_events):
        """DELETE /api/v1/entities/relationships/{id} generates deleted event."""
        entity1_id, entity2_id = await _create_related_entities(
            http_client, "Person to Delete Relationship", "Company to Delete Relationship",
        )

        rel_response = await create_relationship(http_client, entity1_id, entity2_id)
        relationship_id = rel_response.json()["id"]

        await http_client.delete(f"/api/v1/entities/relationships/{relationship_id}")

        await wait_for_events()

        response = await http_client.get(
            f"/api/v1/activity?entity_type=entity_relationship&entity_id={relationship_id}&action=deleted",
        )
        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) >= 1
        event = data["events"][0]
        assert event["entity_type"] == "entity_relationship"
        assert event["action"] == "deleted"
