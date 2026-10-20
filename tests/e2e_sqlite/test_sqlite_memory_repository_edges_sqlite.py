"""SQLite memory repository: answers for empty inputs, unknown ids and optional filters.

The memory service validates ids and normalises filters before it calls the repository, so the
repository's own behaviour for an omitted exclusion list, an empty batch or an unknown memory is
only visible when it is called directly. Embeddings come from a deterministic offline stub.
"""
import pytest

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.memory_models import MemoryCreate
from app.repositories.sqlite.memory_repository import SqliteMemoryRepository
from tests.e2e_sqlite.sqlite_repository_helpers import (
    UNKNOWN_ID,
    create_user,
    open_adapter,
)


class TopicEmbeddings:
    """Offline embedder: every text maps to the same unit vector, so all memories are neighbours."""

    async def generate_embedding(self, text: str) -> list[float]:
        return [1.0, *([0.0] * (settings.EMBEDDING_DIMENSIONS - 1))]


def _memory(title: str) -> MemoryCreate:
    return MemoryCreate(
        title=title,
        content=f"{title} body",
        context="repository edge cases",
        keywords=["edge"],
        tags=["edge"],
        importance=7,
    )


@pytest.fixture
async def adapter(monkeypatch):
    async with open_adapter(monkeypatch) as opened:
        yield opened


@pytest.fixture
async def repo(adapter):
    return SqliteMemoryRepository(
        db_adapter=adapter, embedding_adapter=TopicEmbeddings(), rerank_adapter=None,
    )


@pytest.fixture
async def user_id(adapter):
    return await create_user(adapter)


@pytest.fixture
async def memories(repo, user_id):
    return [await repo.create_memory(user_id, _memory(title)) for title in ("One", "Two", "Three")]


async def test_linked_memories_include_both_link_directions_when_nothing_is_excluded(
    repo, user_id, memories,
):
    one, two, three = memories
    await repo.create_link(user_id, one.id, two.id)
    await repo.create_link(user_id, three.id, one.id)

    linked = await repo.get_linked_memories(user_id, one.id, project_ids=None)

    assert sorted(memory.id for memory in linked) == sorted([two.id, three.id])


async def test_batch_link_with_no_targets_creates_nothing(repo, user_id, memories):
    one = memories[0]

    assert await repo.create_links_batch(user_id, one.id, []) == []

    assert await repo.get_linked_memories(user_id, one.id, project_ids=None) == []


async def test_batch_link_skips_the_source_memory_itself(repo, user_id, memories):
    one, two, _ = memories

    created = await repo.create_links_batch(user_id, one.id, [one.id, two.id])

    assert created == [two.id]
    linked = await repo.get_linked_memories(user_id, one.id, project_ids=None)
    assert [memory.id for memory in linked] == [two.id]


async def test_semantic_search_leaves_out_excluded_memory_ids(repo, user_id, memories):
    one, two, three = memories

    found = await repo.semantic_search_scored(
        user_id, "One body", k=10, importance_threshold=None, project_ids=None,
        exclude_ids=[one.id, three.id],
    )

    assert [memory.id for memory, _ in found] == [two.id]


async def test_semantic_search_without_an_exclusion_list_returns_every_neighbour(
    repo, user_id, memories,
):
    found = await repo.semantic_search_scored(
        user_id, "One body", k=10, importance_threshold=None, project_ids=None, exclude_ids=None,
    )

    assert sorted(memory.id for memory, _ in found) == sorted(memory.id for memory in memories)


async def test_marking_an_unknown_memory_obsolete_raises_not_found(repo, user_id):
    with pytest.raises(NotFoundError, match=f"^Memory {UNKNOWN_ID} not found$"):
        await repo.mark_obsolete(user_id, UNKNOWN_ID, reason="stale")


async def test_targeted_embedding_upsert_with_no_updates_writes_nothing(repo, user_id):
    assert await repo.upsert_targeted_embeddings(user_id, []) == []


async def test_reading_an_unknown_memory_raises_not_found(repo, user_id):
    with pytest.raises(NotFoundError, match=f"^Memory with id {UNKNOWN_ID} not found$"):
        await repo.get_memory_by_id(user_id, UNKNOWN_ID)


async def test_reading_another_users_memory_raises_not_found(repo, adapter, memories):
    intruder = await create_user(adapter, name="Intruder")

    with pytest.raises(NotFoundError, match=f"^Memory with id {memories[0].id} not found$"):
        await repo.get_memory_by_id(intruder, memories[0].id)
