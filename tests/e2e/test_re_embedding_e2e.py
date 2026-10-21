"""E2E tests for re-embedding on a real Qdrant node with the real TensorRT embedder.

Tests the full stack: ReEmbeddingService -> QdrantMemoryRepository -> Qdrant collections. Every test
runs on collections of its own (``isolated_stack``), so counts and generations are exact.
"""
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.repositories.qdrant.memory_repository import QdrantStack
from app.repositories.qdrant.store import SYSTEM
from app.services.re_embedding_service import ReEmbeddingService
from tests.embedding_doubles import DelegatingEmbedder, NarrowedEmbedder


@pytest.fixture(scope="module")
def embedding_adapter(tensorrt_embedder):
    """The real TensorRT embedder of the test session (production settings, one engine for all tests)."""
    return tensorrt_embedder


@pytest.fixture
async def repo(isolated_stack, embedding_adapter):
    return isolated_stack.memory_repository(embedding_adapter)


async def _create_test_memories(repo, count=5):
    """Helper to create test memories via the repository"""
    user_id = uuid4()
    memories = []
    for i in range(count):
        memory = await repo.create_memory(
            user_id=user_id,
            memory=MemoryCreate(
                title=f"Test Memory {i}",
                content=f"This is test memory content number {i} about topic {i}",
                context=f"Testing context for memory {i}",
                keywords=[f"keyword{i}", "test"],
                tags=[f"tag{i}", "test"],
                importance=7,
            ),
        )
        memories.append(memory)
    return user_id, memories


@pytest.mark.asyncio
async def test_re_embed_search_works_after(repo, embedding_adapter):
    """Create memories, re-embed, run semantic search, verify results returned"""
    user_id, memories = await _create_test_memories(repo, count=3)

    # Re-embed all memories
    service = ReEmbeddingService(
        memory_repository=repo,
        embedding_adapter=embedding_adapter,
        batch_size=10,
    )
    result = await service.re_embed_all()

    assert result.total_processed == 3
    assert result.validation.all_passed

    # Verify search works after re-embedding
    search_results = await repo.search(
        user_id=user_id,
        query="test memory content",
        query_context="verifying search after re-embed",
        k=3,
        importance_threshold=None,
        project=None,
        exclude_ids=None,
    )
    assert len(search_results) > 0


@pytest.mark.asyncio
async def test_re_embed_count_integrity(repo, embedding_adapter):
    """After re-embed, the new generation holds exactly the memories the old one did"""
    user_id, memories = await _create_test_memories(repo, count=5)

    service = ReEmbeddingService(
        memory_repository=repo,
        embedding_adapter=embedding_adapter,
        batch_size=3,
    )
    result = await service.re_embed_all()

    assert result.total_processed == 5
    assert result.validation.count_ok
    assert await repo.count_all_memories() == 5


@pytest.mark.asyncio
async def test_re_embed_switches_the_live_generation_and_keeps_the_previous_one(repo, embedding_adapter):
    user_id, memories = await _create_test_memories(repo, count=2)
    before = await repo.live_generation()

    result = await ReEmbeddingService(repo, embedding_adapter, batch_size=10).re_embed_all()

    after = await repo.live_generation()
    assert after != before
    assert (result.generation, result.previous_generation) == (after, before)
    # The previous generation still holds the old vectors, ready for a switch back
    assert await repo.store.count(SYSTEM, before) == 2


@pytest.mark.asyncio
async def test_re_embed_preserves_memory_data(repo, embedding_adapter):
    """After re-embed, verify all memory fields (title, content, tags, links, states, ids) unchanged"""
    user_id, original_memories = await _create_test_memories(repo, count=3)
    await repo.create_links_batch(user_id, original_memories[0].id, [original_memories[1].id])
    await repo.mark_obsolete(user_id, original_memories[2].id, "superseded", original_memories[0].id)
    originals = [await repo.get_memory_by_id(user_id, memory.id) for memory in original_memories]

    service = ReEmbeddingService(
        memory_repository=repo,
        embedding_adapter=embedding_adapter,
        batch_size=10,
    )
    await service.re_embed_all()

    # Re-read all memories and verify data unchanged
    for original in originals:
        assert await repo.get_memory_by_id(user_id=user_id, memory_id=original.id) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("obsolete_only", [False, True], ids=["mixed", "all-obsolete"])
async def test_re_embed_recovers_from_vectors_of_another_width(
    isolated_qdrant, embedding_adapter, monkeypatch, obsolete_only,
):
    """The stored vectors come from an older, narrower model: re-embedding replaces them with the model's."""
    # Phase one: the deployment of the old model, whose vectors have 8 components
    with monkeypatch.context() as old_model:
        old_model.setattr(settings, "EMBEDDING_DIMENSIONS", 8)
        old_stack = QdrantStack.from_settings()
        await old_stack.schema.ensure()
        old_repo = old_stack.memory_repository(NarrowedEmbedder(embedding_adapter))
        user_id, memories = await _create_test_memories(old_repo, count=3)
        old = memories[-1]
        await old_repo.mark_obsolete(user_id, old.id, "superseded", memories[0].id)
        if obsolete_only:
            for memory in memories[:-1]:
                await old_repo.mark_obsolete(user_id, memory.id, "outdated")
        original = await old_repo.get_memory_by_id(user_id, old.id)
        await old_stack.close()

    # Phase two: the new model (384 components) runs re-embed; the server would refuse to start first
    stack = QdrantStack.from_settings()
    try:
        with pytest.raises(Exception, match="vectors are 8 wide but 384 are configured"):
            await stack.schema.ensure()
        await stack.schema.ensure(replacing=True)
        repo = stack.memory_repository(embedding_adapter)
        progress = []
        result = await ReEmbeddingService(repo, embedding_adapter, batch_size=2).re_embed_all(
            progress_callback=lambda done, total: progress.append((done, total)),
        )

        assert result.total_memories == result.total_processed == 3
        assert progress == [(2, 3), (3, 3)]
        assert result.validation.all_passed
        await stack.schema.ensure()  # the width matches again
        refreshed = await repo.get_memory_by_id(user_id, old.id)
        preserved_fields = {
            "title", "content", "context", "keywords", "tags", "importance", "created_at",
            "is_obsolete", "obsolete_reason", "superseded_by", "obsoleted_at",
        }
        assert refreshed.model_dump(include=preserved_fields) == original.model_dump(
            include=preserved_fields,
        )
        recreated = await repo.create_memory(user_id, MemoryCreate.model_validate(old.model_dump()))
        matches = await repo.find_obsolete_matches(user_id, recreated.id, limit=3, min_similarity=0.99)
        assert old.id in {memory.id for memory, _ in matches}
    finally:
        await stack.close()


@pytest.mark.asyncio
async def test_re_embed_moves_the_memories_and_the_links_to_the_layout_of_the_settings(
    isolated_qdrant, embedding_adapter, monkeypatch,
):
    """A collection keeps the layout it was made with; re-embedding is how the layout settings are changed afterwards."""
    stack = QdrantStack.from_settings()
    try:
        await stack.schema.ensure()
        repo = stack.memory_repository(embedding_adapter)
        user_id, memories = await _create_test_memories(repo, count=3)
        await repo.create_links_batch(user_id, memories[0].id, [memories[1].id, memories[2].id])
        monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
        with pytest.raises(Exception, match="shard_number is 1 but QDRANT_SHARD_NUMBER is 2"):
            await stack.schema.ensure()
        await stack.schema.ensure(replacing=True)

        result = await ReEmbeddingService(repo, embedding_adapter).re_embed_all()

        assert result.validation.all_passed
        assert result.links_rebuilt
        await stack.schema.ensure()  # the server starts now
        client = stack.pool.clients[0]
        for collection in (await stack.schema.live_generation(), stack.schema.names.links):
            assert (await client.get_collection(collection)).config.params.shard_number == 2
        linked = (await repo.get_memory_by_id(user_id, memories[0].id)).linked_memory_ids
        assert set(linked) == {memories[1].id, memories[2].id}
        assert await repo.create_links_batch(user_id, memories[1].id, [memories[2].id]) == [memories[2].id]
        assert (await repo.create_memory(user_id, MemoryCreate.model_validate(memories[0].model_dump()))).id != memories[0].id
        names = [collection.name for collection in (await client.get_collections()).collections]
        assert [name for name in names if name.endswith("_relayout")] == []
    finally:
        await stack.close()


@pytest.mark.asyncio
async def test_re_embed_with_the_layout_unchanged_leaves_the_links_collection_alone(repo, embedding_adapter, isolated_stack):
    user_id, memories = await _create_test_memories(repo, count=2)
    await repo.create_links_batch(user_id, memories[0].id, [memories[1].id])
    links = isolated_stack.schema.names.links
    created = (await isolated_stack.pool.clients[0].get_collection(links)).config.params.model_dump()

    result = await ReEmbeddingService(repo, embedding_adapter).re_embed_all()

    assert not result.links_rebuilt
    assert (await isolated_stack.pool.clients[0].get_collection(links)).config.params.model_dump() == created


@pytest.mark.asyncio
async def test_re_embed_empty_database(repo, embedding_adapter):
    """Re-embedding an empty database should succeed with no work done"""
    service = ReEmbeddingService(
        memory_repository=repo,
        embedding_adapter=embedding_adapter,
        batch_size=10,
    )
    result = await service.re_embed_all()

    assert result.total_processed == 0
    assert result.total_memories == 0
    assert result.validation.all_passed


@pytest.mark.asyncio
async def test_re_embed_validation_checks(repo, embedding_adapter):
    """Verify all validation checks pass after successful re-embed"""
    await _create_test_memories(repo, count=4)

    service = ReEmbeddingService(
        memory_repository=repo,
        embedding_adapter=embedding_adapter,
        batch_size=2,
    )
    result = await service.re_embed_all()

    assert result.validation.count_ok
    assert result.validation.dimensions_ok
    assert result.validation.search_ok
    assert result.validation.all_passed


@pytest.mark.asyncio
async def test_validation_flags_a_generation_that_lacks_the_memories(repo):
    user_id, memories = await _create_test_memories(repo, count=1)
    await repo.mark_obsolete(user_id, memories[0].id, "outdated")
    empty = await repo.create_generation()

    validation = await repo.validate_generation(empty)

    assert not validation.count_ok
    assert validation.dimensions_ok
    assert not validation.all_passed


@pytest.mark.asyncio
async def test_validation_flags_a_generation_of_the_wrong_width(repo, isolated_stack):
    await _create_test_memories(repo, count=1)
    isolated_stack.schema.dimensions = 8
    narrow = await repo.create_generation()

    validation = await repo.validate_generation(narrow)

    assert not validation.dimensions_ok
    assert not validation.all_passed


class _EditsWhileEmbedding(DelegatingEmbedder):
    """The real embedder, which lets another writer edit a memory in the middle of the copy."""

    def __init__(self, real, edit):
        super().__init__(real)
        self.calls = 0
        self._edit = edit

    async def generate_embedding(self, text: str) -> list[float]:
        self.calls += 1
        if self.calls == 2:
            await self._edit()
        return await super().generate_embedding(text)


@pytest.mark.asyncio
async def test_edit_made_while_the_copy_runs_is_not_lost(repo, embedding_adapter):
    """A memory edited after it was copied is copied again before the new generation goes live."""
    user_id, memories = await _create_test_memories(repo, count=4)
    edited = memories[0]

    async def edit():
        await repo.update_memory(user_id, edited.id, MemoryUpdate(content="edited during the copy"))

    embedder = _EditsWhileEmbedding(embedding_adapter, edit)
    result = await ReEmbeddingService(repo, embedder, batch_size=1).re_embed_all()

    assert result.validation.all_passed
    assert (await repo.get_memory_by_id(user_id, edited.id)).content == "edited during the copy"


# ---------------------------------------------------------------------------
# rebuild_targeted (issue #39) — user-scoped, non-destructive rebuild
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_targeted_is_user_scoped(repo, embedding_adapter):
    """A user can never trigger a rebuild on another user's memories."""
    user_a, memories_a = await _create_test_memories(repo, count=2)
    user_b, memories_b = await _create_test_memories(repo, count=2)

    service = ReEmbeddingService(
        memory_repository=repo,
        embedding_adapter=embedding_adapter,
        batch_size=10,
    )
    # User B passes A's id explicitly: candidate count must be zero.
    result_b = await service.rebuild_targeted(
        user_id=user_b,
        memory_ids=[memories_a[0].id],
    )

    assert result_b.rebuilt_ids == []

    # User A can rebuild their own memories.
    result_a = await service.rebuild_targeted(
        user_id=user_a,
    )
    assert sorted(result_a.rebuilt_ids) == sorted(memory.id for memory in memories_a)


@pytest.mark.asyncio
async def test_rebuild_targeted_never_creates_or_switches_a_generation(repo, embedding_adapter):
    """Targeted rebuild rewrites vectors in place; the live collection stays the same."""
    user_id, memories = await _create_test_memories(repo, count=2)
    live_before = await repo.live_generation()

    result = await ReEmbeddingService(repo, embedding_adapter, batch_size=5).rebuild_targeted(user_id=user_id)

    assert sorted(result.rebuilt_ids) == sorted(memory.id for memory in memories)
    assert await repo.live_generation() == live_before
    assert await repo.count_all_memories() == 2
