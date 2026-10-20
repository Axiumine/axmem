"""ReEmbeddingService when the data shifts under it, and the targeted-rebuild bookkeeping.

A full re-embedding counts rows first and pages through them afterwards, so rows can vanish
in between; a targeted rebuild reports progress per batch, drops batches nobody could embed and
attributes persistence failures to the memory that caused them.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.memory_models import Memory
from app.services.re_embedding_service import ReEmbeddingService


def _memory(memory_id: int) -> Memory:
    return Memory(
        id=memory_id,
        title=f"Memory {memory_id}",
        content="Content",
        context="context",
        keywords=["k"],
        tags=["t"],
        importance=7,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _adapter() -> AsyncMock:
    adapter = AsyncMock()
    adapter.generate_embedding.return_value = [0.1] * 384
    return adapter


def _valid_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.validate_embedding_count.return_value = True
    repo.validate_embedding_dimensions.return_value = True
    repo.validate_search_works.return_value = True
    return repo


def _targeted_repo(memories: list[Memory]) -> AsyncMock:
    repo = AsyncMock()
    repo.count_memories_for_targeted_rebuild.return_value = len(memories)

    async def batch(user_id, limit, after_id=None, memory_ids=None, project=None):
        return [m for m in memories if after_id is None or m.id > after_id][:limit]

    async def upsert(user_id, updates, expected_updated_at=None):
        return [memory_id for memory_id, _ in updates]

    repo.get_memories_for_targeted_rebuild.side_effect = batch
    repo.upsert_targeted_embeddings.side_effect = upsert
    repo.find_similar_memories.return_value = []
    return repo


async def test_full_re_embedding_stops_when_memories_vanish_while_paging():
    repo = _valid_repo()
    repo.count_all_memories.return_value = 5
    pages = {0: [_memory(1), _memory(2)]}

    async def page(limit, offset):
        return pages.get(offset, [])

    repo.get_memories_for_reembedding.side_effect = page

    result = await ReEmbeddingService(repo, _adapter(), batch_size=2).re_embed_all()

    assert (result.total_memories, result.total_processed) == (5, 2)
    repo.bulk_update_embeddings.assert_awaited_once()
    assert [memory_id for memory_id, _ in repo.bulk_update_embeddings.await_args.args[0]] == [1, 2]


def _scoped_repo(matched_in_project: int) -> AsyncMock:
    """A repository that finds ``matched_in_project`` of the memories inside the project."""
    repo = AsyncMock()

    async def count(user_id, memory_ids, project=None):
        return matched_in_project if project is not None else len(memory_ids)

    repo.count_memories_for_targeted_rebuild.side_effect = count
    return repo


async def test_targeted_scope_accepts_memories_that_all_belong_to_the_project():
    repo = _scoped_repo(matched_in_project=2)

    assert await ReEmbeddingService(repo, _adapter()).validate_targeted_scope(
        uuid4(), [1, 2], project="atlas",
    ) is None

    # counted once inside the project and once overall, and both counts agreed
    assert [call.kwargs.get("project") for call in repo.count_memories_for_targeted_rebuild.await_args_list] == ["atlas", None]


async def test_targeted_scope_rejects_a_memory_outside_the_project():
    service = ReEmbeddingService(_scoped_repo(matched_in_project=2), _adapter())

    with pytest.raises(ValueError, match=r"memory_ids do not all belong to project='atlas'.*matched_in_project=2"):
        await service.validate_targeted_scope(uuid4(), [1, 2, 3], project="atlas")


async def test_targeted_rebuild_reports_progress_after_every_batch():
    repo = _targeted_repo([_memory(1), _memory(2), _memory(3)])
    progress: list[tuple[int, int]] = []

    result = await ReEmbeddingService(repo, _adapter(), batch_size=2).rebuild_targeted(
        user_id=uuid4(), progress_callback=lambda done, total: progress.append((done, total)),
    )

    assert progress == [(2, 3), (3, 3)]
    assert result.rebuilt_ids == [1, 2, 3]


async def test_targeted_rebuild_writes_nothing_when_no_memory_could_be_embedded():
    repo = _targeted_repo([_memory(1), _memory(2)])
    adapter = _adapter()
    adapter.generate_embedding.side_effect = RuntimeError("embedding provider down")

    result = await ReEmbeddingService(repo, adapter).rebuild_targeted(user_id=uuid4())

    assert result.rebuilt_ids == []
    assert result.failed == [
        {"memory_id": 1, "reason": "embedding_failed"},
        {"memory_id": 2, "reason": "embedding_failed"},
    ]
    repo.upsert_targeted_embeddings.assert_not_awaited()


async def test_targeted_rebuild_attributes_a_failed_single_row_write_to_that_memory():
    repo = _targeted_repo([_memory(1)])
    repo.upsert_targeted_embeddings.side_effect = RuntimeError("database is locked")

    result = await ReEmbeddingService(repo, _adapter()).rebuild_targeted(user_id=uuid4())

    assert result.rebuilt_ids == []
    assert result.skipped_ids == []
    assert result.failed == [{"memory_id": 1, "reason": "persistence_failed"}]
