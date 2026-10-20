"""Integration tests for ReEmbeddingService.

Uses a mocked repository (no real database) and the real TensorRT embedder behind call-recording
spies (``embedding_spy``); the provider faults are injected through the spies' ``side_effect``.
"""
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.config.settings import settings
from app.models.memory_models import Memory
from app.services.re_embedding_service import ReEmbeddingService
from tests.embedding_doubles import failing_on


def _make_memory(memory_id: int, title: str = "Test", content: str = "Content") -> Memory:
    """Helper to create a Memory object for testing"""
    return Memory(
        id=memory_id,
        title=title,
        content=content,
        context="test context",
        keywords=["test"],
        tags=["test"],
        importance=7,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_mock_repo(memories: list[Memory]):
    """Create a mock MemoryRepository with re-embedding methods"""
    repo = AsyncMock()
    repo.count_all_memories.return_value = len(memories)

    # get_memories_for_reembedding returns slices of the memory list
    async def get_batch(limit: int, offset: int):
        return memories[offset:offset + limit]

    repo.get_memories_for_reembedding.side_effect = get_batch
    repo.reset_embedding_storage.return_value = None
    repo.bulk_update_embeddings.return_value = None
    repo.validate_embedding_count.return_value = True
    repo.validate_embedding_dimensions.return_value = True
    repo.validate_search_works.return_value = True
    return repo


@pytest.mark.asyncio
async def test_re_embed_processes_all_memories(embedding_spy):
    """Verify every memory gets a new embedding"""
    memories = [_make_memory(i, title=f"Memory {i}") for i in range(1, 6)]
    repo = _make_mock_repo(memories)
    adapter = embedding_spy

    service = ReEmbeddingService(repo, adapter, batch_size=20)
    result = await service.re_embed_all()

    assert result.total_processed == 5
    assert result.total_memories == 5
    # One provider call carries the whole batch of five memories
    adapter.generate_embeddings.assert_awaited_once()
    assert len(adapter.generate_embeddings.call_args[0][0]) == 5
    adapter.generate_embedding.assert_not_called()
    repo.bulk_update_embeddings.assert_called_once()

    # Verify all memory IDs were included in the update
    call_args = repo.bulk_update_embeddings.call_args[0][0]
    updated_ids = {mid for mid, _ in call_args}
    assert updated_ids == {1, 2, 3, 4, 5}


@pytest.mark.asyncio
async def test_re_embed_respects_batch_size(embedding_spy):
    """Verify batching with various sizes"""
    memories = [_make_memory(i) for i in range(1, 21)]
    repo = _make_mock_repo(memories)
    adapter = embedding_spy

    service = ReEmbeddingService(repo, adapter, batch_size=5)
    result = await service.re_embed_all()

    assert result.total_processed == 20
    # Should have 4 batches of 5
    assert repo.bulk_update_embeddings.call_count == 4
    assert adapter.generate_embeddings.call_count == 4
    assert sum(len(call[0][0]) for call in adapter.generate_embeddings.call_args_list) == 20


@pytest.mark.asyncio
async def test_re_embed_partial_batch(embedding_spy):
    """7 memories with batch_size=5 -> 2 batches"""
    memories = [_make_memory(i) for i in range(1, 8)]
    repo = _make_mock_repo(memories)
    adapter = embedding_spy

    service = ReEmbeddingService(repo, adapter, batch_size=5)
    result = await service.re_embed_all()

    assert result.total_processed == 7
    assert repo.bulk_update_embeddings.call_count == 2

    # First batch: 5 items, second batch: 2 items
    first_batch = repo.bulk_update_embeddings.call_args_list[0][0][0]
    second_batch = repo.bulk_update_embeddings.call_args_list[1][0][0]
    assert len(first_batch) == 5
    assert len(second_batch) == 2


@pytest.mark.asyncio
async def test_re_embed_empty_database(embedding_spy):
    """0 memories -> success with no embedding work, but storage still matches the configuration"""
    repo = _make_mock_repo([])
    adapter = embedding_spy

    service = ReEmbeddingService(repo, adapter, batch_size=20)
    result = await service.re_embed_all()

    assert result.total_processed == 0
    assert result.total_memories == 0
    assert result.validation.all_passed
    # Vector storage is still resized (a dimension change needs it); nothing is written
    repo.reset_embedding_storage.assert_awaited_once()
    repo.bulk_update_embeddings.assert_not_called()
    adapter.generate_embeddings.assert_not_called()


@pytest.mark.asyncio
async def test_re_embed_progress_callback(embedding_spy):
    """Verify progress callback fires per batch"""
    memories = [_make_memory(i) for i in range(1, 8)]
    repo = _make_mock_repo(memories)
    adapter = embedding_spy

    progress_calls = []

    def progress_cb(processed, total):
        progress_calls.append((processed, total))

    service = ReEmbeddingService(repo, adapter, batch_size=3)
    result = await service.re_embed_all(progress_callback=progress_cb)

    assert result.total_processed == 7
    # batch_size=3, 7 items -> batches of 3, 3, 1
    assert len(progress_calls) == 3
    assert progress_calls[0] == (3, 7)
    assert progress_calls[1] == (6, 7)
    assert progress_calls[2] == (7, 7)


@pytest.mark.asyncio
async def test_re_embed_adapter_error_propagates(embedding_spy):
    """Embedding adapter throws -> error raised"""
    memories = [_make_memory(1)]
    repo = _make_mock_repo(memories)
    adapter = embedding_spy
    adapter.generate_embeddings.side_effect = RuntimeError("API rate limit exceeded")

    service = ReEmbeddingService(repo, adapter, batch_size=20)

    with pytest.raises(RuntimeError, match="API rate limit exceeded"):
        await service.re_embed_all()


@pytest.mark.asyncio
async def test_re_embed_calls_reset_before_embedding(embedding_spy):
    """Verify reset_embedding_storage is called before bulk_update_embeddings"""
    memories = [_make_memory(1)]
    repo = _make_mock_repo(memories)
    adapter = embedding_spy

    call_order = []

    async def track_reset():
        call_order.append("reset")
    async def track_bulk(updates):
        call_order.append("bulk")

    repo.reset_embedding_storage.side_effect = track_reset
    repo.bulk_update_embeddings.side_effect = track_bulk

    service = ReEmbeddingService(repo, adapter, batch_size=20)
    await service.re_embed_all()

    assert call_order == ["reset", "bulk"]


@pytest.mark.asyncio
async def test_re_embed_validation_failure(embedding_spy):
    """Verify validation result reflects failures"""
    memories = [_make_memory(1)]
    repo = _make_mock_repo(memories)
    repo.validate_embedding_count.return_value = False
    adapter = embedding_spy

    service = ReEmbeddingService(repo, adapter, batch_size=20)
    result = await service.re_embed_all()

    assert result.validation is not None
    assert not result.validation.count_ok
    assert result.validation.dimensions_ok
    assert result.validation.search_ok
    assert not result.validation.all_passed


# ---------------------------------------------------------------------------
# rebuild_targeted (issue #39) — user-scoped, non-destructive rebuild
# ---------------------------------------------------------------------------


def _make_targeted_repo(memories: list[Memory], owned_by_user: bool = True):
    """Mock repo wired for rebuild_targeted (no reset, user-scoped upsert)."""
    repo = AsyncMock()
    repo.count_memories_for_targeted_rebuild.return_value = len(memories)

    async def get_batch(user_id, limit, after_id=None, memory_ids=None,
                        project=None):
        eligible = memories
        if after_id is not None:
            eligible = [memory for memory in eligible if memory.id > after_id]
        return eligible[:limit]

    async def upsert_targeted(user_id, updates, expected_updated_at=None):
        return [mid for mid, _ in updates] if owned_by_user else []

    repo.get_memories_for_targeted_rebuild.side_effect = get_batch
    repo.upsert_targeted_embeddings.side_effect = upsert_targeted
    repo.find_similar_memories.return_value = []
    repo.create_links_batch.return_value = []
    return repo


@pytest.mark.asyncio
async def test_rebuild_targeted_does_not_reset_storage(embedding_spy):
    """rebuild_targeted must never call reset_embedding_storage()."""
    from uuid import uuid4

    memories = [_make_memory(i) for i in range(1, 4)]
    repo = _make_targeted_repo(memories)
    adapter = embedding_spy

    service = ReEmbeddingService(repo, adapter, batch_size=20)
    result = await service.rebuild_targeted(user_id=uuid4())

    repo.reset_embedding_storage.assert_not_called()
    assert sorted(result.rebuilt_ids) == [1, 2, 3]
    assert result.skipped_ids == []
    assert result.failed == []


@pytest.mark.asyncio
async def test_rebuild_targeted_no_candidates_short_circuits(embedding_spy):
    """Empty candidate set -> no embedding work, empty result."""
    from uuid import uuid4

    repo = _make_targeted_repo([])
    adapter = embedding_spy

    service = ReEmbeddingService(repo, adapter, batch_size=20)
    result = await service.rebuild_targeted(user_id=uuid4())

    assert result.rebuilt_ids == []
    adapter.generate_embeddings.assert_not_called()
    adapter.generate_embedding.assert_not_called()
    repo.upsert_targeted_embeddings.assert_not_called()


@pytest.mark.asyncio
async def test_rebuild_targeted_skips_unowned_ids(embedding_spy):
    """Memories rejected by upsert (not owned) land in skipped_ids."""
    from uuid import uuid4

    memories = [_make_memory(i) for i in range(1, 4)]
    repo = _make_mock_repo([])  # not used directly
    repo.count_memories_for_targeted_rebuild.return_value = len(memories)

    async def get_batch(user_id, limit, after_id=None, memory_ids=None,
                        project=None):
        eligible = memories
        if after_id is not None:
            eligible = [memory for memory in eligible if memory.id > after_id]
        return eligible[:limit]

    async def partial_upsert(user_id, updates, expected_updated_at=None):
        return [updates[0][0]]  # Only the first id is owned

    repo.get_memories_for_targeted_rebuild.side_effect = get_batch
    repo.upsert_targeted_embeddings.side_effect = partial_upsert
    repo.find_similar_memories.return_value = []
    repo.create_links_batch.return_value = []
    adapter = embedding_spy

    service = ReEmbeddingService(repo, adapter, batch_size=20)
    result = await service.rebuild_targeted(user_id=uuid4())

    assert result.rebuilt_ids == [1]
    assert sorted(result.skipped_ids) == [2, 3]
    assert result.failed == []


@pytest.mark.asyncio
async def test_rebuild_targeted_records_embedding_failures(embedding_spy):
    """Per-memory embedding error -> entry in result.failed, others succeed."""
    from uuid import uuid4

    memories = [_make_memory(i) for i in range(1, 4)]
    repo = _make_targeted_repo(memories)
    adapter = embedding_spy

    adapter.generate_embeddings.side_effect = RuntimeError("batch rejected")
    adapter.generate_embedding.side_effect = failing_on(
        adapter.real.generate_embedding, call=2, error=RuntimeError("transient API failure"),
    )

    service = ReEmbeddingService(repo, adapter, batch_size=20)
    result = await service.rebuild_targeted(user_id=uuid4())

    # The failed batch is retried memory by memory, which isolates memory 2
    adapter.generate_embeddings.assert_awaited_once()
    assert adapter.generate_embedding.await_count == 3
    assert sorted(result.rebuilt_ids) == [1, 3]
    assert len(result.failed) == 1
    assert result.failed[0]["memory_id"] == 2
    assert result.failed[0]["reason"] == "embedding_failed"


@pytest.mark.asyncio
async def test_rebuild_targeted_embeds_a_batch_in_one_call(embedding_spy):
    """A batch of memories reaches the provider as one generate_embeddings call."""
    from uuid import uuid4

    memories = [_make_memory(i) for i in range(1, 4)]
    repo = _make_targeted_repo(memories)
    adapter = embedding_spy

    result = await ReEmbeddingService(repo, adapter, batch_size=20).rebuild_targeted(user_id=uuid4())

    adapter.generate_embeddings.assert_awaited_once()
    assert len(adapter.generate_embeddings.call_args[0][0]) == 3
    adapter.generate_embedding.assert_not_called()
    assert sorted(result.rebuilt_ids) == [1, 2, 3]


@pytest.mark.asyncio
async def test_rebuild_targeted_count_mismatch_falls_back_to_single_calls(embedding_spy):
    """A provider that returns the wrong number of vectors does not corrupt the rebuild."""
    from uuid import uuid4

    memories = [_make_memory(i) for i in range(1, 3)]
    repo = _make_targeted_repo(memories)
    adapter = embedding_spy

    async def one_vector_too_few(texts):
        return (await adapter.real.generate_embeddings(texts))[:1]

    adapter.generate_embeddings.side_effect = one_vector_too_few

    result = await ReEmbeddingService(repo, adapter, batch_size=20).rebuild_targeted(user_id=uuid4())

    assert adapter.generate_embedding.await_count == 2
    assert sorted(result.rebuilt_ids) == [1, 2]
    assert result.failed == []


@pytest.mark.asyncio
async def test_rebuild_targeted_recomputes_auto_links(monkeypatch, embedding_spy):
    """After writing fresh vectors, targeted rebuild refreshes additive auto-links."""
    from uuid import uuid4

    user_id = uuid4()
    memories = [_make_memory(i) for i in range(1, 3)]
    repo = _make_targeted_repo(memories)
    repo.find_similar_memories.return_value = [memories[1]]
    repo.create_links_batch.return_value = [memories[1].id]
    adapter = embedding_spy
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 1)

    service = ReEmbeddingService(repo, adapter, batch_size=20)
    result = await service.rebuild_targeted(user_id=user_id)

    assert sorted(result.rebuilt_ids) == [1, 2]
    assert repo.find_similar_memories.call_count == 2
    assert repo.create_links_batch.call_count == 2
    repo.find_similar_memories.assert_any_call(
        user_id=user_id,
        memory_id=1,
        max_links=1,
    )
