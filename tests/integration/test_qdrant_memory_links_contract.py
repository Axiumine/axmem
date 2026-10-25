"""The exact contract of the memory links: the cap, the order, what is stored for a link and what is logged about it.

``test_qdrant_search_filters.py`` and ``test_qdrant_links_rebuild.py`` check that linked memories come back most
important first and that links survive a rebuild. This file pins the values around that: the default cap, the order of
equally important neighbours, how many points a lookup reads and with how much of each, the payload and the id of a link,
which links a batch reports as new, and the line each change logs. The memories are the real ones of the Qdrant
repository, and the calls it makes to the store are watched, never replaced.
"""
import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.memory_models import MemoryCreate
from app.repositories.qdrant import memory_links as links_module
from app.repositories.qdrant.payloads import link_id
from app.repositories.qdrant.schema import LINK_VECTOR
from app.repositories.qdrant.store import TENANT_KEY, TOKEN_KEY, Scope


def memory(title: str, importance: int = 5) -> MemoryCreate:
    return MemoryCreate(
        title=title, content=f"Content of {title}", context="Links", keywords=["links"], tags=["contract"], importance=importance,
    )


async def hub_with_neighbours(repo, tenant, importances: tuple[int, ...]):
    """A memory linked to one neighbour of each importance given; the hub and the neighbours, in that order."""
    neighbours = [await repo.create_memory(tenant, memory(f"Neighbour {number}", importance)) for number, importance in enumerate(importances)]
    hub = await repo.create_memory(tenant, memory("Hub"))
    await repo.create_links_batch(tenant, hub.id, [neighbour.id for neighbour in neighbours])
    return hub, neighbours


class StoreCalls:
    """The calls a repository makes to its store, in order, as ``(method, arguments, keyword arguments)``."""

    def __init__(self, store, monkeypatch, *methods: str) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        for method in methods:
            monkeypatch.setattr(store, method, self._watching(method, getattr(store, method)))

    def _watching(self, method, original):
        async def watched(*args, **kwargs):
            self.calls.append((method, args, kwargs))
            return await original(*args, **kwargs)

        return watched

    def of(self, method: str, collection: str) -> list[tuple[tuple, dict]]:
        return [(args, kwargs) for name, args, kwargs in self.calls if name == method and args[1] == collection]


def records_of(caplog, text: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == text]


# ---- what a lookup returns ----------------------------------------------------------------------------------------------


async def test_five_linked_memories_are_returned_unless_more_or_fewer_are_asked_for(memory_repository):
    tenant = uuid4()
    hub, neighbours = await hub_with_neighbours(memory_repository, tenant, (2, 9, 4, 7, 5, 8, 3))

    default = await memory_repository.get_linked_memories(tenant, hub.id, None)
    fewer = await memory_repository.get_linked_memories(tenant, hub.id, None, 2)
    more = await memory_repository.get_linked_memories(tenant, hub.id, None, max_links=20)

    assert [found.importance for found in default] == [9, 8, 7, 5, 4]
    assert [found.importance for found in fewer] == [9, 8]
    assert [found.importance for found in more] == [9, 8, 7, 5, 4, 3, 2]
    assert {found.id for found in more} == {neighbour.id for neighbour in neighbours}


@pytest.mark.parametrize("chunk", [1000, 3])
async def test_equally_important_neighbours_come_back_in_the_order_of_their_ids(memory_repository, monkeypatch, chunk):
    """The same ones, in the same order, however many lookups it takes to read them."""
    monkeypatch.setattr(links_module, "LINK_CHUNK", chunk)
    tenant = uuid4()
    hub, neighbours = await hub_with_neighbours(memory_repository, tenant, (5,) * 7)

    linked = await memory_repository.get_linked_memories(tenant, hub.id, None, max_links=4)

    assert [found.id for found in linked] == sorted((neighbour.id for neighbour in neighbours), key=str)[:4]


async def test_a_memory_is_never_among_its_own_linked_memories_and_the_excluded_ones_do_not_count_towards_the_cap(
    memory_repository,
):
    tenant = uuid4()
    hub, neighbours = await hub_with_neighbours(memory_repository, tenant, (9, 8, 7, 6))

    linked = await memory_repository.get_linked_memories(tenant, hub.id, None, 2, exclude_ids=[neighbours[0].id])
    unexcluded = await memory_repository.get_linked_memories(tenant, hub.id, None, 10, exclude_ids=None)

    assert [found.importance for found in linked] == [8, 7]
    assert hub.id not in {found.id for found in unexcluded}
    assert len(unexcluded) == 4


async def test_a_lookup_that_one_request_can_answer_reads_the_whole_memories_at_once(memory_repository, monkeypatch):
    tenant = uuid4()
    hub, neighbours = await hub_with_neighbours(memory_repository, tenant, (3, 9, 6))
    store = StoreCalls(memory_repository.store, monkeypatch, "scroll", "get")

    linked = await memory_repository.get_linked_memories(tenant, hub.id, None, 2)

    assert [found.importance for found in linked] == [9, 6]
    [(args, kwargs)] = store.of("scroll", memory_repository.memories)
    assert args[0] == Scope.of(tenant)
    assert (kwargs["limit"], kwargs["with_payload"]) == (3, True)
    assert store.of("get", memory_repository.memories) == []  # nothing is read twice


async def test_a_lookup_over_several_requests_reads_a_number_per_memory_and_the_best_ones_in_full(memory_repository, monkeypatch):
    monkeypatch.setattr(links_module, "LINK_CHUNK", 2)
    tenant = uuid4()
    hub, neighbours = await hub_with_neighbours(memory_repository, tenant, (3, 9, 6, 1, 8))
    store = StoreCalls(memory_repository.store, monkeypatch, "scroll", "get")

    linked = await memory_repository.get_linked_memories(tenant, hub.id, None, 2)

    assert [found.importance for found in linked] == [9, 8]
    numbers = [kwargs["limit"] for _args, kwargs in store.of("scroll", memory_repository.memories) if kwargs["with_payload"] == ["importance"]]
    assert numbers == [2, 2, 1]  # the neighbours in requests of the size of a chunk
    [(args, kwargs)] = store.of("get", memory_repository.memories)
    assert (args[0], list(args[2])) == (Scope.of(tenant), [found.id for found in linked])
    assert kwargs == {}


async def test_only_the_neighbours_that_are_not_obsolete_and_in_the_project_are_looked_up(memory_repository, monkeypatch):
    tenant = uuid4()
    inside = await memory_repository.create_memory(tenant, MemoryCreate(
        title="Inside", content="In the project", context="Links", keywords=["links"], tags=["contract"], importance=5, project="billing",
    ))
    outside = await memory_repository.create_memory(tenant, memory("Outside"))
    hub = await memory_repository.create_memory(tenant, memory("Hub"))
    await memory_repository.create_links_batch(tenant, hub.id, [inside.id, outside.id])

    in_project = await memory_repository.get_linked_memories(tenant, hub.id, "billing")
    anywhere = await memory_repository.get_linked_memories(tenant, hub.id, None)
    await memory_repository.mark_obsolete(tenant, inside.id, "superseded")
    still_current = await memory_repository.get_linked_memories(tenant, hub.id, None)

    assert [found.id for found in in_project] == [inside.id]
    assert {found.id for found in anywhere} == {inside.id, outside.id}
    assert [found.id for found in still_current] == [outside.id]


# ---- what a link is -----------------------------------------------------------------------------------------------------


async def test_a_link_is_a_point_of_its_own_with_an_id_a_zero_vector_and_the_two_memories_in_order(memory_repository):
    tenant = uuid4()
    first = await memory_repository.create_memory(tenant, memory("First"))
    second = await memory_repository.create_memory(tenant, memory("Second"))
    before = datetime.now().astimezone()

    created = await memory_repository.create_links_batch(tenant, second.id, [first.id])

    assert created == [first.id]
    [link] = await memory_repository.store.get(
        Scope.of(tenant), memory_repository.links, [link_id(tenant, first.id, second.id)], with_vectors=True,
    )
    low, high = sorted((first.id, second.id))
    assert link.vector == LINK_VECTOR
    assert {key: value for key, value in link.payload.items() if key not in ("created_at", TOKEN_KEY)} == {
        TENANT_KEY: str(tenant), "a": str(low), "b": str(high),
    }
    assert set(link.payload) == {TENANT_KEY, "a", "b", "created_at", TOKEN_KEY}
    assert before <= datetime.fromisoformat(link.payload["created_at"]) <= datetime.now().astimezone()


async def test_the_links_of_a_batch_share_one_claim_token_and_one_moment(memory_repository):
    tenant = uuid4()
    hub, neighbours = await hub_with_neighbours(memory_repository, tenant, (5, 5, 5))

    links = await memory_repository.store.get(
        Scope.of(tenant), memory_repository.links, [link_id(tenant, hub.id, neighbour.id) for neighbour in neighbours],
    )

    assert len(links) == 3
    assert len({link.payload[TOKEN_KEY] for link in links}) == 1
    assert len({link.payload["created_at"] for link in links}) == 1


async def test_a_batch_hands_the_store_links_that_name_their_tenant_and_a_moment_taken_in_utc(memory_repository, monkeypatch):
    tenant = uuid4()
    first = await memory_repository.create_memory(tenant, memory("First"))
    second = await memory_repository.create_memory(tenant, memory("Second"))
    zones: list = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            zones.append(tz)
            return super().now(tz)

    monkeypatch.setattr(links_module, "datetime", Clock)
    calls = StoreCalls(memory_repository.store, monkeypatch, "insert")

    await memory_repository.create_links_batch(tenant, second.id, [first.id])

    [(args, _kwargs)] = calls.of("insert", memory_repository.links)
    [point] = args[2]
    assert point.payload[TENANT_KEY] == str(tenant)  # the store stamps the tenant of the scope on it as well, so only the call shows it
    assert zones == [UTC]


async def test_a_batch_reports_the_targets_it_linked_in_the_order_given_and_leaves_the_rest_alone(memory_repository):
    tenant = uuid4()
    hub, (first, second, third) = await hub_with_neighbours(memory_repository, tenant, (5, 5, 5))
    other = await memory_repository.create_memory(tenant, memory("Other"))
    fresh = [await memory_repository.create_memory(tenant, memory(f"Fresh {number}")) for number in range(3)]

    created = await memory_repository.create_links_batch(
        tenant, other.id, [fresh[2].id, other.id, uuid4(), fresh[0].id, fresh[2].id, first.id, fresh[1].id],
    )
    again = await memory_repository.create_links_batch(tenant, other.id, [fresh[0].id, second.id, third.id])

    assert created == [fresh[2].id, fresh[0].id, first.id, fresh[1].id]
    assert again == [second.id, third.id]  # the link to the first of these exists already
    assert len(await memory_repository.get_linked_memories(tenant, other.id, None, 20)) == 6
    assert hub.id not in {found.id for found in await memory_repository.get_linked_memories(tenant, other.id, None, 20)}


async def test_nothing_is_linked_from_a_memory_that_does_not_exist_for_the_tenant(memory_repository, monkeypatch):
    tenant = uuid4()
    mine = await memory_repository.create_memory(tenant, memory("Mine"))
    foreign = await memory_repository.create_memory(uuid4(), memory("Foreign"))
    store = StoreCalls(memory_repository.store, monkeypatch, "insert")

    assert await memory_repository.create_links_batch(tenant, uuid4(), [mine.id]) == []
    assert await memory_repository.create_links_batch(tenant, mine.id, [foreign.id]) == []
    assert await memory_repository.create_links_batch(tenant, foreign.id, [mine.id]) == []

    assert store.calls == []


async def test_a_batch_reads_back_the_claim_tokens_of_its_links_from_a_majority_and_asks_for_the_targets_by_agreement(
    memory_repository, monkeypatch,
):
    tenant = uuid4()
    source = await memory_repository.create_memory(tenant, memory("Source"))
    target = await memory_repository.create_memory(tenant, memory("Target"))
    store = StoreCalls(memory_repository.store, monkeypatch, "get_agreed", "get")

    await memory_repository.create_links_batch(tenant, source.id, [target.id])

    [(args, kwargs)] = store.of("get_agreed", memory_repository.memories)
    assert (args[0], list(args[2]), kwargs) == (Scope.of(tenant), [source.id, target.id], {"with_payload": False})
    [(args, kwargs)] = store.of("get", memory_repository.links)
    assert (args[0], list(args[2]), kwargs) == (
        Scope.of(tenant), [link_id(tenant, source.id, target.id)], {"with_payload": [TOKEN_KEY], "quorum": True},
    )


# ---- what is logged -----------------------------------------------------------------------------------------------------


async def test_the_links_a_batch_created_are_logged_with_the_tenant_the_source_and_the_new_targets(memory_repository, caplog):
    tenant = uuid4()
    hub, (existing,) = await hub_with_neighbours(memory_repository, tenant, (5,))
    new = [await memory_repository.create_memory(tenant, memory(f"New {number}")) for number in range(2)]
    caplog.set_level(logging.INFO, logger=links_module.logger.name)

    await memory_repository.create_links_batch(tenant, hub.id, [new[1].id, existing.id, new[0].id])

    [record] = records_of(caplog, "Memory links created")
    assert (record.user_id, record.source_id, record.links_created, record.levelno) == (
        str(tenant), str(hub.id), [str(new[1].id), str(new[0].id)], logging.INFO,
    )


async def test_a_batch_that_created_nothing_new_still_says_so_in_the_log(memory_repository, caplog):
    tenant = uuid4()
    hub, (existing,) = await hub_with_neighbours(memory_repository, tenant, (5,))
    caplog.set_level(logging.INFO, logger=links_module.logger.name)

    assert await memory_repository.create_links_batch(tenant, hub.id, [existing.id]) == []

    [record] = records_of(caplog, "Memory links created")
    assert record.links_created == []


@pytest.mark.parametrize("swapped", [False, True])
async def test_a_link_is_removed_whichever_memory_names_it_first_and_the_removal_is_logged(memory_repository, monkeypatch, caplog, swapped):
    tenant = uuid4()
    hub, (neighbour,) = await hub_with_neighbours(memory_repository, tenant, (5,))
    first, second = (neighbour, hub) if swapped else (hub, neighbour)
    store = StoreCalls(memory_repository.store, monkeypatch, "get", "delete")
    caplog.set_level(logging.INFO, logger=links_module.logger.name)

    removed = await memory_repository.unlink_memories(tenant, first.id, second.id)
    again = await memory_repository.unlink_memories(tenant, first.id, second.id)

    identifier = link_id(tenant, hub.id, neighbour.id)
    assert (removed, again) == (True, False)
    assert await memory_repository.get_linked_memories(tenant, hub.id, None) == []
    [(args, kwargs), (args_again, _kwargs)] = store.of("get", memory_repository.links)
    assert (args[0], list(args[2]), kwargs) == (Scope.of(tenant), [identifier], {"with_payload": False, "quorum": True})
    assert [(args[0], args[1], list(args[2])) for _name, args, _kwargs in store.calls if _name == "delete"] == [
        (Scope.of(tenant), memory_repository.links, [identifier]),
    ]
    first_record, second_record = records_of(caplog, "Memory link removed")
    assert [(record.user_id, record.source_id, record.target_id, record.deleted, record.levelno) for record in (first_record, second_record)] == [
        (str(tenant), str(first.id), str(second.id), True, logging.INFO),
        (str(tenant), str(first.id), str(second.id), False, logging.INFO),
    ]


async def test_another_tenant_cannot_remove_a_link(memory_repository):
    tenant = uuid4()
    hub, (neighbour,) = await hub_with_neighbours(memory_repository, tenant, (5,))

    assert await memory_repository.unlink_memories(uuid4(), hub.id, neighbour.id) is False
    assert [found.id for found in await memory_repository.get_linked_memories(tenant, hub.id, None)] == [neighbour.id]
