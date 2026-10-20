"""E2E tests: SQLite returns timezone-aware UTC timestamps and honours UTC offsets.

Uses in-memory SQLite for test isolation.

- Timestamps read back from SQLite must carry a UTC offset, exactly like the value
  returned by the create call and like PostgreSQL `timestamptz`.
"""
from datetime import datetime, timedelta

import pytest

from app.config.settings import settings
from app.models.project_models import ProjectCreate, ProjectType, ProjectUpdate
from app.models.user_models import UserCreate

RESOURCES = {
    "projects": {
        "name": "UTC project",
        "description": "Timestamp check",
        "project_type": "development",
    },
    "memories": {
        "title": "UTC memory",
        "content": "Timestamp check content.",
        "context": "Timestamp check",
        "keywords": ["utc"],
        "tags": ["utc"],
        "importance": 7,
    },
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
    @pytest.mark.parametrize("resource", sorted(RESOURCES))
    async def test_get_matches_create(self, http_client, resource):
        created = await http_client.post(f"/api/v1/{resource}", json=RESOURCES[resource])
        assert created.status_code == 201
        body = created.json()

        fetched = await http_client.get(f"/api/v1/{resource}/{body['id']}")
        assert fetched.status_code == 200
        data = fetched.json()

        for field in ("created_at", "updated_at"):
            read_back = _parse_aware(data[field])
            # Memory creation returns a result summary without timestamps.
            if field in body:
                assert read_back == _parse_aware(body[field])

    @pytest.mark.asyncio
    async def test_project_list_and_update_are_aware(self, http_client):
        created = await http_client.post("/api/v1/projects", json=RESOURCES["projects"])
        project_id = created.json()["id"]

        updated = await http_client.put(f"/api/v1/projects/{project_id}", json={"name": "Renamed"})
        assert updated.status_code == 200
        listed = await http_client.get("/api/v1/projects")
        assert listed.status_code == 200

        _parse_aware(updated.json()["updated_at"])
        for project in listed.json()["projects"]:
            _parse_aware(project["created_at"])
            _parse_aware(project["updated_at"])

    @pytest.mark.asyncio
    async def test_service_results_are_aware(self, app):
        """Same check one layer down, on the model objects the services return."""
        user = await app.user_service.get_or_create_user(
            user=UserCreate(
                external_id=settings.DEFAULT_USER_ID,
                name=settings.DEFAULT_USER_NAME,
                email=settings.DEFAULT_USER_EMAIL,
            ),
        )
        service = app.project_service
        created = await service.create_project(
            user.id,
            ProjectCreate(name="p", description="d", project_type=ProjectType.DEVELOPMENT),
        )
        fetched = await service.get_project(user.id, created.id)
        updated = await service.update_project(user.id, created.id, ProjectUpdate(name="p2"))
        listed = (await service.list_projects(user.id))[0]

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
