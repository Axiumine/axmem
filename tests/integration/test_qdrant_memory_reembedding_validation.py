"""Re-embedding in Qdrant: what the validation of a generation asks of the node, and what each failed check means.

The node and the embedder are the real ones; the spy of the store notes each call the repository makes on it and can
stand in for a request that answers otherwise.
"""
from typing import Any
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.protocols.memory_protocol import ValidationResult
from app.repositories.qdrant.store import SYSTEM, Point
from tests.reembedding_support import hold, make_world, seed


@pytest.fixture
async def world(isolated_stack, tensorrt_embedder, monkeypatch):
    return make_world(isolated_stack, tensorrt_embedder, monkeypatch)


@pytest.fixture
async def generations(world):
    """Two memories in the live generation and an empty generation next to it, with no call noted yet."""
    first, second = await seed(world, "First", "Second")
    live = await world.repo.live_generation()
    target = await world.repo.create_generation()
    world.store.calls.clear()
    return first, second, live, target


async def test_the_live_generation_passes_every_check_by_the_vector_of_a_sample_that_is_searched_for(world, generations):
    *_, live, _ = generations

    assert await world.repo.validate_generation(live) == ValidationResult(count_ok=True, dimensions_ok=True, search_ok=True)

    assert world.store.names() == ["scroll", "search"]
    [(scroll_args, scroll_kwargs)] = world.store.made("scroll")
    assert scroll_args == (SYSTEM, live)
    assert scroll_kwargs == {"limit": 1, "with_payload": False, "with_vectors": True}
    [sample] = world.store.results["scroll"][0]
    assert sample.vector is not None
    [(search_args, search_kwargs)] = world.store.made("search")
    assert search_args == (SYSTEM, live)
    assert search_kwargs == {"query": sample.vector, "limit": 1}


async def test_a_generation_that_holds_no_memory_fails_the_count_against_the_live_one_and_is_not_searched(
    world, generations,
):
    *_, live, target = generations

    empty = await world.repo.validate_generation(target)

    assert empty == ValidationResult(count_ok=False, dimensions_ok=True, search_ok=True)
    assert [args for args, _ in world.store.made("count")] == [(SYSTEM, target), (SYSTEM, live)]
    assert world.store.made("search") == []


async def test_a_generation_that_holds_fewer_memories_than_the_live_one_fails_the_count(world, generations):
    first, _, _, target = generations
    await hold(world, target, first, 1)

    assert (await world.repo.validate_generation(target)).count_ok is False


async def test_a_generation_that_holds_as_many_memories_as_the_live_one_passes_every_check(world, generations):
    first, second, _, target = generations
    await hold(world, target, first, 1)
    await hold(world, target, second, 1)
    world.store.calls.clear()

    full = await world.repo.validate_generation(target)

    assert full == ValidationResult(count_ok=True, dimensions_ok=True, search_ok=True)
    assert full.all_passed
    [(search_args, search_kwargs)] = world.store.made("search")
    assert search_args == (SYSTEM, target)
    assert search_kwargs["limit"] == 1
    assert len(search_kwargs["query"]) == 384


async def test_a_generation_of_another_width_than_the_settings_fails_the_dimension_check_even_when_it_holds_no_memory(
    world, monkeypatch,
):
    repo = world.repo
    await seed(world, "First")
    target = await repo.create_generation()
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 8)

    assert (await repo.validate_generation(target)).dimensions_ok is False
    assert (await repo.validate_generation(await repo.live_generation())).dimensions_ok is False


async def test_a_generation_that_returns_a_vector_narrower_than_the_settings_or_none_fails_the_dimension_check(world):
    repo = world.repo
    await seed(world, "First")
    live = await repo.live_generation()
    world.store.calls.clear()
    replies: list[list[Point]] = [[Point(uuid4(), {}, vector=[0.5] * 3)], [Point(uuid4(), {}, vector=None)]]

    async def narrow(*_args: Any, **_kwargs: Any) -> list[Point]:
        return replies.pop(0)

    async def finds_the_sample(*_args: Any, **_kwargs: Any) -> list[Point]:
        return [Point(uuid4(), {})]

    world.store.replaced["scroll"] = narrow
    world.store.replaced["search"] = finds_the_sample

    assert await repo.validate_generation(live) == ValidationResult(count_ok=True, dimensions_ok=False, search_ok=True)
    assert world.store.names().count("search") == 1  # the sample with a vector is searched for
    assert await repo.validate_generation(live) == ValidationResult(count_ok=True, dimensions_ok=False, search_ok=True)
    assert world.store.names().count("search") == 1  # a sample without one is not


async def test_a_generation_whose_search_finds_nothing_fails_the_search_check(world):
    repo = world.repo
    await seed(world, "First")
    live = await repo.live_generation()

    async def finds_nothing(*_args: Any, **_kwargs: Any) -> list[Point]:
        return []

    world.store.replaced["search"] = finds_nothing

    assert await repo.validate_generation(live) == ValidationResult(count_ok=True, dimensions_ok=True, search_ok=False)
