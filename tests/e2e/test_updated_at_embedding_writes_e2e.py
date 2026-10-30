# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests on a real Qdrant node for embedding writes.

Covers:
- A targeted rebuild must not overwrite a vector regenerated for newer content, and its version check is part
  of the write itself.
- `rebuild_embeddings` failures return stable reason codes and blame only the failing memory.
- `rebuild_embeddings(project=...)` rejects unknown or foreign project scopes.

Uses the real TensorRT embedder, wrapped only to hold a call or to return a vector of the wrong width.
"""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastmcp.exceptions import ToolError

from app.config.settings import settings
from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.models.tenant_models import Tenant
from app.repositories.qdrant.store import QdrantStore
from app.routes.mcp.tool_adapters import MemoryToolAdapters
from app.services.memory_service import MemoryService
from app.services.re_embedding_service import ReEmbeddingService
from tests.embedding_doubles import DelegatingEmbedder

DIMS = 384
LEAK_MARKERS = ("/collections", "Unexpected Response", "Raw response", "dim", "qdrant", "b'")


class GatedEmbeddingAdapter(DelegatingEmbedder):
    """The real embedder; can hold calls whose text contains `gate_marker`.

    `wrong_dimension_marker` makes texts containing it return a vector of the wrong length (the
    model's vector plus one component), which the collection rejects at write time, like a
    misconfigured provider would.
    """

    def __init__(self, real) -> None:
        super().__init__(real)
        self.gate_marker: str | None = None
        self.wrong_dimension_marker: str | None = None
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def vector(self, text_: str) -> list[float]:
        """The model's vector for ``text_``, with no gate and no fault."""
        return await self.real.generate_embedding(text_)

    async def generate_embedding(self, text: str) -> list[float]:
        if self.gate_marker and self.gate_marker in text:
            self.reached.set()
            await self.release.wait()
        vector = await super().generate_embedding(text)
        if self.wrong_dimension_marker and self.wrong_dimension_marker in text:
            return [*vector, 0.0]
        return vector


@pytest.fixture
async def stack(qdrant_stack, tensorrt_embedder, raw_qdrant, monkeypatch):
    """(repo, raw Qdrant client, embedder, tenant id): the tenant is the one the tool adapters resolve."""
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", DIMS)
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    embedder = GatedEmbeddingAdapter(tensorrt_embedder)
    repo = qdrant_stack.memory_repository(embedder)
    return repo, raw_qdrant, embedder, Tenant(settings.DEFAULT_TENANT).id


def _memory(content: str, project: str | None = None) -> MemoryCreate:
    return MemoryCreate(
        title="T",
        content=content,
        context="c",
        keywords=["k"],
        tags=["t"],
        importance=5,
        project=project,
    )


async def _vector(raw, memory_id):
    """The stored vector of a memory."""
    (record,) = await raw.retrieve(
        f"{settings.QDRANT_COLLECTION_PREFIX}_memories", ids=[str(memory_id)], with_vectors=True,
    )
    return list(record.vector)


async def _bump_version_behind_the_rebuild(raw, memory_id, vector):
    """What a committed edit leaves: a new vector and the next version."""
    collection = f"{settings.QDRANT_COLLECTION_PREFIX}_memories"
    (record,) = await raw.retrieve(collection, ids=[str(memory_id)])
    await raw.update_vectors(collection, points=[{"id": str(memory_id), "vector": vector}], wait=True)
    await raw.set_payload(
        collection, payload={"version": record.payload["version"] + 1}, points=[str(memory_id)], wait=True,
    )


def _tool_adapters(repo, embedder):
    ctx = SimpleNamespace(fastmcp=SimpleNamespace(auth=None))
    adapters = MemoryToolAdapters(
        memory_service=SimpleNamespace(),
        re_embedding_service=ReEmbeddingService(repo, embedder),
    )
    return ctx, adapters


# ---------------------------------------------------------------------------
# updated_at must not move on derived-data writes
# ---------------------------------------------------------------------------


async def test_targeted_rebuild_does_not_change_updated_at(stack):
    repo, _raw, embedder, user_id = stack
    memory = await repo.create_memory(user_id, _memory("content"))
    before = await repo.get_memory_by_id(user_id, memory.id)

    await asyncio.sleep(0.05)
    result = await ReEmbeddingService(repo, embedder).rebuild_targeted(user_id=user_id)

    assert result.rebuilt_ids == [memory.id]
    assert (await repo.get_memory_by_id(user_id, memory.id)).updated_at == before.updated_at


# ---------------------------------------------------------------------------
# rebuild must not overwrite a vector computed for newer content
# ---------------------------------------------------------------------------


async def test_rebuild_does_not_clobber_embedding_of_concurrent_update(stack):
    repo, raw, embedder, user_id = stack
    memory_service = MemoryService(memory_repo=repo)
    re_service = ReEmbeddingService(repo, embedder, batch_size=20)
    memory, _ = await memory_service.create_memory(user_id=user_id, memory_data=_memory("OLD-CONTENT"))

    # Hold the rebuild inside generate_embedding(OLD text) ...
    embedder.gate_marker = "OLD-CONTENT"
    rebuild = asyncio.create_task(re_service.rebuild_targeted(user_id=user_id, memory_ids=[memory.id]))
    await asyncio.wait_for(embedder.reached.wait(), timeout=10)

    # ... while update_memory commits NEW content together with its fresh vector.
    embedder.gate_marker = None
    updated = await memory_service.update_memory(
        user_id=user_id, memory_id=memory.id, updated_memory=MemoryUpdate(content="NEW-CONTENT"),
    )
    assert updated.content == "NEW-CONTENT"
    new_vector = await embedder.vector("T NEW-CONTENT c k t")
    assert await _vector(raw, memory.id) == pytest.approx(new_vector, abs=1e-6)

    embedder.release.set()
    result = await asyncio.wait_for(rebuild, timeout=10)

    assert await _vector(raw, memory.id) == pytest.approx(new_vector, abs=1e-6)
    assert result.rebuilt_ids == []
    assert result.skipped_ids == [memory.id]
    assert result.failed == []


async def test_upsert_targeted_embeddings_only_writes_memories_unchanged_since_read(stack):
    repo, raw, embedder, user_id = stack
    first = await repo.create_memory(user_id, _memory("first"))
    second = await repo.create_memory(user_id, _memory("second"))
    read = {stored.memory.id: stored for stored in await repo.get_memories_for_targeted_rebuild(user_id, limit=10)}
    original = {first.id: await _vector(raw, first.id), second.id: await _vector(raw, second.id)}
    new_vector = await embedder.vector("refreshed")

    await repo.update_memory(user_id=user_id, memory_id=first.id, updated_memory=MemoryUpdate(content="edited after the read"))

    outcome = await repo.upsert_targeted_embeddings(
        user_id=user_id,
        updates=[(first.id, new_vector), (second.id, new_vector)],
        expected_versions={first.id: read[first.id].version, second.id: read[second.id].version},
    )

    assert outcome.written == [second.id]
    assert outcome.failed == []
    assert await _vector(raw, first.id) != pytest.approx(new_vector, abs=1e-6)
    assert await _vector(raw, first.id) != pytest.approx(original[first.id], abs=1e-6)  # the edit's own vector
    assert await _vector(raw, second.id) == pytest.approx(new_vector, abs=1e-6)


async def test_upsert_targeted_embeddings_without_an_expected_version_writes_nothing(stack):
    repo, raw, embedder, user_id = stack
    memory = await repo.create_memory(user_id, _memory("content"))
    before = await _vector(raw, memory.id)

    outcome = await repo.upsert_targeted_embeddings(
        user_id=user_id, updates=[(memory.id, await embedder.vector("refreshed"))], expected_versions={},
    )

    assert (outcome.written, outcome.failed) == ([], [])
    assert await _vector(raw, memory.id) == pytest.approx(before, abs=1e-9)


async def test_upsert_targeted_embeddings_version_check_is_atomic_with_the_write(stack, monkeypatch):
    """An edit committed after the version check but before the vector write must not be overwritten.

    The edit is injected right before the conditional write is sent, that is between the check done on
    the read copy and the write. The version guard is part of the write itself, so Qdrant applies nothing,
    and the repository reads the claim token back to learn that its write did not win.
    """
    repo, raw, embedder, user_id = stack
    memory = await repo.create_memory(user_id, _memory("OLD-CONTENT"))
    (read,) = await repo.get_memories_for_targeted_rebuild(user_id, limit=10)
    stale_vector = await embedder.vector("computed from OLD-CONTENT")
    fresh_vector = await embedder.vector("computed from NEW-CONTENT")

    real_replace = QdrantStore.replace
    competing = []

    async def edit_then_replace(self, *args, **kwargs):
        if not competing:
            competing.append(True)
            await _bump_version_behind_the_rebuild(raw, memory.id, fresh_vector)
        return await real_replace(self, *args, **kwargs)

    monkeypatch.setattr(QdrantStore, "replace", edit_then_replace)

    outcome = await repo.upsert_targeted_embeddings(
        user_id=user_id,
        updates=[(memory.id, stale_vector)],
        expected_versions={memory.id: read.version},
    )

    assert competing == [True]
    assert (outcome.written, outcome.failed) == ([], [])
    assert await _vector(raw, memory.id) == pytest.approx(fresh_vector, abs=1e-6)


async def test_upsert_targeted_embeddings_skips_memory_edited_by_another_writer(stack):
    repo, raw, embedder, user_id = stack
    memory = await repo.create_memory(user_id, _memory("OLD-CONTENT"))
    (read,) = await repo.get_memories_for_targeted_rebuild(user_id, limit=10)
    fresh_vector = await embedder.vector("computed from NEW-CONTENT")
    await _bump_version_behind_the_rebuild(raw, memory.id, fresh_vector)

    outcome = await repo.upsert_targeted_embeddings(
        user_id=user_id,
        updates=[(memory.id, await embedder.vector("computed from OLD-CONTENT"))],
        expected_versions={memory.id: read.version},
    )

    assert outcome.written == []
    assert await _vector(raw, memory.id) == pytest.approx(fresh_vector, abs=1e-6)


# ---------------------------------------------------------------------------
# failure reporting
# ---------------------------------------------------------------------------


async def test_failed_reasons_are_stable_codes_without_request_text_or_vector_bytes(stack):
    repo, _raw, embedder, user_id = stack
    for index in range(3):
        await repo.create_memory(user_id, _memory(f"content {index}"))
    embedder.wrong_dimension_marker = "content"

    ctx, adapters = _tool_adapters(repo, embedder)
    out = await adapters.rebuild_embeddings(ctx)

    assert out["rebuilt_ids"] == []
    assert len(out["failed"]) == 3
    for failure in out["failed"]:
        assert failure["reason"] == "persistence_failed"
        for marker in LEAK_MARKERS:
            assert marker not in failure["reason"]


async def test_batch_write_failure_blames_only_the_failing_memory(stack):
    repo, raw, embedder, user_id = stack
    memories = [await repo.create_memory(user_id, _memory(f"content {index}")) for index in range(3)]
    embedder.wrong_dimension_marker = "content 1"
    original = {memory.id: await _vector(raw, memory.id) for memory in memories}

    result = await ReEmbeddingService(repo, embedder, batch_size=10).rebuild_targeted(user_id=user_id)

    assert result.failed == [{"memory_id": memories[1].id, "reason": "persistence_failed"}]
    assert sorted(result.rebuilt_ids) == sorted([memories[0].id, memories[2].id])
    assert result.skipped_ids == []
    assert await _vector(raw, memories[1].id) == pytest.approx(original[memories[1].id], abs=1e-9)
    for index in (0, 2):
        expected = await embedder.vector(f"T content {index} c k t")
        assert await _vector(raw, memories[index].id) == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# project scope validation
# ---------------------------------------------------------------------------


async def test_rebuild_embeddings_rejects_nonexistent_project(stack):
    repo, _raw, embedder, user_id = stack
    await repo.create_memory(user_id, _memory("content", "known"))
    ctx, adapters = _tool_adapters(repo, embedder)

    with pytest.raises(ToolError, match=r"VALIDATION_ERROR: project='missing' not found"):
        await adapters.rebuild_embeddings(ctx, project="missing")


async def test_rebuild_embeddings_rejects_foreign_project_without_rebuilding(stack):
    repo, raw, embedder, _user_id = stack
    owner_memory = await repo.create_memory(uuid4(), _memory("owner content", "owner project"))
    before = await _vector(raw, owner_memory.id)
    ctx, adapters = _tool_adapters(repo, embedder)

    with pytest.raises(ToolError, match=r"project='owner project' not found"):
        await adapters.rebuild_embeddings(ctx, project="owner project")

    assert await _vector(raw, owner_memory.id) == pytest.approx(before, abs=1e-9)


async def test_rebuild_embeddings_accepts_an_owned_project_scope(stack):
    repo, _raw, embedder, user_id = stack
    memory = await repo.create_memory(user_id, _memory("content", "used"))
    await repo.create_memory(user_id, _memory("elsewhere", "other"))
    ctx, adapters = _tool_adapters(repo, embedder)

    used = await adapters.rebuild_embeddings(ctx, project="  used ")

    assert used == {"rebuilt_ids": [memory.id], "skipped_ids": [], "failed": []}
