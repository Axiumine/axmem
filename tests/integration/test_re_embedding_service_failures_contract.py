# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""ReEmbeddingService.rebuild_targeted on the real Qdrant repository: how it reports what failed, and the links it refreshes.

The repository is the production one and the embedder is the real TensorRT model behind call-recording spies
(``embedding_spy``), into which a test injects a provider fault by assigning ``side_effect``. A pass-through spy
(``spy``) records the arguments of the repository calls while the real method still runs. What the service logs is
part of the contract: it is what an operator reads when a rebuild goes wrong.
"""
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config.settings import settings
from app.exceptions import EmbeddingUnavailableError
from app.models.memory_models import MemoryUpdate
from app.services import re_embedding_service as service_module
from app.services.re_embedding_service import ReEmbeddingService
from app.utils.embedding_helpers import build_embedding_text
from tests.service_contract_support import ServiceLog, seed_memories, spy


@pytest.fixture
def log(caplog):
    return ServiceLog(caplog, service_module.logger)


@pytest.fixture
async def shared_repo(memory_repository, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    return memory_repository


async def test_a_memory_whose_embedding_fails_is_reported_once_and_logged_next_to_the_unknown_ids(
    shared_repo, embedding_spy, log,
):
    user_id, (good, bad) = await seed_memories(shared_repo, 2)
    unknown = uuid4()
    bad_text = build_embedding_text(bad)

    async def a_batch_the_provider_rejects(texts):
        raise RuntimeError("batch rejected")

    async def one_text_the_provider_rejects(text):
        if text == bad_text:
            raise RuntimeError("text rejected")
        return await embedding_spy.real.generate_embedding(text)

    embedding_spy.generate_embeddings.side_effect = a_batch_the_provider_rejects
    embedding_spy.generate_embedding.side_effect = one_text_the_provider_rejects

    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(
        user_id=user_id, memory_ids=[good.id, bad.id, unknown],
    )

    assert result.rebuilt_ids == [good.id]
    assert result.skipped_ids == []
    assert result.failed == [
        {"memory_id": bad.id, "reason": "embedding_failed"},
        {"memory_id": unknown, "reason": "not_found_or_not_owned"},
    ]
    assert log.entries() == [
        (
            "ERROR",
            "rebuild_embeddings: batch embedding failed, retrying one memory at a time",
            {"user_id": str(user_id), "batch_size": 2},
        ),
        ("ERROR", "rebuild_embeddings: embedding failed", {"memory_id": str(bad.id), "user_id": str(user_id)}),
        (
            "INFO",
            "rebuild_embeddings: done",
            {"user_id": str(user_id), "rebuilt": 1, "skipped": 0, "failed": 2, "total_candidates": 2},
        ),
    ]
    assert [record.exc_info[0] for record in log.records()[:2]] == [RuntimeError, RuntimeError]


async def test_an_unavailable_provider_fails_the_whole_batch_in_one_log_line(shared_repo, embedding_spy, log):
    user_id, memories = await seed_memories(shared_repo, 3)
    embedding_spy.generate_embeddings.side_effect = EmbeddingUnavailableError("budget exceeded")

    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(user_id=user_id)

    assert result.rebuilt_ids == []
    assert sorted(result.failed, key=lambda failure: str(failure["memory_id"])) == sorted(
        ({"memory_id": memory.id, "reason": "embedding_failed"} for memory in memories),
        key=lambda failure: str(failure["memory_id"]),
    )
    assert log.entries()[0] == (
        "ERROR",
        "rebuild_embeddings: the embeddings provider is unavailable, not retrying",
        {"user_id": str(user_id), "batch_size": 3},
    )
    assert log.records()[0].exc_info[0] is EmbeddingUnavailableError


async def test_each_write_outcome_lands_under_its_own_code(shared_repo, embedding_spy, log):
    user_id, memories = await seed_memories(shared_repo, 3)
    ids_by_text = {build_embedding_text(memory): memory.id for memory in memories}
    batch: list[str] = []

    async def one_vector_too_wide_and_one_memory_edited_meanwhile(texts):
        batch.extend(texts)
        vectors = await embedding_spy.real.generate_embeddings(texts)
        vectors[0] = [*vectors[0], 0.0]
        await shared_repo.update_memory(user_id, ids_by_text[texts[1]], MemoryUpdate(importance=9))
        return vectors

    embedding_spy.generate_embeddings.side_effect = one_vector_too_wide_and_one_memory_edited_meanwhile

    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(user_id=user_id)

    wrong_width, edited, written = (ids_by_text[text] for text in batch)
    assert result.failed == [{"memory_id": wrong_width, "reason": "persistence_failed"}]
    assert result.skipped_ids == [edited]
    assert result.rebuilt_ids == [written]
    assert log.entries() == [
        (
            "INFO",
            "rebuild_embeddings: done",
            {"user_id": str(user_id), "rebuilt": 1, "skipped": 1, "failed": 1, "total_candidates": 3},
        ),
    ]


@pytest.mark.parametrize("wanted", [0, 1])
async def test_auto_links_are_refreshed_only_when_at_least_one_is_wanted(
    shared_repo, embedding_spy, monkeypatch, wanted,
):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", wanted)
    user_id, memories = await seed_memories(shared_repo, 2)
    searches = spy(monkeypatch, shared_repo, "find_similar_memories")

    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(user_id=user_id)

    assert sorted(result.rebuilt_ids) == sorted(memory.id for memory in memories)
    asked = [(call.args, call.kwargs) for call in searches]
    assert asked == ([((), {"user_id": user_id, "memory_id": memory_id, "max_links": wanted}) for memory_id in result.rebuilt_ids]
                     if wanted else [])


async def test_a_memory_without_a_neighbour_does_not_stop_the_links_of_the_next_one(
    shared_repo, embedding_spy, monkeypatch,
):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 2)
    user_id, (neighbour, _) = await seed_memories(shared_repo, 2)
    answers = iter([[], [neighbour]])
    searched: list[UUID] = []
    link_writes: list[dict[str, Any]] = []

    async def similar_memories(**kwargs):
        searched.append(kwargs["memory_id"])
        return next(answers)

    async def link(**kwargs):
        link_writes.append(kwargs)

    monkeypatch.setattr(shared_repo, "find_similar_memories", similar_memories)
    monkeypatch.setattr(shared_repo, "create_links_batch", link)

    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(user_id=user_id)

    assert searched == result.rebuilt_ids
    assert link_writes == [{"user_id": user_id, "source_id": searched[1], "target_ids": [neighbour.id]}]


async def test_a_failed_auto_link_is_logged_for_that_memory(shared_repo, embedding_spy, monkeypatch, log):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 1)
    user_id, (memory,) = await seed_memories(shared_repo, 1)

    async def broken(**kwargs):
        raise RuntimeError("similarity lookup failed")

    monkeypatch.setattr(shared_repo, "find_similar_memories", broken)

    result = await ReEmbeddingService(shared_repo, embedding_spy).rebuild_targeted(user_id=user_id)

    assert result.failed == [{"memory_id": memory.id, "phase": "auto_link", "reason": "auto_link_failed"}]
    assert log.entries()[0] == (
        "ERROR",
        "rebuild_embeddings: auto-link failed",
        {"memory_id": str(memory.id), "user_id": str(user_id)},
    )
    assert log.records()[0].exc_info[0] is RuntimeError
