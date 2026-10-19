"""PostgreSQL memory update: another writer edits the memory while its embedding is generated.

The embedding is requested before the write transaction opens. If the memory's search text
changes in the meantime the repository embeds again; after the allowed attempts it writes
the update anyway and says so in the log. Updating a memory that vanished raises NotFound.
"""
import logging

import pytest
import pytest_asyncio

from app.exceptions import NotFoundError
from app.models.memory_models import MemoryUpdate
from app.repositories.postgres.memory_repository import (
    MAX_EMBEDDING_ATTEMPTS,
    PostgresMemoryRepository,
)
from tests.e2e.pg_memory_repository_helpers import (
    HashingEmbeddings,
    create_user,
    memory_data,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

UNKNOWN_ID = 987654
MODULE = "app.repositories.postgres.memory_repository"


class EditingEmbeddings(HashingEmbeddings):
    """Embedder during whose provider call a second client renames the memory."""

    def __init__(self, other_client, user_id, memory_id):
        self._other_client = other_client
        self._user_id = user_id
        self._memory_id = memory_id
        self.calls = 0

    async def generate_embedding(self, text):
        self.calls += 1
        current = await self._other_client.get_memory_by_id(self._user_id, self._memory_id)
        await self._other_client.update_memory(
            self._user_id,
            self._memory_id,
            MemoryUpdate(title=f"renamed {self.calls}"),
            existing_memory=current,
            search_fields_changed=False,
        )
        return await super().generate_embedding(text)


@pytest_asyncio.fixture(loop_scope="session")
async def owner(db_adapter):
    return await create_user(db_adapter, "memory-races-e2e")


@pytest_asyncio.fixture(loop_scope="session")
async def overtaken_update(db_adapter, owner, caplog, monkeypatch):
    """Run an update whose memory is renamed by another client during every embedding call."""
    monkeypatch.setattr(logging.getLogger(MODULE), "disabled", False)
    caplog.set_level(logging.INFO, logger=MODULE)
    other_client = PostgresMemoryRepository(db_adapter, HashingEmbeddings())
    memory = await other_client.create_memory(owner, memory_data("original"))
    embedder = EditingEmbeddings(other_client, owner, memory.id)
    updater = PostgresMemoryRepository(db_adapter, embedder)

    updated = await updater.update_memory(
        owner,
        memory.id,
        MemoryUpdate(content="new content"),
        existing_memory=memory,
        search_fields_changed=True,
    )
    return updated, embedder, list(caplog.records)


async def test_update_that_keeps_being_overtaken_is_written_after_the_last_attempt(overtaken_update):
    updated, embedder, _ = overtaken_update

    assert embedder.calls == MAX_EMBEDDING_ATTEMPTS
    assert updated.content == "new content"
    assert updated.title == f"renamed {MAX_EMBEDDING_ATTEMPTS}"


async def test_update_that_keeps_being_overtaken_warns_once_and_retries_before(overtaken_update):
    updated, _, records = overtaken_update

    (warning,) = [r for r in records if r.levelno == logging.WARNING]
    assert warning.getMessage().startswith("memory kept changing while its embedding")
    assert (warning.memory_id, warning.attempts) == (updated.id, MAX_EMBEDDING_ATTEMPTS)
    retries = [r for r in records if r.getMessage().endswith("embedding again")]
    assert len(retries) == MAX_EMBEDDING_ATTEMPTS - 1


async def test_updating_a_memory_that_no_longer_exists_raises_not_found(db_adapter, owner):
    repository = PostgresMemoryRepository(db_adapter, HashingEmbeddings())
    existing = await repository.create_memory(owner, memory_data("unrelated"))

    with pytest.raises(NotFoundError, match=f"Memory with id {UNKNOWN_ID} not found"):
        await repository.update_memory(
            owner, UNKNOWN_ID, MemoryUpdate(importance=9),
            existing_memory=existing, search_fields_changed=False,
        )
