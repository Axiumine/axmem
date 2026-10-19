"""ActivityService on in-memory SQLite: ownerless events and lazy retention cleanup.

Events are persisted through the event-bus handler and read back through ``get_activity``,
which first removes the events that fell out of ``ACTIVITY_RETENTION_DAYS`` (and must keep
answering when that cleanup fails).
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.config.settings import settings
from app.models.activity_models import ActionType, ActivityEvent, EntityType
from app.models.user_models import UserCreate
from app.repositories.sqlite.activity_repository import SqliteActivityRepository
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from app.repositories.sqlite.user_repository import SqliteUserRepository
from app.services.activity_service import ActivityService


@pytest.fixture
async def activity(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE", "SQLite")
    monkeypatch.setattr(settings, "SQLITE_MEMORY", True)
    db = SqliteDatabaseAdapter()
    await db.init_db()
    try:
        user = await SqliteUserRepository(db_adapter=db).create_user(
            UserCreate(external_id="activity-user", name="U", email="u@example.com"),
        )
        repo = SqliteActivityRepository(db_adapter=db)
        yield ActivityService(repo), repo, user
    finally:
        await db.dispose()


def _event(user_id, entity_id, created_at=None):
    fields = {"created_at": created_at} if created_at else {}
    return ActivityEvent(
        entity_type=EntityType.MEMORY,
        entity_id=entity_id,
        action=ActionType.CREATED,
        snapshot={"id": entity_id},
        user_id=user_id,
        **fields,
    )


async def test_event_without_a_user_is_dropped_with_a_warning(activity, caplog):
    service, _, user = activity
    caplog.set_level(logging.WARNING, logger="app.services.activity_service")

    await service.handle_event(_event(user_id=None, entity_id=1))

    assert "Received event without user_id: memory.created" in [r.getMessage() for r in caplog.records]
    assert (await service.get_activity(user.id)).total == 0


async def test_expired_events_are_removed_when_activity_is_read(activity, monkeypatch, caplog):
    service, _, user = activity
    monkeypatch.setattr(settings, "ACTIVITY_RETENTION_DAYS", 1)
    await service.handle_event(_event(str(user.id), 1, created_at=datetime.now(UTC) - timedelta(days=3)))
    await service.handle_event(_event(str(user.id), 2))
    caplog.set_level(logging.INFO, logger="app.services.activity_service")

    response = await service.get_activity(user.id)

    assert [e.entity_id for e in response.events] == [2]
    assert response.total == 1
    assert "Lazy cleanup removed 1 expired activity events" in [r.getMessage() for r in caplog.records]


async def test_activity_is_still_returned_when_the_retention_cleanup_fails(activity, monkeypatch, caplog):
    service, repo, user = activity
    monkeypatch.setattr(settings, "ACTIVITY_RETENTION_DAYS", 1)
    await service.handle_event(_event(str(user.id), 1))

    async def database_locked(**_):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(repo, "cleanup_expired", database_locked)
    caplog.set_level(logging.ERROR, logger="app.services.activity_service")

    response = await service.get_activity(user.id)

    assert [e.entity_id for e in response.events] == [1]
    failure = next(r for r in caplog.records if r.getMessage() == "Failed to perform lazy activity cleanup")
    assert failure.exc_info[1].args == ("database is locked",)
