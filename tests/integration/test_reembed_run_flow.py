"""Integration tests for the full re-embed run: storage reset and constraints.

Uses mocked repositories (no real database is required) and the real embedder behind call-recording spies.
"""
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.models.memory_models import Memory
from app.services.re_embedding_service import ReEmbeddingService
from tests.embedding_doubles import failing_on


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


def _memory_repo(memories: list[Memory]):
    repo = AsyncMock()
    repo.count_all_memories.return_value = len(memories)

    async def batch(limit: int, offset: int):
        return memories[offset:offset + limit]

    repo.get_memories_for_reembedding.side_effect = batch
    repo.validate_embedding_count.return_value = True
    repo.validate_embedding_dimensions.return_value = True
    repo.validate_search_works.return_value = True
    return repo


async def test_empty_database_still_resets_vector_storage(embedding_spy):
    """A dimension change on a database without memories must still resize the vector storage."""
    memory_repo = _memory_repo([])
    embedder = embedding_spy

    result = await ReEmbeddingService(memory_repo, embedder).re_embed_all()

    memory_repo.reset_embedding_storage.assert_awaited_once()
    memory_repo.bulk_update_embeddings.assert_not_called()
    embedder.generate_embeddings.assert_not_called()
    assert (result.total_memories, result.total_processed) == (0, 0)
    assert result.validation is not None
    assert result.validation.all_passed


async def test_constraints_are_restored_only_after_validation_passes(embedding_spy):
    memory_repo = _memory_repo([_memory(1)])
    calls: list[str] = []

    async def validate():
        calls.append("validate")
        return True

    async def finalize():
        calls.append("finalize")

    memory_repo.validate_search_works.side_effect = validate
    memory_repo.finalize_embedding_storage.side_effect = finalize

    await ReEmbeddingService(memory_repo, embedding_spy).re_embed_all()

    assert calls == ["validate", "finalize"]


async def test_failed_validation_keeps_constraints_relaxed(embedding_spy):
    memory_repo = _memory_repo([_memory(1)])
    memory_repo.validate_embedding_count.return_value = False

    result = await ReEmbeddingService(memory_repo, embedding_spy).re_embed_all()

    assert not result.validation.all_passed
    memory_repo.finalize_embedding_storage.assert_not_called()


async def test_failed_run_never_restores_constraints(embedding_spy):
    memory_repo = _memory_repo([_memory(1), _memory(2)])
    embedder = embedding_spy
    embedder.generate_embeddings.side_effect = failing_on(
        embedder.real.generate_embeddings, call=2, error=RuntimeError("provider down"),
    )

    with pytest.raises(RuntimeError, match="provider down"):
        await ReEmbeddingService(memory_repo, embedder, batch_size=1).re_embed_all()

    memory_repo.finalize_embedding_storage.assert_not_called()


async def test_caller_can_reset_storage_itself(embedding_spy):
    """The CLI resets storage as its own visible step, so the run must not reset it twice."""
    memory_repo = _memory_repo([_memory(1)])
    service = ReEmbeddingService(memory_repo, embedding_spy)

    await service.re_embed_all(reset_storage=False)

    memory_repo.reset_embedding_storage.assert_not_called()

    await service.reset_vector_storage()

    memory_repo.reset_embedding_storage.assert_awaited_once()
