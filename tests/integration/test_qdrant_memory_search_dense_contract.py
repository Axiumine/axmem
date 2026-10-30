# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Searching memories in Qdrant by the vector alone: what a search asks the node for and what comes back.

The reranker is off here, or has nothing to choose among. The node, the embedder and the reranker are the real ones; the
store is behind a spy that notes each call (see ``tests/memory_search_support.py``).
"""
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config.settings import settings
from app.repositories.qdrant.store import Scope, Where, at_least, equals, has_ids
from app.utils.embedding_helpers import build_embedding_text
from tests.embedding_doubles import cosine
from tests.memory_search_support import (
    a_memory,
    ask,
    build_world,
    five,
    one_memory_short,
    seed,
)


@pytest.fixture
def world(isolated_stack, embedding_spy, tensorrt_embedder, tensorrt_reranker, monkeypatch):
    return build_world(isolated_stack, embedding_spy, tensorrt_embedder, tensorrt_reranker, monkeypatch)


@dataclass
class Searched:
    created: list[Any]
    query: str
    query_vector: list[float]
    scored: list[Any]
    expected: list[tuple[float, UUID]]


@pytest.fixture
async def searched(world):
    """Five memories of the tenant and one of another, a search for the best three, and what the best three should be."""
    created = await seed(world, *five())
    await seed(world, a_memory("Deploying a service with Docker"), tenant=uuid4())
    query = "  " + build_embedding_text(five()[0]) + "  "
    query_vector = await world.real_embedder.generate_embedding(query.strip())

    scored = await ask(world, query, k=3)

    stored = {
        point.id: point.vector
        for point in await world.repo.store.get(
            Scope.of(world.tenant), world.repo.memories, [m.id for m in created], with_vectors=True,
        )
    }
    expected = sorted(((cosine(query_vector, stored[m.id]), m.id) for m in created), reverse=True)[:3]
    return Searched(created, query, query_vector, scored, expected)


async def test_a_search_returns_the_best_k_active_memories_of_the_tenant_by_similarity(searched):
    assert [memory.id for memory, _ in searched.scored] == [memory_id for _, memory_id in searched.expected]
    assert searched.scored[0][0].id == searched.created[0].id


async def test_a_search_scores_each_memory_by_its_similarity_to_the_query_and_does_not_call_the_reranker(world, searched):
    for (memory, score), (similarity_to_query, memory_id) in zip(searched.scored, searched.expected, strict=True):
        assert memory.id == memory_id
        assert score.memory_id == memory_id
        assert score.similarity == pytest.approx(similarity_to_query, abs=1e-4)
        assert score.rerank_score is None
    assert world.reranker.calls == []


async def test_a_search_embeds_the_stripped_query_once(world, searched):
    world.embedder.generate_embedding.assert_awaited_once_with(text=searched.query.strip())


async def test_a_search_asks_the_node_for_the_k_nearest_active_memories_of_the_tenant(world, searched):
    [(args, kwargs)] = world.store.made("search")
    assert args == (Scope.of(world.tenant), world.repo.memories)
    assert set(kwargs) == {"query", "where", "limit"}
    assert kwargs["query"] == pytest.approx(searched.query_vector, abs=1e-5)
    assert kwargs["limit"] == 3
    assert kwargs["where"] == Where().also(equals("is_obsolete", False))


async def test_a_search_without_scores_is_the_memories_of_the_search_with_scores(world):
    await seed(world, *five())

    query = build_embedding_text(five()[2])

    plain = await world.repo.search(world.tenant, query, "", 4, None, None, None)
    scored = await ask(world, query, k=4)

    assert len(plain) == 4
    assert [memory.id for memory in plain] == [memory.id for memory, _ in scored]
    assert plain[0].title == five()[2].title


@pytest.mark.parametrize("query", ["", " ", "   \n\t "])
@pytest.mark.parametrize("reranking", [False, True])
async def test_a_blank_query_finds_nothing_and_embeds_and_searches_nothing(world, monkeypatch, query, reranking):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", reranking)
    await seed(world, *five()[:2])

    assert await ask(world, query) == []

    world.embedder.generate_embedding.assert_not_awaited()
    assert world.store.made("search") == []
    assert world.reranker.calls == []


# The conditions are built by the test, not by the parameters: a parameter is built when the module is collected, and a
# module that cannot be collected ends the run of its tests with a usage error instead of a failed test.
@pytest.mark.parametrize(
    ("importance_threshold", "project", "exclude", "conditions"),
    [
        (None, None, None, lambda: ([], [])),
        (0, "", [], lambda: ([], [])),
        (7, None, None, lambda: ([at_least("importance", 7)], [])),
        (1, None, None, lambda: ([at_least("importance", 1)], [])),
        (None, "alpha", None, lambda: ([equals("project", "alpha")], [])),
        (None, None, [UUID(int=1)], lambda: ([], [has_ids([UUID(int=1)])])),
        (
            3, "alpha", [UUID(int=1), UUID(int=2)],
            lambda: ([at_least("importance", 3), equals("project", "alpha")], [has_ids([UUID(int=1), UUID(int=2)])]),
        ),
    ],
    ids=["none", "falsy", "importance-7", "importance-1", "project", "excluded", "all"],
)
async def test_a_search_adds_a_condition_for_each_filter_that_was_given(
    world, importance_threshold, project, exclude, conditions,
):
    must, must_not = conditions()

    assert await ask(
        world, importance_threshold=importance_threshold, project=project, exclude_ids=exclude,
    ) == []

    [(_, kwargs)] = world.store.made("search")
    assert kwargs["where"] == Where(
        must=(equals("is_obsolete", False), *must), must_not=tuple(must_not),
    )


@pytest.fixture
async def deployments(world):
    """Five ways to deploy, one of them obsolete, as ``high``, ``low``, ``other_project`` and ``fifth`` (the live ones)."""
    high, low, other_project, obsolete, fifth = await seed(
        world,
        a_memory("Deploying with Docker", importance=9, project="alpha"),
        a_memory("Deploying with Kubernetes", importance=3, project="alpha"),
        a_memory("Deploying with Nomad", importance=9, project="beta"),
        a_memory("Deploying with Ansible", importance=9, project="alpha"),
        a_memory("Deploying with Helm", importance=8, project="alpha"),
    )
    await world.repo.mark_obsolete(world.tenant, obsolete.id, "replaced")
    world.store.calls.clear()
    return high, low, other_project, fifth


def found(scored):
    return {memory.id for memory, _ in scored}


async def test_a_search_keeps_to_the_active_memories_of_the_project_above_the_importance_and_not_excluded(
    world, deployments,
):
    high, low, other_project, fifth = deployments

    assert found(await ask(world, k=10)) == {high.id, low.id, other_project.id, fifth.id}
    assert found(await ask(world, k=10, importance_threshold=8)) == {high.id, other_project.id, fifth.id}
    assert found(await ask(world, k=10, project="alpha")) == {high.id, low.id, fifth.id}
    assert found(await ask(world, k=10, exclude_ids=[high.id, fifth.id])) == {low.id, other_project.id}
    assert found(
        await ask(world, k=10, importance_threshold=8, project="alpha", exclude_ids=[fifth.id]),
    ) == {high.id}


async def test_a_search_without_scores_keeps_to_the_same_memories(world, deployments):
    high, _, other_project, fifth = deployments

    assert [memory.id for memory in await world.repo.search(world.tenant, "Deploying", "", 10, 9, "beta", [])] == [
        other_project.id,
    ]
    assert [
        memory.id for memory in await world.repo.search(world.tenant, "Deploying", "", 10, 8, "alpha", [fifth.id])
    ] == [high.id]


async def test_a_search_never_pairs_the_scores_with_fewer_memories_than_it_found(world, monkeypatch):
    await seed(world, *five()[:2])
    one_memory_short(monkeypatch, world.repo)

    with pytest.raises(ValueError, match="zip"):
        await ask(world, k=2)


@pytest.mark.parametrize(
    ("reranking", "k", "limit"),
    [(False, 3, 3), (False, 12, 12), (True, 3, 7), (True, 7, 7), (True, 8, 8), (True, 12, 12)],
)
async def test_a_search_asks_the_node_for_k_memories_and_for_more_when_the_reranker_is_to_choose_among_them(
    world, monkeypatch, reranking, k, limit,
):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", reranking)
    monkeypatch.setattr(settings, "DENSE_SEARCH_CANDIDATES", 7)

    assert await ask(world, k=k) == []

    [(_, kwargs)] = world.store.made("search")
    assert kwargs["limit"] == limit
    assert world.reranker.calls == []
