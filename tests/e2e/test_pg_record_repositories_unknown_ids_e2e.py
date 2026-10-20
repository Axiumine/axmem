"""PostgreSQL tenant repository: contract for names that do not exist (or belong to nobody).

The tenant service looks a record up before it creates one, but the repository is a contract
of its own: it reports an unknown name with the documented return value, and refuses a
second record for the same tenant name.
"""
import pytest
import pytest_asyncio

from app.exceptions import ConflictError
from app.models.user_models import UserCreate
from app.repositories.postgres.user_repository import PostgresUserRepository

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def user_repo(db_adapter):
    return PostgresUserRepository(db_adapter)


async def test_user_lookup_by_external_id_finds_the_stored_user(user_repo):
    created = await user_repo.create_user(
        UserCreate(external_id="external-lookup-e2e", name="External", email=""),
    )

    found = await user_repo.get_user_by_external_id("external-lookup-e2e")

    assert found is not None
    assert found.id == created.id


async def test_user_lookup_by_unknown_external_id_finds_nothing(user_repo):
    assert await user_repo.get_user_by_external_id("nobody-e2e") is None


async def test_a_second_record_for_the_same_tenant_is_a_conflict(user_repo):
    await user_repo.create_user(UserCreate(external_id="twice-e2e", name="Twice", email=""))

    with pytest.raises(ConflictError):
        await user_repo.create_user(UserCreate(external_id="twice-e2e", name="Twice", email=""))
