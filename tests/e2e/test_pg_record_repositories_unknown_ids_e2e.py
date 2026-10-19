"""PostgreSQL repositories: contract for ids that do not exist (or belong to nobody).

The services check existence before they call a repository, but the repositories are a
contract of their own: a concurrent delete can still remove a record between the check and
the write. For projects and users the repository itself
reports an unknown id with the documented exception or return value.
"""
from uuid import uuid4

import pytest
import pytest_asyncio

from app.exceptions import NotFoundError
from app.models.project_models import ProjectCreate, ProjectType, ProjectUpdate
from app.models.user_models import UserCreate, UserUpdate
from app.repositories.postgres.project_repository import PostgresProjectRepository
from app.repositories.postgres.user_repository import PostgresUserRepository

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

UNKNOWN_ID = 987654


@pytest_asyncio.fixture(loop_scope="session")
async def user_repo(db_adapter):
    return PostgresUserRepository(db_adapter)


@pytest_asyncio.fixture(loop_scope="session")
async def owner(user_repo):
    user = await user_repo.create_user(
        UserCreate(external_id="unknown-ids-e2e", name="Owner", email="owner@example.com"),
    )
    return user.id


async def test_project_update_without_fields_returns_the_stored_project(db_adapter, owner):
    projects = PostgresProjectRepository(db_adapter)
    created = await projects.create_project(
        owner, ProjectCreate(name="kept", description="d", project_type=ProjectType.DEVELOPMENT),
    )

    unchanged = await projects.update_project(owner, created.id, ProjectUpdate())

    assert unchanged.id == created.id
    assert unchanged.name == "kept"
    assert unchanged.updated_at == created.updated_at


@pytest.mark.parametrize(
    "patch",
    [pytest.param(ProjectUpdate(), id="without-fields"), pytest.param(ProjectUpdate(name="x"), id="with-fields")],
)
async def test_updating_an_unknown_project_raises_not_found(db_adapter, owner, patch):
    projects = PostgresProjectRepository(db_adapter)

    with pytest.raises(NotFoundError, match=f"Project with id {UNKNOWN_ID} not found"):
        await projects.update_project(owner, UNKNOWN_ID, patch)


async def test_deleting_an_unknown_project_reports_nothing_deleted(db_adapter, owner):
    projects = PostgresProjectRepository(db_adapter)

    assert await projects.delete_project(owner, UNKNOWN_ID) is False


async def test_another_users_project_cannot_be_updated_or_deleted(db_adapter, owner, user_repo):
    projects = PostgresProjectRepository(db_adapter)
    other = await user_repo.create_user(
        UserCreate(external_id="intruder-e2e", name="Other", email="other@example.com"),
    )
    created = await projects.create_project(
        owner, ProjectCreate(name="mine", description="d", project_type=ProjectType.DEVELOPMENT),
    )

    with pytest.raises(NotFoundError):
        await projects.update_project(other.id, created.id, ProjectUpdate(name="stolen"))
    assert await projects.delete_project(other.id, created.id) is False
    assert (await projects.get_project_by_id(owner, created.id)).name == "mine"


async def test_user_lookup_by_internal_id_finds_the_stored_user(user_repo):
    created = await user_repo.create_user(
        UserCreate(external_id="lookup-e2e", name="Lookup", email="lookup@example.com"),
    )

    found = await user_repo.get_user_by_id(created.id)

    assert found is not None
    assert (found.id, found.external_id, found.name) == (created.id, "lookup-e2e", "Lookup")


async def test_user_lookup_by_unknown_internal_id_finds_nothing(user_repo):
    assert await user_repo.get_user_by_id(uuid4()) is None


async def test_updating_an_unknown_user_raises_not_found(user_repo):
    unknown = uuid4()

    with pytest.raises(NotFoundError, match=f"User with id {unknown} not found"):
        await user_repo.update_user(unknown, UserUpdate(name="ghost"))
