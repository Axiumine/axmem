# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Re-embedding in Qdrant: what the scan, the copy and the links rebuild ask of the node, exactly.

The repository works through the store, so the tests note every call it makes there (a spy that passes each call on, and
can stand in for a request that fails or has no effect) and read the collections back from the node. The node and the
embedder are the real ones.
"""
import logging
from dataclasses import dataclass
from typing import Any

import pytest

from app.config.settings import settings
from app.models.memory_models import Memory, MemoryUpdate
from app.repositories.qdrant import memory_reembedding as module
from app.repositories.qdrant.payloads import payload_of
from app.repositories.qdrant.store import SYSTEM, TOKEN_KEY, VERSION_KEY, NewPoint
from tests.reembedding_support import TOKEN, World, held, hold, make_world, seed, stored


@pytest.fixture
async def world(isolated_stack, tensorrt_embedder, monkeypatch):
    return make_world(isolated_stack, tensorrt_embedder, monkeypatch)


# ---- the scan: what a generation lacks ---------------------------------------------------------------------------


async def test_the_scan_names_the_memories_a_target_lacks_or_holds_at_an_older_version_page_by_page(world):
    repo = world.repo
    first, second, third, fourth, fifth = await seed(world, "First", "Second", "Third", "Fourth", "Fifth")
    await repo.update_memory(world.tenant, third.id, MemoryUpdate(importance=6))
    await repo.update_memory(world.tenant, fourth.id, MemoryUpdate(importance=6))
    source = await repo.live_generation()
    target = await repo.create_generation()
    await hold(world, target, first, 1)
    await hold(world, target, third, 1)
    await hold(world, target, fourth, 3)
    world.store.calls.clear()

    page_one = await repo.outdated_in(source, target, 2, None)
    page_two = await repo.outdated_in(source, target, 2, page_one[1])
    page_three = await repo.outdated_in(source, target, 2, page_two[1])

    assert [(item.memory.id, item.tenant_id, item.version) for item in page_one[0]] == [(second.id, world.tenant, 1)]
    assert page_one[1] == third.id
    assert [(item.memory.id, item.memory.title, item.version) for item in page_two[0]] == [(third.id, "Third", 2)]
    assert page_two[1] == fifth.id
    assert [(item.memory.id, item.version) for item in page_three[0]] == [(fifth.id, 1)]
    assert page_three[1] is None
    assert world.store.names() == ["page", "get_agreed"] * 3
    pages = world.store.made("page")
    assert pages == [
        ((SYSTEM, source), {"limit": 2, "start_from": None, "quorum": True}),
        ((SYSTEM, source), {"limit": 2, "start_from": third.id, "quorum": True}),
        ((SYSTEM, source), {"limit": 2, "start_from": fifth.id, "quorum": True}),
    ]
    reads = world.store.made("get_agreed")
    assert reads == [
        ((SYSTEM, target, [first.id, second.id]), {"with_payload": [VERSION_KEY]}),
        ((SYSTEM, target, [third.id, fourth.id]), {"with_payload": [VERSION_KEY]}),
        ((SYSTEM, target, [fifth.id]), {"with_payload": [VERSION_KEY]}),
    ]


# ---- the copy ----------------------------------------------------------------------------------------------------


@dataclass
class Copy:
    """A copy of five memories into a generation that holds four of them: one at the same version, one at a newer one,
    two at an older one, and one not at all."""

    target: str
    memories: list[Memory]  # the same, the newer, the absent, the older and the much older
    items: list
    copied: int


@pytest.fixture
async def made_copy(world):
    memories = await seed(world, "Same", "Newer", "Absent", "Older", "Much older")
    same, newer, absent, older, much_older = memories
    target = await world.repo.create_generation()
    await hold(world, target, same, 3)
    await hold(world, target, newer, 5)
    await hold(world, target, older, 1)
    await hold(world, target, much_older, 1)
    items = [
        (stored(world, memory, version), await world.embedder.generate_embedding(f"new {memory.title}"))
        for memory, version in ((same, 3), (newer, 4), (absent, 2), (older, 2), (much_older, 3))
    ]
    world.store.calls.clear()
    return Copy(target, memories, items, await world.repo.copy_into(target, items))


def inserted_token(world: World) -> str:
    [(insert_args, _)] = world.store.made("insert")
    [created] = insert_args[2]
    return created.payload[TOKEN_KEY]


async def test_a_copy_counts_what_it_created_and_what_it_replaced(made_copy):
    assert made_copy.copied == 3


async def test_a_copy_reads_the_versions_of_all_and_reads_back_only_what_it_wrote(world, made_copy):
    _, _, absent, older, much_older = made_copy.memories

    assert world.store.names() == ["get_agreed", "insert", "replace", "replace", "get_agreed"]
    ((read_args, read_kwargs), (readback_args, readback_kwargs)) = world.store.made("get_agreed")
    assert read_args == (SYSTEM, made_copy.target, [memory.id for memory in made_copy.memories])
    assert read_kwargs == {"with_payload": [VERSION_KEY]}
    assert readback_args == (SYSTEM, made_copy.target, [absent.id, older.id, much_older.id])
    assert readback_kwargs == {"with_payload": [VERSION_KEY]}


async def test_a_copy_inserts_what_is_absent_as_one_point_with_a_token_of_32_hex_digits(world, made_copy):
    absent = made_copy.memories[2]

    [(insert_args, insert_kwargs)] = world.store.made("insert")
    assert insert_args[:2] == (SYSTEM, made_copy.target)
    assert insert_kwargs == {}
    [created] = insert_args[2]
    token = created.payload[TOKEN_KEY]
    assert TOKEN.fullmatch(token)
    assert created == NewPoint(absent.id, made_copy.items[2][1], payload_of(made_copy.items[2][0], token=token))


async def test_a_copy_replaces_what_is_older_under_the_token_of_the_insert_and_only_while_it_is_older(world, made_copy):
    token = inserted_token(world)

    replaced = world.store.made("replace")
    for (args, kwargs), (item, vector), version in zip(replaced, made_copy.items[3:], (2, 3), strict=True):
        assert args == (SYSTEM, made_copy.target, NewPoint(item.memory.id, vector, payload_of(item, token=token)))
        assert kwargs == {"older_than": version}


async def test_a_copy_leaves_the_same_and_the_newer_as_they_were_and_writes_the_others_with_their_vectors(
    world, made_copy,
):
    token = inserted_token(world)
    _, _, absent, older, _ = made_copy.memories

    points = await held(world, made_copy.target, *made_copy.memories)

    assert [(points[memory.id].payload[VERSION_KEY], points[memory.id].payload[TOKEN_KEY]) for memory in (
        made_copy.memories
    )] == [(3, "seeded"), (5, "seeded"), (2, token), (2, token), (3, token)]
    assert points[absent.id].vector == pytest.approx(made_copy.items[2][1])
    assert points[older.id].vector == pytest.approx(made_copy.items[3][1])


async def test_a_second_copy_of_the_same_items_writes_nothing_and_reads_nothing_back(world, made_copy):
    world.store.calls.clear()

    assert await world.repo.copy_into(made_copy.target, made_copy.items) == 0

    assert world.store.names() == ["get_agreed", "insert"]  # nothing to write, so nothing is read back
    assert world.store.made("insert")[0][0][2] == []


async def test_a_copy_does_not_count_a_replacement_that_did_not_take_effect(world):
    repo = world.repo
    absent, older = await seed(world, "Absent", "Older")
    target = await repo.create_generation()
    await hold(world, target, older, 1)
    items = [(stored(world, absent, 2), [0.5] * 384), (stored(world, older, 2), [0.25] * 384)]

    async def does_not_take_effect(*_args: Any, **_kwargs: Any) -> None:
        return None

    world.store.replaced["replace"] = does_not_take_effect

    assert await repo.copy_into(target, items) == 1

    assert (await held(world, target, older))[older.id].payload[VERSION_KEY] == 1
    assert (await held(world, target, absent))[absent.id].payload[VERSION_KEY] == 2


# ---- the links ---------------------------------------------------------------------------------------------------


@pytest.fixture
async def relayout(world, monkeypatch):
    """Four memories with three links, a layout of two shards that the links do not have, and batches of three."""
    repo = world.repo
    memories = await seed(world, "One", "Two", "Three", "Four")
    await repo.create_links_batch(world.tenant, memories[0].id, [memories[1].id, memories[2].id])
    await repo.create_links_batch(world.tenant, memories[3].id, [memories[1].id])
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    monkeypatch.setattr(module, "LINK_COPY_BATCH", 3)
    world.store.calls.clear()
    return world.stack.schema.names


async def test_the_links_are_copied_in_batches_that_are_sent_when_they_fill_up_and_once_more_at_the_end(
    world, relayout,
):
    names = relayout

    assert await world.repo.rebuild_links() is True

    inserted = [(args[1], len(args[2])) for args, _ in world.store.made("insert")]
    assert inserted == [(names.links_staging, 3), (names.links_staging, 0), (names.links, 3), (names.links, 0)]
    assert world.store.made("iterate") == [
        ((SYSTEM, names.links), {"quorum": True}), ((SYSTEM, names.links_staging), {"quorum": True}),
    ]


async def test_a_rebuild_of_the_links_is_logged_once_with_the_collection_that_was_rebuilt(world, relayout, caplog):
    with caplog.at_level(logging.INFO, logger=module.logger.name):
        assert await world.repo.rebuild_links() is True

    [record] = [record for record in caplog.records if record.name == module.logger.name]
    assert (record.levelno, record.getMessage()) == (logging.INFO, "Rebuilt the links collection in the configured layout")
    assert record.collection == relayout.links


async def test_links_that_are_in_the_configured_layout_are_neither_copied_nor_logged(world, relayout, caplog):
    assert await world.repo.rebuild_links() is True
    world.store.calls.clear()

    with caplog.at_level(logging.INFO, logger=module.logger.name):
        assert await world.repo.rebuild_links() is False

    assert [record for record in caplog.records if record.name == module.logger.name] == []
    assert world.store.made("insert") == []
