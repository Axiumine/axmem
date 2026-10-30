# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""Integration tests for the targeted, tenant-scoped rebuild on a real Qdrant node.

Unlike ``tests/e2e/test_re_embedding_e2e.py``, this module drives the repository and the re-embedding
service directly, with the real TensorRT embedder, to exercise the persistence layer of the
``rebuild_embeddings`` MCP tool.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastmcp.exceptions import ToolError

from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.models.tenant_models import Tenant
from app.routes.mcp.tool_adapters import MemoryToolAdapters
from app.services.re_embedding_service import ReEmbeddingService


@pytest.fixture
async def stack(memory_repository, tensorrt_embedder, raw_qdrant, monkeypatch):
    """(repository, embedder, raw Qdrant client): auto-linking is off unless a test turns it on."""
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    return memory_repository, tensorrt_embedder, raw_qdrant


def _make_ctx():
    return SimpleNamespace(fastmcp=SimpleNamespace(auth=None, trusted_local=True))


async def _seed_memories(repo, count=3, user_id=None):
    user_id = user_id or uuid4()
    memories = [
        await repo.create_memory(
            user_id=user_id,
            memory=MemoryCreate(
                title=f"M{i}",
                content=f"content {i}",
                context=f"ctx {i}",
                keywords=[f"k{i}"],
                tags=[f"t{i}"],
                importance=5,
            ),
        )
        for i in range(count)
    ]
    return user_id, memories


async def _vector(raw, memory_id):
    (record,) = await raw.retrieve(
        f"{settings.QDRANT_COLLECTION_PREFIX}_memories", ids=[str(memory_id)], with_vectors=True,
    )
    return list(record.vector)


async def _set_marker_vector(raw, memory_id):
    """Overwrite the stored vector with one no embedder would produce."""
    marker = [1.0] + [0.0] * (settings.EMBEDDING_DIMENSIONS - 1)
    await raw.update_vectors(
        f"{settings.QDRANT_COLLECTION_PREFIX}_memories", points=[{"id": str(memory_id), "vector": marker}], wait=True,
    )
    return marker


async def _versions(repo, user_id):
    return {stored.memory.id: stored.version for stored in await repo.get_memories_for_targeted_rebuild(user_id, limit=100)}


async def test_rebuild_targeted_is_user_scoped(stack):
    repo, embedder, raw = stack
    user_a, memories_a = await _seed_memories(repo, count=2)
    user_b, _memories_b = await _seed_memories(repo, count=2)
    target = memories_a[0]
    marker = await _set_marker_vector(raw, target.id)
    service = ReEmbeddingService(memory_repository=repo, embedding_adapter=embedder, batch_size=10)

    result_b = await service.rebuild_targeted(user_id=user_b, memory_ids=[target.id])

    assert result_b.rebuilt_ids == []
    assert await _vector(raw, target.id) == pytest.approx(marker)

    result_a = await service.rebuild_targeted(user_id=user_a)
    assert sorted(result_a.rebuilt_ids) == sorted(memory.id for memory in memories_a)
    assert await _vector(raw, target.id) != pytest.approx(marker)


async def test_rebuild_targeted_never_creates_or_switches_a_generation(stack):
    repo, embedder, _raw = stack
    user_id, _memories = await _seed_memories(repo, count=2)
    generation = await repo.live_generation()
    total = await repo.count_all_memories()

    await ReEmbeddingService(memory_repository=repo, embedding_adapter=embedder, batch_size=5).rebuild_targeted(user_id=user_id)

    assert await repo.live_generation() == generation
    assert await repo.count_all_memories() == total


async def test_upsert_targeted_embeddings_rejects_unowned_ids(stack):
    repo, embedder, _raw = stack
    user_a, memories_a = await _seed_memories(repo, count=1)
    _user_b, memories_b = await _seed_memories(repo, count=1)
    vector = await embedder.generate_embedding("anything")
    versions = await _versions(repo, user_a) | await _versions(repo, _user_b)

    foreign = await repo.upsert_targeted_embeddings(
        user_id=user_a, updates=[(memories_b[0].id, vector)], expected_versions=versions,
    )
    own = await repo.upsert_targeted_embeddings(
        user_id=user_a, updates=[(memories_a[0].id, vector)], expected_versions=versions,
    )

    assert foreign.written == []
    assert own.written == [memories_a[0].id]


async def test_rebuild_targeted_recomputes_auto_links(stack, monkeypatch):
    repo, embedder, _raw = stack
    user_id, memories = await _seed_memories(repo, count=2)
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 1)
    find_calls = []
    create_calls = []
    original_find = repo.find_similar_memories
    original_create = repo.create_links_batch

    async def tracked_find_similar_memories(*args, **kwargs):
        find_calls.append((args, kwargs))
        return await original_find(*args, **kwargs)

    async def tracked_create_links_batch(*args, **kwargs):
        create_calls.append((args, kwargs))
        return await original_create(*args, **kwargs)

    monkeypatch.setattr(repo, "find_similar_memories", tracked_find_similar_memories)
    monkeypatch.setattr(repo, "create_links_batch", tracked_create_links_batch)

    result = await ReEmbeddingService(
        memory_repository=repo, embedding_adapter=embedder, batch_size=10,
    ).rebuild_targeted(user_id=user_id, memory_ids=[memories[0].id])

    assert result.rebuilt_ids == [memories[0].id]
    assert len(find_calls) == 1
    assert find_calls[0][1]["memory_id"] == memories[0].id
    assert create_calls


async def test_rebuild_targeted_multibatch_rebuilds_all_explicit_ids(stack):
    repo, embedder, _raw = stack
    user_id, memories = await _seed_memories(repo, count=5)

    result = await ReEmbeddingService(
        memory_repository=repo, embedding_adapter=embedder, batch_size=2,
    ).rebuild_targeted(user_id=user_id, memory_ids=[memory.id for memory in memories])

    assert sorted(result.rebuilt_ids) == sorted(memory.id for memory in memories)
    assert result.failed == []


async def test_upsert_targeted_embeddings_leaves_foreign_vector_unchanged(stack):
    repo, embedder, raw = stack
    user_a, _memories_a = await _seed_memories(repo, count=1)
    user_b, memories_b = await _seed_memories(repo, count=1)
    foreign = memories_b[0]
    original = await _vector(raw, foreign.id)
    versions = await _versions(repo, user_b)

    vector = await embedder.generate_embedding("foreign overwrite attempt")
    outcome = await repo.upsert_targeted_embeddings(
        user_id=user_a, updates=[(foreign.id, vector)], expected_versions=versions,
    )

    assert outcome.written == []
    assert await _vector(raw, foreign.id) == pytest.approx(original)


async def test_rebuild_targeted_recomputes_links_after_upsert(stack, monkeypatch):
    repo, embedder, raw = stack
    user_id, memories = await _seed_memories(repo, count=2)
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 1)
    order = []
    original_upsert = repo.upsert_targeted_embeddings
    original_find = repo.find_similar_memories
    marker = await _set_marker_vector(raw, memories[0].id)

    async def tracked_upsert(*args, **kwargs):
        order.append("upsert")
        return await original_upsert(*args, **kwargs)

    async def tracked_find(*args, **kwargs):
        order.append("find")
        assert order[-2] == "upsert"
        # The similarity lookup reads the vector the rebuild has just written
        assert await _vector(raw, kwargs["memory_id"]) != pytest.approx(marker)
        return await original_find(*args, **kwargs)

    monkeypatch.setattr(repo, "upsert_targeted_embeddings", tracked_upsert)
    monkeypatch.setattr(repo, "find_similar_memories", tracked_find)

    result = await ReEmbeddingService(
        memory_repository=repo, embedding_adapter=embedder, batch_size=10,
    ).rebuild_targeted(user_id=user_id, memory_ids=[memories[0].id])

    assert result.rebuilt_ids == [memories[0].id]
    assert order == ["upsert", "find"]


async def test_rebuild_embeddings_rejects_empty_memory_ids(stack):
    repo, embedder, _raw = stack
    adapters = MemoryToolAdapters(
        memory_service=SimpleNamespace(), re_embedding_service=ReEmbeddingService(repo, embedder),
    )

    with pytest.raises(ToolError, match="memory_ids must be non-empty"):
        await adapters.rebuild_embeddings(ctx=_make_ctx(), memory_ids=[])


async def test_rebuild_embeddings_rejects_mismatched_memory_ids_and_project(stack):
    repo, embedder, _raw = stack
    memory = await repo.create_memory(
        user_id=Tenant(settings.DEFAULT_TENANT).id,
        memory=MemoryCreate(
            title="Scoped memory", content="content", context="ctx", keywords=["k"], tags=["t"],
            importance=5, project="A",
        ),
    )
    adapters = MemoryToolAdapters(
        memory_service=SimpleNamespace(), re_embedding_service=ReEmbeddingService(repo, embedder),
    )

    with pytest.raises(ToolError, match="do not all belong"):
        await adapters.rebuild_embeddings(ctx=_make_ctx(), memory_ids=[str(memory.id)], project="B")


async def test_rebuild_targeted_records_unresolved_memory_ids_in_failed(stack):
    repo, embedder, _raw = stack
    user_a, memories_a = await _seed_memories(repo, count=1)
    _user_b, memories_b = await _seed_memories(repo, count=1)

    result = await ReEmbeddingService(
        memory_repository=repo, embedding_adapter=embedder, batch_size=10,
    ).rebuild_targeted(user_id=user_a, memory_ids=[memories_a[0].id, memories_b[0].id])

    assert result.rebuilt_ids == [memories_a[0].id]
    assert result.failed == [{"memory_id": memories_b[0].id, "reason": "not_found_or_not_owned"}]
