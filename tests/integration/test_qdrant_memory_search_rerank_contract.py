# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Searching memories in Qdrant with the reranker: what it is given, and what comes back in the order it chose.

The reranker is the real model, so the order it gives is whatever the model says, and the tests follow it. The store and
the reranker are behind spies that note each call (see ``tests/memory_search_support.py``).
"""
from dataclasses import dataclass
from typing import Any

import pytest

from app.config.settings import settings
from app.repositories.embeddings.reranker_adapter import RerankAdapter
from app.repositories.qdrant import memory_search as search_module
from app.repositories.qdrant.store import Scope
from app.utils.embedding_helpers import build_contextual_query, build_memory_text
from tests.memory_search_support import ask, build_world, five, seed


@pytest.fixture
def world(isolated_stack, embedding_spy, tensorrt_embedder, tensorrt_reranker, monkeypatch):
    return build_world(isolated_stack, embedding_spy, tensorrt_embedder, tensorrt_reranker, monkeypatch)


@dataclass
class Reranked:
    created: list[Any]
    query: str
    scored: list[Any]


@pytest.fixture
async def reranked(world, monkeypatch, k, context):
    """Five memories stored, a search for ``k`` of them with ``context`` and the reranker to choose among five."""
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "DENSE_SEARCH_CANDIDATES", 5)
    created = await seed(world, *five())
    query = " How is a service deployed? "

    return Reranked(created, query, await ask(world, query, context=context, k=k))


CHOSEN_OF = pytest.mark.parametrize("k", [2, 4])
WITH_CONTEXT = pytest.mark.parametrize("context", ["", "debugging a failed rollout"])


@CHOSEN_OF
@WITH_CONTEXT
async def test_the_reranker_is_given_the_texts_of_the_candidates_the_node_found(world, reranked):
    by_id = {memory.id: memory for memory in reranked.created}

    [(_, documents, _)] = world.reranker.calls

    candidates = world.store.results["search"][0]
    assert len(candidates) == 5
    assert documents == [build_memory_text(by_id[point.id]) for point in candidates]


@CHOSEN_OF
@WITH_CONTEXT
async def test_the_reranker_is_given_the_query_with_its_context_when_there_is_one(world, reranked, context):
    [(rerank_query, _, _)] = world.reranker.calls

    assert rerank_query == (
        build_contextual_query(query=reranked.query, context=context) if context else reranked.query
    )


@CHOSEN_OF
@WITH_CONTEXT
async def test_the_reranker_chooses_k_of_the_candidates_and_its_scores_are_kept(world, reranked, k):
    [(_, _, ranked)] = world.reranker.calls
    candidates = world.store.results["search"][0]

    assert len(reranked.scored) == k
    for (memory, score), (index, rerank_score) in zip(reranked.scored, ranked[:k], strict=True):
        assert memory.id == candidates[index].id
        assert score.memory_id == memory.id
        assert score.similarity == candidates[index].score
        assert score.rerank_score == rerank_score
        assert isinstance(score.rerank_score, float)


async def test_the_adapter_that_reranks_the_candidates_is_taken_as_a_reranker(world, monkeypatch):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "DENSE_SEARCH_CANDIDATES", 5)
    taken_as: list[Any] = []

    def noting_cast(target: Any, value: Any) -> Any:
        taken_as.append(target)
        return value

    await seed(world, *five())
    monkeypatch.setattr(search_module, "cast", noting_cast)

    await ask(world, k=2)

    assert taken_as == [RerankAdapter]


async def test_a_search_without_scores_returns_the_candidates_in_the_order_the_reranker_chose(world, monkeypatch):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "DENSE_SEARCH_CANDIDATES", 5)
    await seed(world, *five())

    chosen = await world.repo.search(world.tenant, "How is a service deployed?", "on a laptop", 3, None, None, None)

    [(rerank_query, _, ranked)] = world.reranker.calls
    candidates = world.store.results["search"][0]
    assert rerank_query == "query: How is a service deployed?, context: on a laptop"
    assert [memory.id for memory in chosen] == [candidates[index].id for index, _ in ranked[:3]]


@pytest.mark.parametrize(("reranking", "k", "expected"), [(True, 5, 5), (True, 9, 5), (False, 2, 2), (False, 5, 5)])
async def test_the_candidates_stay_in_their_dense_order_when_no_reranking_is_needed(
    world, monkeypatch, reranking, k, expected,
):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", reranking)
    monkeypatch.setattr(settings, "DENSE_SEARCH_CANDIDATES", 5)
    await seed(world, *five())

    scored = await ask(world, k=k)

    candidates = world.store.results["search"][0]
    assert [(memory.id, score.similarity, score.rerank_score) for memory, score in scored] == [
        (point.id, point.score, None) for point in candidates[:expected]
    ]
    assert len(scored) == expected
    assert world.reranker.calls == []


async def test_the_candidates_are_kept_whole_when_reranking_is_off_whatever_the_number_the_store_returned(world):
    await seed(world, *five())

    async def returns_more_than_asked_for(scope: Scope, collection: str, **kwargs: Any) -> list[Any]:
        return await world.store.real.search(scope, collection, **{**kwargs, "limit": 5})

    world.store.replaced["search"] = returns_more_than_asked_for

    scored = await ask(world, k=2)

    candidates = world.store.results["search"][0]
    assert len(candidates) == 5
    assert [(memory.id, score.similarity, score.rerank_score) for memory, score in scored] == [
        (point.id, point.score, None) for point in candidates
    ]
    assert world.reranker.calls == []


async def test_a_search_that_finds_nothing_does_not_call_the_reranker(world, monkeypatch):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)

    assert await ask(world, k=1) == []

    assert world.reranker.calls == []
