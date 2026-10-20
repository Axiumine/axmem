"""PostgreSQL memory repository: updates, obsolescence, access counting and links.

Calls the repository directly, so other users' ids, ids that do not exist, writers racing an
embedding call and the optional link filters are exercised against real rows. The services
check most of these before the repository is reached.
"""
import logging

import pytest
import pytest_asyncio
from sqlalchemy import update

from app.exceptions import InvalidStateTransitionError, NotFoundError
from app.models.memory_models import MemoryUpdate
from app.repositories.postgres.memory_repository import (
    MAX_EMBEDDING_ATTEMPTS,
    PostgresMemoryRepository,
)
from app.repositories.postgres.postgres_tables import MemoryTable
from tests.e2e.pg_repository_world_e2e_helpers import (
    RepositoryWorld,
    build_world,
    captured_logger,
    new_memory,
)
from tests.embedding_doubles import DelegatingEmbedder

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

MEMORY_LOGGER = "app.repositories.postgres.memory_repository"
# Larger than the INTEGER (int32) memory id column accepts
OUT_OF_RANGE_ID = 99_999_999_999


@pytest_asyncio.fixture(loop_scope="session")
async def world(db_adapter, tensorrt_embedder) -> RepositoryWorld:
    return await build_world(db_adapter, tensorrt_embedder)


class RenamingEmbeddings(DelegatingEmbedder):
    """The real embedder, during whose call another writer renames the memory, every time."""

    def __init__(self, real, db_adapter, memory_id: int):
        super().__init__(real)
        self.db_adapter = db_adapter
        self.memory_id = memory_id
        self.calls = 0

    async def generate_embedding(self, text: str) -> list[float]:
        self.calls += 1
        async with self.db_adapter.system_session() as session:
            await session.execute(
                update(MemoryTable)
                .where(MemoryTable.id == self.memory_id)
                .values(title=f"concurrent edit {self.calls}"),
            )
        return await super().generate_embedding(text)


def _split_by_level(
    records: list[logging.LogRecord],
) -> tuple[list[logging.LogRecord], list[logging.LogRecord]]:
    """The INFO and the WARNING records, in emission order."""
    infos = [r for r in records if r.levelno == logging.INFO]
    warnings = [r for r in records if r.levelno == logging.WARNING]
    return infos, warnings


async def test_update_of_another_users_memory_is_not_found_and_changes_nothing(world):
    strangers = await new_memory(world, world.stranger, "Original", importance=7)

    with pytest.raises(NotFoundError) as refused:
        await world.memories.update_memory(
            world.owner,
            strangers.id,
            MemoryUpdate(importance=2),
            existing_memory=strangers,
            search_fields_changed=False,
        )

    assert str(refused.value) == f"Memory with id {strangers.id} not found"
    stored = await world.memories.get_memory_by_id(world.stranger, strangers.id)
    assert stored.importance == 7


async def test_update_keeps_a_memory_that_changes_during_embedding_and_warns_on_the_last_attempt(
    world, db_adapter, caplog, tensorrt_embedder,
):
    memory = await new_memory(world, world.owner, "Original title")
    embeddings = RenamingEmbeddings(tensorrt_embedder, db_adapter, memory.id)
    repo = PostgresMemoryRepository(db_adapter, embeddings)

    with captured_logger(caplog, MEMORY_LOGGER, logging.INFO) as records:
        updated = await repo.update_memory(
            world.owner,
            memory.id,
            MemoryUpdate(content="Rewritten content"),
            existing_memory=memory,
            search_fields_changed=True,
        )

    assert embeddings.calls == MAX_EMBEDDING_ATTEMPTS
    assert updated.content == "Rewritten content"
    assert updated.title == f"concurrent edit {MAX_EMBEDDING_ATTEMPTS}"
    retries, warnings = _split_by_level(records)
    assert [r.attempt for r in retries] == list(range(1, MAX_EMBEDDING_ATTEMPTS))
    (warning,) = warnings
    assert warning.getMessage() == (
        "memory kept changing while its embedding was generated, writing without verification"
    )
    assert (warning.memory_id, warning.attempts) == (memory.id, MAX_EMBEDDING_ATTEMPTS)


async def test_marking_an_out_of_range_memory_id_obsolete_is_not_found(world):
    with pytest.raises(NotFoundError) as refused:
        await world.memories.mark_obsolete(world.owner, OUT_OF_RANGE_ID, "gone")

    assert str(refused.value) == f"Memory {OUT_OF_RANGE_ID} not found"


async def test_marking_another_users_memory_obsolete_is_not_found_and_keeps_it_active(world):
    strangers = await new_memory(world, world.stranger, "Stranger memory")

    with pytest.raises(NotFoundError) as refused:
        await world.memories.mark_obsolete(world.owner, strangers.id, "not yours")

    assert str(refused.value) == f"Memory {strangers.id} not found"
    assert (await world.memories.get_memory_by_id(world.stranger, strangers.id)).is_obsolete is False


async def test_marking_an_obsolete_memory_obsolete_again_is_an_invalid_transition(world):
    memory = await new_memory(world, world.owner, "Outdated")
    assert await world.memories.mark_obsolete(world.owner, memory.id, "first reason") is True

    with pytest.raises(InvalidStateTransitionError) as refused:
        await world.memories.mark_obsolete(world.owner, memory.id, "second reason")

    assert str(refused.value) == f"Memory {memory.id} is already obsolete"
    stored = await world.memories.get_memory_by_id(world.owner, memory.id)
    assert stored.obsolete_reason == "first reason"


async def test_restoring_an_obsolete_memory_clears_its_obsolescence(world):
    replacement = await new_memory(world, world.owner, "Replacement")
    memory = await new_memory(world, world.owner, "Outdated")
    await world.memories.mark_obsolete(
        world.owner, memory.id, "superseded", superseded_by=replacement.id,
    )

    assert await world.memories.restore_obsolete(world.owner, memory.id) is True

    restored = await world.memories.get_memory_by_id(world.owner, memory.id)
    assert (restored.is_obsolete, restored.obsolete_reason, restored.superseded_by) == (
        False, None, None,
    )


async def test_restoring_an_active_memory_returns_false(world):
    memory = await new_memory(world, world.owner, "Still active")

    assert await world.memories.restore_obsolete(world.owner, memory.id) is False

    assert (await world.memories.get_memory_by_id(world.owner, memory.id)).is_obsolete is False


async def test_restoring_another_users_obsolete_memory_returns_false_and_keeps_it_obsolete(world):
    strangers = await new_memory(world, world.stranger, "Stranger memory")
    await world.memories.mark_obsolete(world.stranger, strangers.id, "outdated")

    assert await world.memories.restore_obsolete(world.owner, strangers.id) is False

    assert (await world.memories.get_memory_by_id(world.stranger, strangers.id)).is_obsolete is True


async def test_linked_memories_can_be_limited_to_a_project(world):
    owner = world.owner
    project = "Linked project"
    centre = await new_memory(world, owner, "Centre")
    in_project = await new_memory(world, owner, "In the project", project=project)
    outside = await new_memory(world, owner, "Outside the project")
    await world.memories.create_link(owner, centre.id, in_project.id)
    await world.memories.create_link(owner, centre.id, outside.id)

    everything = await world.memories.get_linked_memories(owner, centre.id, project=None)
    scoped = await world.memories.get_linked_memories(owner, centre.id, project=project)

    assert sorted(m.id for m in everything) == sorted([in_project.id, outside.id])
    assert [m.id for m in scoped] == [in_project.id]


async def test_link_from_another_users_memory_is_not_found_and_links_nothing(world):
    strangers = await new_memory(world, world.stranger, "Stranger memory")
    own = await new_memory(world, world.owner, "Own memory")

    with pytest.raises(NotFoundError) as refused:
        await world.memories.create_link(world.owner, strangers.id, own.id)

    assert str(refused.value) == f"Source memory with id {strangers.id} not found"
    assert await world.memories.get_linked_memories(world.owner, own.id, project=None) == []


async def test_batch_link_without_targets_links_nothing(world):
    source = await new_memory(world, world.owner, "Source")

    assert await world.memories.create_links_batch(world.owner, source.id, []) == []


async def test_batch_link_from_an_out_of_range_source_links_nothing(world):
    target = await new_memory(world, world.owner, "Target")

    assert await world.memories.create_links_batch(world.owner, OUT_OF_RANGE_ID, [target.id]) == []

    assert await world.memories.get_linked_memories(world.owner, target.id, project=None) == []


async def test_batch_link_skips_the_source_itself(world):
    source = await new_memory(world, world.owner, "Source")
    target = await new_memory(world, world.owner, "Target")

    linked = await world.memories.create_links_batch(world.owner, source.id, [source.id, target.id])

    assert linked == [target.id]
    neighbours = await world.memories.get_linked_memories(world.owner, source.id, project=None)
    assert [m.id for m in neighbours] == [target.id]
