"""Searching memories in Qdrant for the ones like a stored memory: the live ones, and the obsolete ones.

Four memories are stored (a subject and three others, of which the nearest two are told apart by more than the
tolerance), and the tests compare what the repository finds with the cosine similarity of the real embedder. The store is
behind a spy that notes each call (see ``tests/memory_search_support.py``).
"""
import math
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.repositories.qdrant.store import Scope, Where, equals, has_ids
from tests.embedding_doubles import similarity
from tests.memory_search_support import (
    a_memory,
    build_world,
    five,
    one_memory_short,
    seed,
)


@pytest.fixture
def world(isolated_stack, embedding_spy, tensorrt_embedder, tensorrt_reranker, monkeypatch):
    return build_world(isolated_stack, embedding_spy, tensorrt_embedder, tensorrt_reranker, monkeypatch)


@dataclass
class Neighbours:
    """The stored memories by name, how near each other one is to the subject, and the others from the nearest."""

    created: dict[str, Any]
    nearness: dict[str, float]
    order: list[str]
    twins: dict[str, Any] = field(default_factory=dict)

    @property
    def subject(self) -> Any:
        return self.created["subject"]

    @property
    def middle(self) -> float:
        """The threshold half way between the two nearest."""
        return (self.nearness[self.order[0]] + self.nearness[self.order[1]]) / 2

    @property
    def scores(self) -> list[float]:
        return [self.nearness[name] for name in self.order]

    def nearest(self, count: int) -> list[UUID]:
        """The ids of the ``count`` nearest others, the nearest first."""
        return [self.created[name].id for name in self.order[:count]]


@pytest.fixture
async def neighbours(world):
    """A subject and three others, and how near each of the three is to the subject."""
    subject = a_memory("Deploying a service with Docker", content="Build the image and run it with docker compose up")
    others = {
        "close": a_memory("Deploying a service with Podman", content="Build the image and run it with podman compose up"),
        "middle": a_memory("Deploying a website with Docker", content="Build the site and push the image to the registry"),
        "far": a_memory("Baking a chocolate cake", content="Melt the chocolate, fold in the flour and bake"),
    }
    created = dict(zip(["subject", *others], await seed(world, subject, *others.values()), strict=True))
    nearness = {
        name: await similarity(world.real_embedder, subject, memory) for name, memory in others.items()
    }
    order = sorted(nearness, key=lambda name: nearness[name], reverse=True)
    assert nearness[order[0]] - nearness[order[1]] > 1e-3
    assert nearness[order[1]] - nearness[order[2]] > 1e-3
    return Neighbours(created, nearness, order)


@pytest.fixture
async def crowded(world, neighbours):
    """The neighbours, with an obsolete twin of the subject and a twin of another tenant: neither is ever like it."""
    obsolete_twin = (await seed(world, a_memory("Deploying a service with Docker", content="Obsolete twin")))[0]
    await world.repo.mark_obsolete(world.tenant, obsolete_twin.id, "replaced")
    await seed(world, a_memory("Deploying a service with Docker"), tenant=uuid4())
    return neighbours


@pytest.fixture
async def similar(world, crowded, monkeypatch):
    """The ten memories like the subject that are above a threshold half way between the two nearest."""
    monkeypatch.setattr(settings, "MEMORY_SIMILARITY_THRESHOLD", crowded.middle)
    return await world.repo.find_similar_memories_scored(world.tenant, crowded.subject.id, 10)


async def test_the_memories_like_a_stored_one_are_the_active_ones_above_the_threshold_best_first_without_itself(
    crowded, similar,
):
    assert [(memory.id, score) for memory, score in similar] == [
        (crowded.nearest(1)[0], pytest.approx(crowded.scores[0], abs=1e-4)),
    ]


async def test_the_stored_memory_is_read_with_its_vector_from_a_majority_to_find_the_memories_like_it(
    world, crowded, similar,
):
    [(get_args, get_kwargs)] = world.store.made("get")
    assert get_args == (Scope.of(world.tenant), world.repo.memories, [crowded.subject.id])
    assert get_kwargs == {"with_payload": False, "with_vectors": True, "quorum": True}


async def test_the_memories_like_a_stored_one_are_searched_for_by_its_vector(world, crowded, similar):
    [(_, kwargs)] = world.store.made("search")
    assert kwargs["query"] == pytest.approx(
        (await world.repo.store.get(
            Scope.of(world.tenant), world.repo.memories, [crowded.subject.id], with_vectors=True,
        ))[0].vector,
    )


async def test_the_memories_like_a_stored_one_are_searched_for_among_the_other_active_ones_above_the_threshold(
    world, crowded, similar,
):
    [(args, kwargs)] = world.store.made("search")
    assert args == (Scope.of(world.tenant), world.repo.memories)
    assert set(kwargs) == {"query", "where", "limit", "score_threshold"}
    assert kwargs["where"] == Where(
        must=(equals("is_obsolete", False),), must_not=(has_ids([crowded.subject.id]),),
    )
    assert kwargs["limit"] == 10
    assert kwargs["score_threshold"] == crowded.middle


async def test_below_every_threshold_the_memories_like_a_stored_one_are_all_the_others_best_first(
    world, crowded, monkeypatch,
):
    monkeypatch.setattr(settings, "MEMORY_SIMILARITY_THRESHOLD", -1.0)

    everything = await world.repo.find_similar_memories_scored(world.tenant, crowded.subject.id, 10)

    assert [memory.id for memory, _ in everything] == crowded.nearest(3)
    assert [score for _, score in everything] == pytest.approx(crowded.scores, abs=1e-4)


async def test_the_memories_like_a_stored_one_are_no_more_than_the_limit(world, crowded, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_SIMILARITY_THRESHOLD", -1.0)

    only_nearest = await world.repo.find_similar_memories_scored(world.tenant, crowded.subject.id, 1)

    assert [memory.id for memory, _ in only_nearest] == crowded.nearest(1)


async def test_the_memories_like_a_stored_one_are_without_scores_when_the_scores_are_not_asked_for(
    world, crowded, monkeypatch,
):
    monkeypatch.setattr(settings, "MEMORY_SIMILARITY_THRESHOLD", -1.0)

    plain = await world.repo.find_similar_memories(world.tenant, crowded.subject.id, 2)

    assert [memory.id for memory in plain] == crowded.nearest(2)


async def test_the_similar_memories_are_never_paired_with_fewer_memories_than_were_found(world, monkeypatch):
    subject, _ = await seed(world, *five()[:2])
    monkeypatch.setattr(settings, "MEMORY_SIMILARITY_THRESHOLD", -1.0)
    one_memory_short(monkeypatch, world.repo)

    with pytest.raises(ValueError, match="zip"):
        await world.repo.find_similar_memories_scored(world.tenant, subject.id, 5)


async def test_a_memory_that_is_not_the_tenants_has_no_similar_memories_to_find(world):
    [mine] = await seed(world, a_memory("Mine"))
    [theirs] = await seed(world, a_memory("Theirs"), tenant=uuid4())
    missing = uuid4()

    for memory_id in (missing, theirs.id):
        with pytest.raises(NotFoundError) as refused:
            await world.repo.find_similar_memories_scored(world.tenant, memory_id, 3)
        assert str(refused.value) == f"Memory {memory_id} not found or has no embedding"
        with pytest.raises(NotFoundError, match=f"Memory {memory_id} not found or has no embedding"):
            await world.repo.find_similar_memories(world.tenant, memory_id, 3)
        assert await world.repo.find_obsolete_matches(world.tenant, memory_id) == []
    assert world.store.made("search") == []
    assert (await world.repo.find_similar_memories_scored(world.tenant, mine.id, 3)) == []


@pytest.fixture
async def obsoleted(world, neighbours):
    """The neighbours, all three marked obsolete, and a live twin of the subject that is not among the obsolete ones."""
    for name in ("close", "middle", "far"):
        await world.repo.mark_obsolete(world.tenant, neighbours.created[name].id, "replaced")
    [neighbours.twins["live"]] = await seed(world, a_memory("Deploying a service with Docker", content="Live twin"))
    world.store.calls.clear()
    return neighbours


@pytest.fixture
async def default_matches(world, obsoleted):
    """The obsolete memories like the subject, asked for with the default limit and similarity."""
    return await world.repo.find_obsolete_matches(world.tenant, obsoleted.subject.id)


async def test_the_obsolete_memories_like_a_stored_one_are_above_the_default_similarity(default_matches):
    assert all(score >= 0.89 - 1e-4 for _, score in default_matches)


async def test_the_obsolete_memories_like_a_stored_one_are_searched_for_among_the_obsolete_ones_by_default(
    world, obsoleted, default_matches,
):
    [(args, kwargs)] = world.store.made("search")
    assert args == (Scope.of(world.tenant), world.repo.memories)
    assert kwargs["where"] == Where(must=(equals("is_obsolete", True),), must_not=(has_ids([obsoleted.subject.id]),))
    assert kwargs["limit"] == 3
    assert kwargs["score_threshold"] == 0.89


async def test_the_stored_memory_is_read_with_its_vector_from_a_majority_to_find_its_obsolete_matches(
    world, obsoleted, default_matches,
):
    [(get_args, get_kwargs)] = world.store.made("get")
    assert get_args == (Scope.of(world.tenant), world.repo.memories, [obsoleted.subject.id])
    assert get_kwargs == {"with_payload": False, "with_vectors": True, "quorum": True}


async def test_the_obsolete_memories_above_the_given_similarity_are_found_best_first(world, obsoleted):
    matches = await world.repo.find_obsolete_matches(
        world.tenant, obsoleted.subject.id, limit=5, min_similarity=obsoleted.middle,
    )

    assert [(memory.id, score) for memory, score in matches] == [
        (obsoleted.nearest(1)[0], pytest.approx(obsoleted.scores[0], abs=1e-4)),
    ]


async def test_the_obsolete_memories_are_searched_for_with_the_limit_and_the_similarity_given(world, obsoleted):
    await world.repo.find_obsolete_matches(world.tenant, obsoleted.subject.id, limit=5, min_similarity=obsoleted.middle)

    assert world.store.made("search")[-1][1]["limit"] == 5
    assert world.store.made("search")[-1][1]["score_threshold"] == obsoleted.middle


@pytest.fixture
async def every_obsolete_match(world, obsoleted):
    """The two nearest obsolete memories, with no similarity asked for."""
    return await world.repo.find_obsolete_matches(world.tenant, obsoleted.subject.id, limit=2, min_similarity=-1.0)


async def test_the_obsolete_memories_without_a_similarity_asked_for_are_the_nearest_best_first(
    obsoleted, every_obsolete_match,
):
    assert [memory.id for memory, _ in every_obsolete_match] == obsoleted.nearest(2)


async def test_a_live_twin_of_the_stored_memory_is_not_an_obsolete_match(obsoleted, every_obsolete_match):
    assert obsoleted.twins["live"].id not in {memory.id for memory, _ in every_obsolete_match}


async def test_the_score_of_an_obsolete_match_is_its_similarity_to_the_stored_memory(obsoleted, every_obsolete_match):
    assert math.isclose(every_obsolete_match[0][1], obsoleted.nearness[obsoleted.order[0]], abs_tol=1e-4)
