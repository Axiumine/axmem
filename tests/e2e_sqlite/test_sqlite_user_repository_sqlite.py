"""User repository on a real SQLite database.

Services look a row up before they use it, so these tests call the repository directly to pin
its own answers for unknown tenant names and for a second record under one name.
"""
import pytest

from app.exceptions import ConflictError
from app.models.user_models import UserCreate
from app.repositories.sqlite.user_repository import SqliteUserRepository
from tests.e2e_sqlite.sqlite_repository_helpers import open_adapter


@pytest.fixture
async def adapter(monkeypatch):
    async with open_adapter(monkeypatch) as opened:
        yield opened


async def test_tenant_is_found_by_name(adapter):
    repo = SqliteUserRepository(adapter)
    created = await repo.create_user(UserCreate(external_id="acme", name="acme", email=""))

    found = await repo.get_user_by_external_id("acme")

    assert found is not None
    assert (found.id, found.external_id) == (created.id, "acme")


async def test_unknown_tenant_name_finds_nothing(adapter):
    assert await SqliteUserRepository(adapter).get_user_by_external_id("nobody") is None


async def test_a_second_record_for_one_tenant_name_conflicts(adapter):
    repo = SqliteUserRepository(adapter)
    await repo.create_user(UserCreate(external_id="acme", name="acme", email=""))

    with pytest.raises(ConflictError):
        await repo.create_user(UserCreate(external_id="acme", name="acme", email=""))
