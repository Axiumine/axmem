"""E2E tests: SQLite returns timezone-aware UTC timestamps and honours UTC offsets.

Uses in-memory SQLite for test isolation.

- Timestamps read back from SQLite must carry a UTC offset, exactly like the value
  returned by the create call and like PostgreSQL `timestamptz`.
"""
from datetime import datetime, timedelta

import pytest

from app.models.memory_models import MemoryCreate, MemoryUpdate

MEMORY = {
    "title": "UTC memory",
    "content": "Timestamp check content.",
    "context": "Timestamp check",
    "keywords": ["utc"],
    "tags": ["utc"],
    "importance": 7,
}


@pytest.fixture
async def app(http_client, sqlite_app):
    """The FastMCP app with its lifespan running, so the services are attached."""
    return sqlite_app


def _parse_aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, f"timestamp has no UTC offset: {value}"
    assert parsed.utcoffset() == timedelta(0), value
    return parsed


class TestTimestampsAreAwareUtc:
    """Every read path returns the same aware UTC value that create returned."""

    @pytest.mark.asyncio
    async def test_get_matches_create(self, http_client):
        created = await http_client.post("/api/v1/memories", json=MEMORY)
        assert created.status_code == 201
        body = created.json()

        fetched = await http_client.get(f"/api/v1/memories/{body['id']}")
        assert fetched.status_code == 200
        data = fetched.json()

        for field in ("created_at", "updated_at"):
            read_back = _parse_aware(data[field])
            # Memory creation returns a result summary without timestamps.
            if field in body:
                assert read_back == _parse_aware(body[field])

    @pytest.mark.asyncio
    async def test_memory_list_and_update_are_aware(self, http_client):
        created = await http_client.post("/api/v1/memories", json=MEMORY)
        memory_id = created.json()["id"]

        updated = await http_client.put(f"/api/v1/memories/{memory_id}", json={"title": "Renamed"})
        assert updated.status_code == 200
        listed = await http_client.get("/api/v1/memories")
        assert listed.status_code == 200

        _parse_aware(updated.json()["updated_at"])
        for memory in listed.json()["memories"]:
            _parse_aware(memory["created_at"])
            _parse_aware(memory["updated_at"])

    @pytest.mark.asyncio
    async def test_service_results_are_aware(self, app):
        """Same check one layer down, on the model objects the services return."""
        tenant = await app.tenant_service.get_or_create_tenant("utc-tenant")
        service = app.memory_service
        created, _ = await service.create_memory(tenant.id, MemoryCreate(**MEMORY))
        fetched = await service.get_memory(tenant.id, created.id)
        updated = await service.update_memory(tenant.id, created.id, MemoryUpdate(title="p2"))
        listed = (await service.list_memories(tenant.id))[0][0]

        for value in (
            created.created_at,
            fetched.created_at,
            fetched.updated_at,
            updated.updated_at,
            listed.updated_at,
        ):
            assert value.utcoffset() == timedelta(0)
        assert fetched.created_at == created.created_at
        assert updated.model_dump(mode="json")["updated_at"].endswith(("Z", "+00:00"))
