"""Project and user repositories on a real SQLite database.

Services look a row up before they update or delete it, so these tests call the repositories
directly to pin their own answers for unknown ids and empty patches.
"""
from uuid import uuid4

import pytest

from app.exceptions import NotFoundError
from app.models.project_models import ProjectCreate, ProjectType, ProjectUpdate
from app.models.user_models import UserUpdate
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
        ProjectCreate(name=name, description="Repository target", project_type=ProjectType.WORK),
    )


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
