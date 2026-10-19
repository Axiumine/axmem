"""SQLite end-to-end tests: entity REST routes on missing resources and type filters.

Covers the search entity_type filter and the 404 answers for entities, links and
relationships that do not exist.
"""
from rest_helpers import create_entity, create_memory

MISSING_ID = 99999


async def test_search_filters_entities_by_entity_type(http_client):
    person = await create_entity(http_client, "Zephyr Person", "Individual")
    await create_entity(http_client, "Zephyr Corp", "Organization")

    response = await http_client.post(
        "/api/v1/entities/search", json={"query": "Zephyr", "entity_type": "Individual"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [e["id"] for e in body["entities"]] == [person]
    assert body["total"] == 1


async def test_search_rejects_unknown_entity_type(http_client):
    response = await http_client.post(
        "/api/v1/entities/search", json={"query": "Zephyr", "entity_type": "Robot"},
    )

    assert response.status_code == 400
    assert response.json()["error"].startswith("Invalid entity_type: Robot. Valid values:")


async def test_unlink_reports_404_when_entity_and_memory_were_never_linked(http_client):
    entity_id = await create_entity(http_client, "Unlinked Entity", "Individual")
    memory_id = await create_memory(
        http_client, "Unlinked memory", "content", "context", ["unlinked"], ["t"],
    )

    response = await http_client.delete(f"/api/v1/entities/{entity_id}/memories/{memory_id}")

    assert response.status_code == 404
    assert response.json() == {"error": "Link not found"}


async def test_unlink_reports_404_for_missing_entity(http_client):
    memory_id = await create_memory(
        http_client, "Orphan memory", "content", "context", ["orphan"], ["t"],
    )

    response = await http_client.delete(f"/api/v1/entities/{MISSING_ID}/memories/{memory_id}")

    assert response.status_code == 404
    assert response.json() == {"error": "Link not found"}



async def test_relationships_of_missing_entity_return_404(http_client):
    response = await http_client.get(f"/api/v1/entities/{MISSING_ID}/relationships")

    assert response.status_code == 404
    assert response.json() == {"error": "Entity not found"}


async def test_create_relationship_to_missing_entity_returns_404(http_client):
    source_id = await create_entity(http_client, "Lonely Source", "Individual")

    response = await http_client.post(
        f"/api/v1/entities/{source_id}/relationships",
        json={"target_entity_id": MISSING_ID, "relationship_type": "knows"},
    )

    assert response.status_code == 404
    assert str(MISSING_ID) in response.json()["error"]


async def test_update_missing_relationship_returns_404(http_client):
    response = await http_client.put(
        f"/api/v1/entities/relationships/{MISSING_ID}", json={"strength": 0.5},
    )

    assert response.status_code == 404
    assert response.json() == {"error": "Relationship not found"}


async def test_delete_missing_relationship_returns_404(http_client):
    response = await http_client.delete(f"/api/v1/entities/relationships/{MISSING_ID}")

    assert response.status_code == 404
    assert response.json() == {"error": "Relationship not found"}
