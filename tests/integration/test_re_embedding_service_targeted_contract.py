"""ReEmbeddingService.rebuild_targeted on the real Qdrant repository: which memories it pages through and what it reports.

The repository is the production one and the embedder is the real TensorRT model behind call-recording spies
(``embedding_spy``). A pass-through spy (``spy``) records the arguments of the repository calls and what they returned
while the real method still runs, so a test can state that the service counted, paged and wrote under the scope it was
given. What the service logs is part of the contract: it is what an operator reads when a rebuild goes wrong.
"""
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config.settings import settings
from app.services import re_embedding_service as service_module
from app.services.re_embedding_service import ReEmbeddingService
from tests.service_contract_support import (
    Call,
    ServiceLog,
    capturing,
    numbered_memory,
    seed_memories,
    spy,
)


@pytest.fixture
def log(caplog):
    return ServiceLog(caplog, service_module.logger)


@pytest.fixture
async def shared_repo(memory_repository, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    return memory_repository


@dataclass
class TargetedRun:
    """A rebuild of five memories in pages of two, with the calls the service made on the way."""

    user_id: UUID
    memories: list[Any]
    result: Any
    progress: list[tuple[int, int]]
    counts: list[Call]
    fetches: list[Call]
    upserts: list[Call]
    embedded: list[tuple[list[str], list[list[float]]]]
    logged: list[tuple[str, str, dict[str, Any]]]  # read in the fixture: a log record belongs to the phase that wrote it


@pytest.fixture
async def targeted_run(shared_repo, embedding_spy, monkeypatch, log):
    user_id, memories = await seed_memories(shared_repo, 5)
    counts = spy(monkeypatch, shared_repo, "count_memories_for_targeted_rebuild")
    fetches = spy(monkeypatch, shared_repo, "get_memories_for_targeted_rebuild", at_most=4)
    upserts = spy(monkeypatch, shared_repo, "upsert_targeted_embeddings")
    embedded = capturing(embedding_spy)
    progress: list[tuple[int, int]] = []

    result = await ReEmbeddingService(shared_repo, embedding_spy, batch_size=2).rebuild_targeted(
        user_id=user_id, progress_callback=lambda done, total: progress.append((done, total)),
    )

    return TargetedRun(user_id, memories, result, progress, counts, fetches, upserts, embedded, log.entries())


async def test_a_targeted_rebuild_counts_once_and_pages_through_the_candidates_until_a_page_is_empty(targeted_run):
    pages = [call.result for call in targeted_run.fetches]

    assert [len(page) for page in pages] == [2, 2, 1, 0]
    assert [(call.args, call.kwargs) for call in targeted_run.counts] == [
        ((), {"user_id": targeted_run.user_id, "memory_ids": None, "project": None}),
    ]
    assert [(call.args, call.kwargs) for call in targeted_run.fetches] == [
        ((), {"user_id": targeted_run.user_id, "limit": 2, "after_id": after, "memory_ids": None, "project": None})
        for after in (None, pages[0][-1].memory.id, pages[1][-1].memory.id, pages[2][-1].memory.id)
    ]


async def test_a_targeted_rebuild_writes_each_page_under_the_versions_it_read(targeted_run):
    pages = [call.result for call in targeted_run.fetches]

    assert [call.args for call in targeted_run.upserts] == [(), (), ()]
    for call, page, (_, vectors) in zip(targeted_run.upserts, pages[:3], targeted_run.embedded, strict=True):
        assert call.kwargs == {
            "user_id": targeted_run.user_id,
            "updates": [(stored.memory.id, vector) for stored, vector in zip(page, vectors, strict=True)],
            "expected_versions": {stored.memory.id: stored.version for stored in page},
        }


async def test_a_targeted_rebuild_reports_every_memory_rebuilt_and_its_progress(targeted_run):
    assert sorted(targeted_run.result.rebuilt_ids) == sorted(memory.id for memory in targeted_run.memories)
    assert (targeted_run.result.skipped_ids, targeted_run.result.failed) == ([], [])
    assert targeted_run.progress == [(2, 5), (4, 5), (5, 5)]


async def test_a_targeted_rebuild_logs_how_many_memories_it_rebuilt(targeted_run):
    assert targeted_run.logged == [
        (
            "INFO",
            "rebuild_embeddings: done",
            {"user_id": str(targeted_run.user_id), "rebuilt": 5, "skipped": 0, "failed": 0, "total_candidates": 5},
        ),
    ]


async def test_explicit_ids_narrow_the_count_the_pages_and_the_progress_total(shared_repo, embedding_spy, monkeypatch):
    user_id, memories = await seed_memories(shared_repo, 4)
    wanted = [memories[3].id, memories[1].id]
    counts = spy(monkeypatch, shared_repo, "count_memories_for_targeted_rebuild")
    fetches = spy(monkeypatch, shared_repo, "get_memories_for_targeted_rebuild", at_most=3)
    progress: list[tuple[int, int]] = []

    result = await ReEmbeddingService(shared_repo, embedding_spy, batch_size=1).rebuild_targeted(
        user_id=user_id, memory_ids=wanted, progress_callback=lambda done, total: progress.append((done, total)),
    )

    assert [call.kwargs for call in counts] == [{"user_id": user_id, "memory_ids": wanted, "project": None}]
    assert [call.kwargs["memory_ids"] for call in fetches] == [wanted, wanted, wanted]
    assert sorted(result.rebuilt_ids) == sorted(wanted)
    assert progress == [(1, 2), (2, 2)]


async def test_a_targeted_rebuild_trims_the_project_before_it_counts_and_fetches(
    shared_repo, embedding_spy, monkeypatch,
):
    user_id, memories = await seed_memories(shared_repo, 2, project="atlas")
    counts = spy(monkeypatch, shared_repo, "count_memories_for_targeted_rebuild")
    fetches = spy(monkeypatch, shared_repo, "get_memories_for_targeted_rebuild", at_most=2)

    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(user_id=user_id, project="  atlas ")

    assert sorted(result.rebuilt_ids) == sorted(memory.id for memory in memories)
    assert counts[0].kwargs == {"user_id": user_id, "memory_ids": None, "project": "atlas"}
    assert fetches[0].kwargs == {
        "user_id": user_id, "limit": 20, "after_id": None, "memory_ids": None, "project": "atlas",
    }


async def test_ids_nobody_owns_are_reported_once_each_when_there_is_nothing_to_rebuild(
    shared_repo, embedding_spy, log,
):
    user_id = uuid4()
    first, second = uuid4(), uuid4()

    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(
        user_id=user_id, memory_ids=[first, first, second],
    )

    assert (result.rebuilt_ids, result.skipped_ids) == ([], [])
    assert result.failed == [
        {"memory_id": first, "reason": "not_found_or_not_owned"},
        {"memory_id": second, "reason": "not_found_or_not_owned"},
    ]
    assert log.entries() == [
        (
            "INFO",
            "rebuild_embeddings: no candidates",
            {"user_id": str(user_id), "memory_ids": [str(first), str(first), str(second)], "project": None},
        ),
    ]


async def test_a_project_without_candidates_is_logged_with_an_empty_id_list(shared_repo, embedding_spy, log):
    user_id = uuid4()

    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(user_id=user_id, project="ghost")

    assert (result.rebuilt_ids, result.skipped_ids, result.failed) == ([], [], [])
    assert log.entries() == [
        (
            "INFO",
            "rebuild_embeddings: no candidates",
            {"user_id": str(user_id), "memory_ids": [], "project": "ghost"},
        ),
    ]


async def test_ids_are_not_reported_as_unresolved_when_a_project_was_asked_for_and_nothing_matches(
    shared_repo, embedding_spy,
):
    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(
        user_id=uuid4(), memory_ids=[uuid4()], project="atlas",
    )

    assert (result.rebuilt_ids, result.skipped_ids, result.failed) == ([], [], [])


async def test_ids_outside_a_requested_project_are_not_reported_as_unresolved(shared_repo, embedding_spy):
    user_id, inside = await seed_memories(shared_repo, 1, project="atlas")
    outside = await shared_repo.create_memory(user_id, numbered_memory(9, project="other"))

    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(
        user_id=user_id, memory_ids=[inside[0].id, outside.id, uuid4()], project="atlas",
    )

    assert result.rebuilt_ids == [inside[0].id]
    assert (result.skipped_ids, result.failed) == ([], [])
