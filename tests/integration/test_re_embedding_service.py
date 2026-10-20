"""Integration tests for ReEmbeddingService on a real Qdrant node.

The repository is the production one; the embedder is the real TensorRT model behind call-recording
spies (``embedding_spy``), through which the provider faults are injected. The full re-embedding changes
the live generation, so it runs on collections of its own (``isolated_stack``); the tenant-scoped rebuild
works on the shared ones.
"""
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.exceptions import EmbeddingUnavailableError
from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.protocols.memory_protocol import ValidationResult
from app.services.re_embedding_service import MAX_CATCH_UP_PASSES, ReEmbeddingService
from tests.embedding_doubles import failing_on


def _memory(index: int, project: str | None = None) -> MemoryCreate:
    return MemoryCreate(
        title=f"Memory {index}",
        content=f"Content number {index}",
        context="test context",
        keywords=["test"],
        tags=["test"],
        importance=7,
        project=project,
    )


async def _seed(repo, count: int, user_id=None, project: str | None = None):
    user_id = user_id or uuid4()
    return user_id, [await repo.create_memory(user_id, _memory(i, project)) for i in range(count)]


@pytest.fixture
async def isolated_repo(isolated_stack, tensorrt_embedder, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    return isolated_stack.memory_repository(tensorrt_embedder)


@pytest.fixture
async def shared_repo(memory_repository, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    return memory_repository


async def _generations(isolated_stack) -> list[str]:
    """Names of the memory collections that exist under this test's prefix."""
    client = isolated_stack.pool.clients[0]
    names = [collection.name for collection in (await client.get_collections()).collections]
    return sorted(name for name in names if name.startswith(f"{settings.QDRANT_COLLECTION_PREFIX}_memories_"))


# ---------------------------------------------------------------------------
# re_embed_all: a new generation, filled and switched
# ---------------------------------------------------------------------------


async def test_re_embed_processes_all_memories_into_a_new_live_generation(isolated_repo, embedding_spy):
    await _seed(isolated_repo, 5)
    before = await isolated_repo.live_generation()

    result = await ReEmbeddingService(isolated_repo, embedding_spy, batch_size=20).re_embed_all()

    assert (result.total_processed, result.total_memories) == (5, 5)
    # One provider call carries the whole batch of five memories
    embedding_spy.generate_embeddings.assert_awaited_once()
    assert len(embedding_spy.generate_embeddings.call_args[0][0]) == 5
    embedding_spy.generate_embedding.assert_not_called()
    assert result.previous_generation == before
    assert result.generation == await isolated_repo.live_generation() != before
    assert result.validation.all_passed
    assert await isolated_repo.count_all_memories() == 5


async def test_re_embed_respects_batch_size(isolated_repo, embedding_spy):
    await _seed(isolated_repo, 20)

    result = await ReEmbeddingService(isolated_repo, embedding_spy, batch_size=5).re_embed_all()

    assert result.total_processed == 20
    assert embedding_spy.generate_embeddings.call_count == 4
    assert sum(len(call[0][0]) for call in embedding_spy.generate_embeddings.call_args_list) == 20


async def test_re_embed_partial_batch(isolated_repo, embedding_spy):
    await _seed(isolated_repo, 7)

    result = await ReEmbeddingService(isolated_repo, embedding_spy, batch_size=5).re_embed_all()

    assert result.total_processed == 7
    assert [len(call[0][0]) for call in embedding_spy.generate_embeddings.call_args_list] == [5, 2]


async def test_re_embed_empty_database_still_switches_to_a_fresh_generation(isolated_repo, embedding_spy):
    before = await isolated_repo.live_generation()

    result = await ReEmbeddingService(isolated_repo, embedding_spy, batch_size=20).re_embed_all()

    assert (result.total_processed, result.total_memories) == (0, 0)
    assert result.validation.all_passed
    assert result.generation != before
    embedding_spy.generate_embeddings.assert_not_called()


async def test_re_embed_progress_callback_fires_per_batch(isolated_repo, embedding_spy):
    await _seed(isolated_repo, 7)
    progress = []

    result = await ReEmbeddingService(isolated_repo, embedding_spy, batch_size=3).re_embed_all(
        progress_callback=lambda processed, total: progress.append((processed, total)),
    )

    assert result.total_processed == 7
    assert progress == [(3, 7), (6, 7), (7, 7)]


async def test_re_embed_counts_what_it_copied_when_memories_vanish_after_the_count(isolated_repo, embedding_spy, monkeypatch):
    await _seed(isolated_repo, 2)

    async def stale_count():
        return 5

    monkeypatch.setattr(isolated_repo, "count_all_memories", stale_count)

    result = await ReEmbeddingService(isolated_repo, embedding_spy, batch_size=2).re_embed_all()

    assert (result.total_memories, result.total_processed) == (5, 2)


async def test_re_embed_adapter_error_propagates_and_leaves_the_live_generation_alone(
    isolated_repo, isolated_stack, embedding_spy,
):
    await _seed(isolated_repo, 1)
    live = await isolated_repo.live_generation()
    embedding_spy.generate_embeddings.side_effect = RuntimeError("API rate limit exceeded")
    service = ReEmbeddingService(isolated_repo, embedding_spy, batch_size=20)

    with pytest.raises(RuntimeError, match="API rate limit exceeded"):
        await service.re_embed_all()

    assert await isolated_repo.live_generation() == live
    assert await _generations(isolated_stack) == [live]
    assert service.switch_started is None  # the caller may say that the new collection was discarded


async def test_re_embed_failure_in_a_later_batch_discards_the_partial_generation(
    isolated_repo, isolated_stack, embedding_spy,
):
    await _seed(isolated_repo, 2)
    live = await isolated_repo.live_generation()
    embedding_spy.generate_embeddings.side_effect = failing_on(
        embedding_spy.real.generate_embeddings, call=2, error=RuntimeError("provider down"),
    )

    with pytest.raises(RuntimeError, match="provider down"):
        await ReEmbeddingService(isolated_repo, embedding_spy, batch_size=1).re_embed_all()

    assert await isolated_repo.live_generation() == live
    assert await _generations(isolated_stack) == [live]


async def test_generation_is_activated_only_after_validation_passes(isolated_repo, embedding_spy, monkeypatch):
    await _seed(isolated_repo, 1)
    calls: list[str] = []
    validate, activate = isolated_repo.validate_generation, isolated_repo.activate_generation

    async def tracked_validate(generation):
        calls.append("validate")
        return await validate(generation)

    async def tracked_activate(generation):
        calls.append("activate")
        await activate(generation)

    monkeypatch.setattr(isolated_repo, "validate_generation", tracked_validate)
    monkeypatch.setattr(isolated_repo, "activate_generation", tracked_activate)

    await ReEmbeddingService(isolated_repo, embedding_spy).re_embed_all()

    assert calls == ["validate", "activate"]


async def test_a_failure_in_the_catch_up_after_the_switch_is_recorded_as_a_switch(isolated_repo, embedding_spy, monkeypatch):
    await _seed(isolated_repo, 1)
    service = ReEmbeddingService(isolated_repo, embedding_spy)
    activated: list[str] = []
    activate, original_copy = isolated_repo.activate_generation, service._copy_all

    async def tracked_activate(generation):
        activated.append(generation)
        await activate(generation)

    async def copy_all(*args, **kwargs):
        if activated:
            raise RuntimeError("provider down")
        return await original_copy(*args, **kwargs)

    monkeypatch.setattr(isolated_repo, "activate_generation", tracked_activate)
    monkeypatch.setattr(service, "_copy_all", copy_all)

    with pytest.raises(RuntimeError, match="provider down"):
        await service.re_embed_all()

    assert service.switch_started == activated[0] == await isolated_repo.live_generation()


async def test_failed_validation_discards_the_new_generation(isolated_repo, isolated_stack, embedding_spy, monkeypatch):
    await _seed(isolated_repo, 1)
    live = await isolated_repo.live_generation()

    async def failing_validation(generation):
        return ValidationResult(count_ok=False, dimensions_ok=True, search_ok=True)

    monkeypatch.setattr(isolated_repo, "validate_generation", failing_validation)

    result = await ReEmbeddingService(isolated_repo, embedding_spy).re_embed_all()

    assert not result.validation.all_passed
    assert not result.validation.count_ok
    assert result.validation.dimensions_ok
    assert result.generation is None
    assert await isolated_repo.live_generation() == live
    assert await _generations(isolated_stack) == [live]


async def test_catching_up_with_edits_is_bounded_when_writers_never_stop(isolated_repo, embedding_spy, monkeypatch):
    await _seed(isolated_repo, 2)
    service = ReEmbeddingService(isolated_repo, embedding_spy)
    scans: list[int] = []
    original = service._copy_all

    async def a_writer_is_always_ahead(source, target, progress_callback=None, total=0):
        copied = await original(source, target, progress_callback, total)
        scans.append(copied)
        return copied or 1

    monkeypatch.setattr(service, "_copy_all", a_writer_is_always_ahead)

    result = await service.re_embed_all()

    assert result.validation.all_passed
    assert result.generation == await isolated_repo.live_generation()
    # the copy itself, then the catch-up passes before the switch and those after it, each series bounded
    assert len(scans) == 1 + 2 * MAX_CATCH_UP_PASSES


def test_batch_size_must_be_positive(isolated_repo, embedding_spy):
    with pytest.raises(ValueError, match="batch_size must be at least 1"):
        ReEmbeddingService(isolated_repo, embedding_spy, batch_size=0)


# ---------------------------------------------------------------------------
# rebuild_targeted: tenant-scoped, non-destructive
# ---------------------------------------------------------------------------


async def test_rebuild_targeted_rebuilds_every_memory_of_the_tenant(shared_repo, embedding_spy):
    user_id, memories = await _seed(shared_repo, 3)

    result = await ReEmbeddingService(shared_repo, embedding_spy, batch_size=20).rebuild_targeted(user_id=user_id)

    assert sorted(result.rebuilt_ids) == sorted(memory.id for memory in memories)
    assert result.skipped_ids == []
    assert result.failed == []


async def test_rebuild_targeted_no_candidates_short_circuits(shared_repo, embedding_spy, monkeypatch):
    upserts = []
    original = shared_repo.upsert_targeted_embeddings

    async def tracked(*args, **kwargs):
        upserts.append(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(shared_repo, "upsert_targeted_embeddings", tracked)

    result = await ReEmbeddingService(shared_repo, embedding_spy, batch_size=20).rebuild_targeted(user_id=uuid4())

    assert result.rebuilt_ids == []
    embedding_spy.generate_embeddings.assert_not_called()
    embedding_spy.generate_embedding.assert_not_called()
    assert upserts == []


async def test_rebuild_targeted_skips_a_memory_edited_while_it_was_embedded(shared_repo, embedding_spy):
    user_id, memories = await _seed(shared_repo, 3)
    edited = memories[1]
    real = embedding_spy.real.generate_embeddings

    async def edit_during_embedding(texts):
        vectors = await real(texts)
        await shared_repo.update_memory(user_id, edited.id, MemoryUpdate(importance=9))
        return vectors

    embedding_spy.generate_embeddings.side_effect = edit_during_embedding

    result = await ReEmbeddingService(shared_repo, embedding_spy, batch_size=20).rebuild_targeted(user_id=user_id)

    assert sorted(result.rebuilt_ids) == sorted([memories[0].id, memories[2].id])
    assert result.skipped_ids == [edited.id]
    assert result.failed == []


async def test_rebuild_targeted_records_embedding_failures(shared_repo, embedding_spy):
    user_id, memories = await _seed(shared_repo, 3)
    embedding_spy.generate_embeddings.side_effect = RuntimeError("batch rejected")
    embedding_spy.generate_embedding.side_effect = failing_on(
        embedding_spy.real.generate_embedding, call=2, error=RuntimeError("transient API failure"),
    )

    result = await ReEmbeddingService(shared_repo, embedding_spy, batch_size=20).rebuild_targeted(user_id=user_id)

    # The failed batch is retried memory by memory, which isolates the second one
    embedding_spy.generate_embeddings.assert_awaited_once()
    assert embedding_spy.generate_embedding.await_count == 3
    failed_id = result.failed[0]["memory_id"]
    assert [failure["reason"] for failure in result.failed] == ["embedding_failed"]
    assert sorted(result.rebuilt_ids + [failed_id]) == sorted(memory.id for memory in memories)


async def test_rebuild_targeted_does_not_retry_one_by_one_when_the_provider_is_unavailable(shared_repo, embedding_spy):
    user_id, memories = await _seed(shared_repo, 3)
    embedding_spy.generate_embeddings.side_effect = EmbeddingUnavailableError("budget exceeded")

    result = await ReEmbeddingService(shared_repo, embedding_spy, batch_size=20).rebuild_targeted(user_id=user_id)

    embedding_spy.generate_embeddings.assert_awaited_once()
    embedding_spy.generate_embedding.assert_not_called()  # 20 more identical requests would only repeat the refusal
    assert result.rebuilt_ids == []
    assert sorted(failure["memory_id"] for failure in result.failed) == sorted(memory.id for memory in memories)
    assert {failure["reason"] for failure in result.failed} == {"embedding_failed"}


async def test_rebuild_targeted_embeds_a_batch_in_one_call(shared_repo, embedding_spy):
    user_id, memories = await _seed(shared_repo, 3)

    result = await ReEmbeddingService(shared_repo, embedding_spy, batch_size=20).rebuild_targeted(user_id=user_id)

    embedding_spy.generate_embeddings.assert_awaited_once()
    assert len(embedding_spy.generate_embeddings.call_args[0][0]) == 3
    embedding_spy.generate_embedding.assert_not_called()
    assert len(result.rebuilt_ids) == 3


async def test_rebuild_targeted_count_mismatch_falls_back_to_single_calls(shared_repo, embedding_spy):
    user_id, _memories = await _seed(shared_repo, 2)

    async def one_vector_too_few(texts):
        return (await embedding_spy.real.generate_embeddings(texts))[:1]

    embedding_spy.generate_embeddings.side_effect = one_vector_too_few

    result = await ReEmbeddingService(shared_repo, embedding_spy, batch_size=20).rebuild_targeted(user_id=user_id)

    assert embedding_spy.generate_embedding.await_count == 2
    assert len(result.rebuilt_ids) == 2
    assert result.failed == []


async def test_rebuild_targeted_reports_progress_after_every_batch(shared_repo, embedding_spy):
    user_id, _memories = await _seed(shared_repo, 3)
    progress: list[tuple[int, int]] = []

    result = await ReEmbeddingService(shared_repo, embedding_spy, batch_size=2).rebuild_targeted(
        user_id=user_id, progress_callback=lambda done, total: progress.append((done, total)),
    )

    assert progress == [(2, 3), (3, 3)]
    assert len(result.rebuilt_ids) == 3


async def test_rebuild_targeted_writes_nothing_when_no_memory_could_be_embedded(shared_repo, embedding_spy, monkeypatch):
    user_id, memories = await _seed(shared_repo, 2)
    embedding_spy.generate_embeddings.side_effect = RuntimeError("embedding provider down")
    embedding_spy.generate_embedding.side_effect = RuntimeError("embedding provider down")
    upserts = []
    original = shared_repo.upsert_targeted_embeddings

    async def tracked(*args, **kwargs):
        upserts.append(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(shared_repo, "upsert_targeted_embeddings", tracked)

    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(user_id=user_id)

    assert result.rebuilt_ids == []
    assert sorted(failure["memory_id"] for failure in result.failed) == sorted(memory.id for memory in memories)
    assert {failure["reason"] for failure in result.failed} == {"embedding_failed"}
    assert upserts == []


async def test_rebuild_targeted_attributes_a_failed_write_to_that_memory(shared_repo, embedding_spy):
    user_id, memories = await _seed(shared_repo, 2)

    async def one_vector_of_the_wrong_width(texts):
        vectors = await embedding_spy.real.generate_embeddings(texts)
        vectors[0] = [*vectors[0], 0.0]
        return vectors

    embedding_spy.generate_embeddings.side_effect = one_vector_of_the_wrong_width

    result = await ReEmbeddingService(shared_repo, embedding_spy, batch_size=20).rebuild_targeted(user_id=user_id)

    assert [failure["reason"] for failure in result.failed] == ["persistence_failed"]
    assert result.failed[0]["memory_id"] not in result.rebuilt_ids
    assert len(result.rebuilt_ids) == 1


async def test_rebuild_targeted_records_an_auto_link_failure_without_losing_the_rebuild(shared_repo, embedding_spy, monkeypatch):
    user_id, memories = await _seed(shared_repo, 1)
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 1)

    async def broken(*args, **kwargs):
        raise RuntimeError("similarity lookup failed")

    monkeypatch.setattr(shared_repo, "find_similar_memories", broken)

    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(user_id=user_id)

    assert result.rebuilt_ids == [memories[0].id]
    assert result.failed == [{"memory_id": memories[0].id, "phase": "auto_link", "reason": "auto_link_failed"}]


async def test_rebuild_targeted_skips_auto_link_when_the_memory_has_no_similar_one(shared_repo, embedding_spy, monkeypatch):
    user_id, memories = await _seed(shared_repo, 1)
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 1)
    links = []

    async def none_similar(*args, **kwargs):
        return []

    async def tracked_links(*args, **kwargs):
        links.append(kwargs)

    monkeypatch.setattr(shared_repo, "find_similar_memories", none_similar)
    monkeypatch.setattr(shared_repo, "create_links_batch", tracked_links)

    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(user_id=user_id)

    assert result.rebuilt_ids == [memories[0].id]
    assert links == []


# ---------------------------------------------------------------------------
# validate_targeted_scope
# ---------------------------------------------------------------------------


async def test_targeted_scope_accepts_memories_that_all_belong_to_the_project(shared_repo, embedding_spy):
    user_id, memories = await _seed(shared_repo, 2, project="atlas")

    assert await ReEmbeddingService(shared_repo, embedding_spy).validate_targeted_scope(
        user_id, [memory.id for memory in memories], project="atlas",
    ) is None


async def test_targeted_scope_rejects_a_memory_outside_the_project(shared_repo, embedding_spy):
    user_id, inside = await _seed(shared_repo, 2, project="atlas")
    outside = await shared_repo.create_memory(user_id, _memory(9, project="other"))
    ids = [memory.id for memory in inside] + [outside.id]

    with pytest.raises(ValueError, match=r"memory_ids do not all belong to project='atlas'.*matched_in_project=2"):
        await ReEmbeddingService(shared_repo, embedding_spy).validate_targeted_scope(user_id, ids, project="atlas")


async def test_targeted_scope_rejects_empty_memory_ids(shared_repo, embedding_spy):
    with pytest.raises(ValueError, match="memory_ids must be non-empty"):
        await ReEmbeddingService(shared_repo, embedding_spy).validate_targeted_scope(uuid4(), [], project=None)


async def test_targeted_scope_checks_that_a_project_without_ids_exists(shared_repo, embedding_spy):
    user_id, _memories = await _seed(shared_repo, 1, project="atlas")
    service = ReEmbeddingService(shared_repo, embedding_spy)

    assert await service.validate_targeted_scope(user_id, None, project="atlas") is None
    with pytest.raises(ValueError, match="project='missing' not found"):
        await service.validate_targeted_scope(user_id, None, project="missing")


async def test_targeted_scope_with_ids_and_no_project_needs_no_check(shared_repo, embedding_spy):
    assert await ReEmbeddingService(shared_repo, embedding_spy).validate_targeted_scope(uuid4(), [uuid4()], project=None) is None
    assert await ReEmbeddingService(shared_repo, embedding_spy).validate_targeted_scope(uuid4(), None, project=None) is None
