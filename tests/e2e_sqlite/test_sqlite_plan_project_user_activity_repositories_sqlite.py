"""Plan, project, user and activity repositories on a real SQLite database.

Services look a row up before they update or delete it, so these tests call the repositories
directly to pin their own answers for unknown ids, empty patches and retention clean-up.
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.exceptions import NotFoundError
from app.models.activity_models import (
    ActionType,
    ActivityEvent,
    ActorType,
    EntityType,
)
from app.models.plan_models import PlanCreate, PlanUpdate
from app.models.project_models import ProjectCreate, ProjectType, ProjectUpdate
from app.models.user_models import UserUpdate
from app.repositories.sqlite.activity_repository import SqliteActivityRepository
from app.repositories.sqlite.plan_repository import SqlitePlanRepository
from app.repositories.sqlite.project_repository import SqliteProjectRepository
from app.repositories.sqlite.user_repository import SqliteUserRepository
from tests.e2e_sqlite.sqlite_repository_helpers import (
    UNKNOWN_ID,
    create_user,
    open_adapter,
)


@pytest.fixture
async def adapter(monkeypatch):
    async with open_adapter(monkeypatch) as opened:
        yield opened


@pytest.fixture
async def user_id(adapter):
    return await create_user(adapter)


async def _project(adapter, user_id, name="Atlas"):
    return await SqliteProjectRepository(adapter).create_project(
        user_id,
        ProjectCreate(name=name, description="Planning target", project_type=ProjectType.WORK),
    )


# --- plans ---------------------------------------------------------------------------------


async def test_empty_plan_update_returns_the_stored_plan_unchanged(adapter, user_id):
    project = await _project(adapter, user_id)
    plans = SqlitePlanRepository(adapter)
    created = await plans.create_plan(
        user_id, PlanCreate(title="Ship v2", project_id=project.id, goal="Release"),
    )

    result = await plans.update_plan(user_id, created.id, PlanUpdate())

    assert result == created
    assert await plans.get_plan_by_id(user_id, created.id) == created


async def test_empty_plan_update_of_an_unknown_plan_raises_not_found(adapter, user_id):
    with pytest.raises(NotFoundError, match=f"^Plan with id {UNKNOWN_ID} not found$"):
        await SqlitePlanRepository(adapter).update_plan(user_id, UNKNOWN_ID, PlanUpdate())


async def test_plan_update_of_an_unknown_plan_raises_not_found(adapter, user_id):
    with pytest.raises(NotFoundError, match=f"^Plan with id {UNKNOWN_ID} not found$"):
        await SqlitePlanRepository(adapter).update_plan(
            user_id, UNKNOWN_ID, PlanUpdate(title="Renamed"),
        )


async def test_other_constraint_failures_on_plan_update_are_not_reported_as_external_ref_conflicts(
    adapter, user_id,
):
    project = await _project(adapter, user_id)
    plans = SqlitePlanRepository(adapter)
    created = await plans.create_plan(user_id, PlanCreate(title="Frozen", project_id=project.id))
    async with adapter.system_session() as session:
        await session.execute(text(
            "CREATE TRIGGER plans_are_frozen BEFORE UPDATE ON plans "
            "BEGIN SELECT RAISE(ABORT, 'plans are frozen'); END",
        ))

    with pytest.raises(IntegrityError, match="plans are frozen"):
        await plans.update_plan(user_id, created.id, PlanUpdate(title="Thawed"))

    assert (await plans.get_plan_by_id(user_id, created.id)).title == "Frozen"


# --- projects ------------------------------------------------------------------------------


async def test_empty_project_update_returns_the_stored_project_unchanged(adapter, user_id):
    projects = SqliteProjectRepository(adapter)
    created = await _project(adapter, user_id)

    result = await projects.update_project(user_id, created.id, ProjectUpdate())

    assert result == created
    assert result.updated_at == created.updated_at


async def test_empty_project_update_of_an_unknown_project_raises_not_found(adapter, user_id):
    with pytest.raises(NotFoundError, match=f"^Project with id {UNKNOWN_ID} not found$"):
        await SqliteProjectRepository(adapter).update_project(user_id, UNKNOWN_ID, ProjectUpdate())


async def test_project_update_of_an_unknown_project_raises_not_found(adapter, user_id):
    with pytest.raises(NotFoundError, match=f"^Project with id {UNKNOWN_ID} not found$"):
        await SqliteProjectRepository(adapter).update_project(
            user_id, UNKNOWN_ID, ProjectUpdate(name="Renamed"),
        )


async def test_project_update_is_scoped_to_the_owner(adapter, user_id):
    other_user = await create_user(adapter, name="Other")
    project = await _project(adapter, user_id)

    with pytest.raises(NotFoundError, match=f"^Project with id {project.id} not found$"):
        await SqliteProjectRepository(adapter).update_project(
            other_user, project.id, ProjectUpdate(name="Hijacked"),
        )

    assert (await SqliteProjectRepository(adapter).get_project_by_id(user_id, project.id)).name == "Atlas"


async def test_deleting_a_project_twice_reports_false_the_second_time(adapter, user_id):
    projects = SqliteProjectRepository(adapter)
    project = await _project(adapter, user_id)

    assert await projects.delete_project(user_id, project.id) is True
    assert await projects.delete_project(user_id, project.id) is False
    assert await projects.get_project_by_id(user_id, project.id) is None


# --- users ---------------------------------------------------------------------------------


async def test_user_is_found_by_internal_id(adapter, user_id):
    users = SqliteUserRepository(adapter)

    found = await users.get_user_by_id(user_id)

    assert found is not None
    assert found.id == user_id
    assert found.name == "Repository Test"


async def test_unknown_internal_user_id_finds_nothing(adapter):
    assert await SqliteUserRepository(adapter).get_user_by_id(uuid4()) is None


async def test_updating_an_unknown_user_raises_not_found(adapter):
    missing = uuid4()

    with pytest.raises(NotFoundError, match=f"^User with id {missing} not found$"):
        await SqliteUserRepository(adapter).update_user(missing, UserUpdate(name="Ghost"))


# --- activity ------------------------------------------------------------------------------


def _event(actor=ActorType.USER, created_at=None, entity_id=1):
    fields = {"created_at": created_at} if created_at else {}
    return ActivityEvent(
        entity_type=EntityType.MEMORY,
        entity_id=entity_id,
        action=ActionType.CREATED,
        snapshot={"id": entity_id},
        actor=actor,
        **fields,
    )


async def test_events_can_be_filtered_by_actor(adapter, user_id):
    activity = SqliteActivityRepository(adapter)
    await activity.save_event(user_id, _event(ActorType.USER, entity_id=1))
    await activity.save_event(user_id, _event(ActorType.SYSTEM, entity_id=2))
    await activity.save_event(user_id, _event(ActorType.SYSTEM, entity_id=3))

    events, total = await activity.query_events(user_id, actor=ActorType.SYSTEM)

    assert total == 2
    assert sorted(entry.entity_id for entry in events) == [2, 3]
    assert {entry.actor for entry in events} == {ActorType.SYSTEM}


async def test_cleanup_deletes_only_this_users_events_older_than_the_retention_period(adapter, user_id):
    activity = SqliteActivityRepository(adapter)
    other_user = await create_user(adapter, name="Other")
    now = datetime.now(UTC)
    await activity.save_event(user_id, _event(created_at=now - timedelta(days=90), entity_id=1))
    await activity.save_event(user_id, _event(created_at=now - timedelta(days=45), entity_id=2))
    await activity.save_event(user_id, _event(created_at=now - timedelta(days=5), entity_id=3))
    await activity.save_event(other_user, _event(created_at=now - timedelta(days=90), entity_id=4))

    deleted = await activity.cleanup_expired(user_id, retention_days=30)

    assert deleted == 2
    remaining, _ = await activity.query_events(user_id)
    assert [entry.entity_id for entry in remaining] == [3]
    others, _ = await activity.query_events(other_user)
    assert [entry.entity_id for entry in others] == [4]


async def test_cleanup_with_nothing_expired_deletes_nothing(adapter, user_id):
    activity = SqliteActivityRepository(adapter)
    await activity.save_event(user_id, _event(entity_id=1))

    assert await activity.cleanup_expired(user_id, retention_days=30) == 0

    _, total = await activity.query_events(user_id)
    assert total == 1
