"""Re-embedding in Qdrant: which memories of one tenant a targeted rebuild counts, reads and writes new embeddings for.

The repository works through the store, so the tests note every call it makes there (a spy that passes each call on, and
can stand in for a request that fails or has no effect) and read the collections back from the node. The node and the
embedder are the real ones.
"""
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.models.memory_models import Memory, MemoryUpdate
from app.protocols.memory_protocol import EmbeddingWrites
from app.repositories.qdrant import memory_reembedding as module
from app.repositories.qdrant.store import (
    TOKEN_KEY,
    VERSION_KEY,
    NewPoint,
    Scope,
    Where,
    equals,
)
from tests.reembedding_support import (
    TOKEN,
    held,
    ids_of,
    make_world,
    plant_tenant_memories,
    seed,
)


@pytest.fixture
async def world(isolated_stack, tensorrt_embedder, monkeypatch):
    return make_world(isolated_stack, tensorrt_embedder, monkeypatch)


@pytest.fixture
async def tenant_memories(world):
    return await plant_tenant_memories(world)


# ---- what a targeted rebuild reads -------------------------------------------------------------------------------


async def test_the_memories_to_rebuild_are_the_active_ones_of_the_tenant(world, tenant_memories):
    repo, tenant = world.repo, world.tenant

    assert await repo.count_memories_for_targeted_rebuild(tenant) == 5
    assert await repo.count_memories_for_targeted_rebuild(tenant, [], None) == 5
    assert await repo.count_memories_for_targeted_rebuild(uuid4()) == 0


async def test_the_memories_to_rebuild_are_counted_by_the_ids_that_were_given(world, tenant_memories):
    one, two, *_, gone = tenant_memories
    repo, tenant = world.repo, world.tenant

    assert await repo.count_memories_for_targeted_rebuild(tenant, [one.id, two.id, gone.id]) == 2
    assert await repo.count_memories_for_targeted_rebuild(tenant, [uuid4()]) == 0


async def test_the_memories_to_rebuild_are_counted_by_the_project_that_was_given_with_or_without_ids(
    world, tenant_memories,
):
    one, two, *_ = tenant_memories
    repo, tenant = world.repo, world.tenant

    assert await repo.count_memories_for_targeted_rebuild(tenant, [one.id, two.id], "alpha") == 1
    assert await repo.count_memories_for_targeted_rebuild(tenant, None, "alpha") == 3
    assert await repo.count_memories_for_targeted_rebuild(tenant, None, "gamma") == 0
    assert await repo.count_memories_for_targeted_rebuild(tenant, None, "") == 0


async def test_a_project_exists_for_a_user_when_the_user_has_a_memory_in_it(world, tenant_memories):
    repo, tenant = world.repo, world.tenant

    assert await repo.project_exists_for_user(tenant, "beta") is True
    assert await repo.project_exists_for_user(tenant, "gamma") is False
    assert await repo.project_exists_for_user(uuid4(), "beta") is False


async def test_the_memories_to_rebuild_come_in_pages_by_id_without_the_one_the_page_starts_after(world, tenant_memories):
    one, two, three, four, five, _ = tenant_memories
    repo, tenant = world.repo, world.tenant

    first_page = await repo.get_memories_for_targeted_rebuild(tenant, 2)
    second_page = await repo.get_memories_for_targeted_rebuild(tenant, 2, after_id=two.id)
    third_page = await repo.get_memories_for_targeted_rebuild(tenant, 2, after_id=four.id)

    assert ids_of(first_page) == [one.id, two.id]
    assert ids_of(second_page) == [three.id, four.id]
    assert ids_of(third_page) == [five.id]
    assert [(item.tenant_id, item.version) for item in first_page] == [(tenant, 1), (tenant, 1)]
    assert await repo.get_memories_for_targeted_rebuild(tenant, 10, after_id=five.id) == []
    scrolls = world.store.made("scroll")
    assert [kwargs for _, kwargs in scrolls[:3]] == [
        {"limit": 3, "start_from": None}, {"limit": 3, "start_from": two.id}, {"limit": 3, "start_from": four.id},
    ]
    assert scrolls[0][0] == (Scope.of(tenant), repo.memories, Where().also(equals("is_obsolete", False)))


async def test_the_memories_to_rebuild_can_be_narrowed_to_ids_and_to_a_project(world, tenant_memories):
    one, two, three, four, five, gone = tenant_memories
    repo, tenant = world.repo, world.tenant

    assert ids_of(await repo.get_memories_for_targeted_rebuild(tenant, 10)) == [one.id, two.id, three.id, four.id, five.id]
    assert ids_of(await repo.get_memories_for_targeted_rebuild(tenant, 10, memory_ids=[four.id, two.id, gone.id])) == [
        two.id, four.id,
    ]
    assert ids_of(await repo.get_memories_for_targeted_rebuild(tenant, 10, project="alpha")) == [one.id, three.id, five.id]
    assert ids_of(await repo.get_memories_for_targeted_rebuild(tenant, 1, three.id, [one.id, three.id, five.id], "alpha")) == [
        five.id,
    ]


@pytest.mark.parametrize("limit", [1, 2, 3])
@pytest.mark.parametrize("position", [0, 1, 2, 3])
async def test_the_pages_of_the_rebuild_continue_after_the_memory_they_are_given(world, position, limit):
    """A page that did not start after the given memory would be the first page again, which a caller never leaves."""
    created = await seed(world, "One", "Two", "Three", "Four", "Five")
    ids = [memory.id for memory in created]

    page = await world.repo.get_memories_for_targeted_rebuild(world.tenant, limit, after_id=ids[position])

    assert [item.memory.id for item in page] == ids[position + 1:position + 1 + limit]


# ---- what a targeted rebuild writes ------------------------------------------------------------------------------


@dataclass
class Upsert:
    """New embeddings for five ids (one unknown, one obsolete, one updated after its version was read) and the outcome."""

    memories: tuple[Memory, ...]
    vector: list[float]
    updates: list[tuple[UUID, list[float]]]
    result: EmbeddingWrites


@pytest.fixture
async def upserted(world, tenant_memories):
    one, two, three, *_, gone = tenant_memories
    vector = await world.embedder.generate_embedding("A new text")
    await world.repo.update_memory(world.tenant, three.id, MemoryUpdate(importance=7))
    world.store.calls.clear()
    updates = [(memory_id, vector) for memory_id in (one.id, uuid4(), gone.id, three.id, two.id)]
    read_at = {one.id: 1, gone.id: 1, three.id: 1, two.id: 1}
    result = await world.repo.upsert_targeted_embeddings(world.tenant, updates, read_at)
    return Upsert(tenant_memories, vector, updates, result)


async def test_new_embeddings_are_written_only_for_active_memories_at_the_version_that_was_read(upserted, world):
    one, two, *_ = upserted.memories

    assert upserted.result == EmbeddingWrites(written=[one.id, two.id], failed=[])
    assert world.store.names().count("replace") == 2


async def test_a_new_embedding_replaces_the_point_at_the_version_that_was_read_and_is_confirmed_by_its_token(
    upserted, world,
):
    one = upserted.memories[0]
    generation = await world.repo.live_generation()

    ((one_args, one_kwargs), _) = world.store.made("replace")
    assert one_args[:2] == (Scope.of(world.tenant), generation)
    assert one_kwargs == {"expected_version": 1}
    refreshed = one_args[2]
    token = refreshed.payload[TOKEN_KEY]
    assert TOKEN.fullmatch(token)
    assert refreshed.payload[VERSION_KEY] == 2
    assert world.store.made("landed")[0] == ((Scope.of(world.tenant), generation, one.id), {"from_version": 1, "token": token})


async def test_the_versions_of_all_the_ids_are_read_at_once_by_the_quorum_of_the_nodes(upserted, world):
    generation = await world.repo.live_generation()

    [get_call] = world.store.made("get")
    assert get_call[0] == (Scope.of(world.tenant), generation, [memory_id for memory_id, _ in upserted.updates])
    assert get_call[1] == {"quorum": True}


async def test_the_node_holds_the_new_vector_of_a_written_memory_and_not_that_of_one_updated_since(upserted, world):
    one, two, three, *_ = upserted.memories

    stored_points = await held(world, await world.repo.live_generation(), one, two, three)

    assert stored_points[one.id].vector == pytest.approx(upserted.vector)
    assert stored_points[one.id].payload[VERSION_KEY] == 2
    assert stored_points[three.id].payload[VERSION_KEY] == 2
    assert stored_points[three.id].vector != pytest.approx(upserted.vector)


async def test_a_memory_that_cannot_be_written_is_reported_and_logged_and_the_others_are_written_anyway(
    world, tenant_memories, caplog,
):
    one, two, three, *_ = tenant_memories
    repo, tenant, store = world.repo, world.tenant, world.store
    vector = await world.embedder.generate_embedding("A new text")
    real_replace = store.real.replace

    async def fails_for_the_second(scope: Scope, collection: str, point: NewPoint, **kwargs: Any) -> None:
        if point.id == two.id:
            raise RuntimeError("the node did not take it")
        await real_replace(scope, collection, point, **kwargs)

    store.replaced["replace"] = fails_for_the_second

    with caplog.at_level(logging.ERROR, logger=module.logger.name):
        result = await repo.upsert_targeted_embeddings(
            tenant, [(one.id, vector), (two.id, vector), (three.id, vector)], {one.id: 1, two.id: 1, three.id: 1},
        )

    assert result == EmbeddingWrites(written=[one.id, three.id], failed=[two.id])
    [record] = [record for record in caplog.records if record.name == module.logger.name]
    assert (record.levelno, record.getMessage()) == (logging.ERROR, "embedding write failed")
    assert (record.memory_id, record.user_id) == (str(two.id), str(tenant))
    assert record.exc_info is not None
    assert str(record.exc_info[1]) == "the node did not take it"


async def test_a_write_that_the_node_did_not_apply_is_neither_written_nor_failed(world, tenant_memories):
    one, *_ = tenant_memories
    vector = await world.embedder.generate_embedding("A new text")

    async def not_the_one_that_landed(*_args: Any, **_kwargs: Any) -> bool:
        return False

    world.store.replaced["landed"] = not_the_one_that_landed

    result = await world.repo.upsert_targeted_embeddings(world.tenant, [(one.id, vector)], {one.id: 1})

    assert result == EmbeddingWrites(written=[], failed=[])
