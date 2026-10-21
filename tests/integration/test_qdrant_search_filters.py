"""The filters and orderings of search and of linked-memory lookups, on a real Qdrant node.

A filter that matched everything, or an ordering that was never applied, would still return plausible results
for the broad tests, so each rule is pinned by a case in which it must make a difference: the memory on one side
of the rule is returned, the one on the other side is not.

The same module checks that the lookups that follow a write ask a majority of the replicas, because in a cluster
the next request may reach a node that has not yet received the write.
"""
from uuid import uuid4

import pytest

from app.models.memory_models import MemoryCreate
from app.repositories.qdrant import memory_links as links_module
from app.repositories.qdrant.store import QdrantStore

TOPIC = "Rotating the signing keys of the payment gateway every ninety days"


def memory_about_the_topic(title: str, importance: int, project: str | None = None) -> MemoryCreate:
    """Memories that all match the same query equally well, so only the rule under test tells them apart."""
    return MemoryCreate(
        title=title, content=TOPIC, context="Key rotation policy", keywords=["keys", "rotation"],
        tags=["security"], importance=importance, project=project,
    )


async def search(repo, tenant, **overrides):
    arguments = {
        "user_id": tenant, "query": TOPIC, "query_context": "key rotation", "k": 10,
        "importance_threshold": None, "project": None, "exclude_ids": None,
    }
    return await repo.search_scored(**{**arguments, **overrides})


async def test_the_importance_threshold_drops_what_is_below_it_and_keeps_what_is_at_it(memory_repository):
    tenant = uuid4()
    below = await memory_repository.create_memory(tenant, memory_about_the_topic("Below", importance=4))
    at = await memory_repository.create_memory(tenant, memory_about_the_topic("At", importance=5))
    above = await memory_repository.create_memory(tenant, memory_about_the_topic("Above", importance=9))

    found = {memory.id for memory, _ in await search(memory_repository, tenant, importance_threshold=5)}

    assert found == {at.id, above.id}
    everything = {memory.id for memory, _ in await search(memory_repository, tenant)}
    assert everything == {below.id, at.id, above.id}


async def test_excluded_ids_are_left_out_of_a_search(memory_repository):
    tenant = uuid4()
    kept = await memory_repository.create_memory(tenant, memory_about_the_topic("Kept", importance=5))
    left_out = await memory_repository.create_memory(tenant, memory_about_the_topic("Left out", importance=5))
    also_left_out = await memory_repository.create_memory(tenant, memory_about_the_topic("Also left out", importance=5))

    found = {
        memory.id
        for memory, _ in await search(memory_repository, tenant, exclude_ids=[left_out.id, also_left_out.id])
    }

    assert found == {kept.id}


async def test_a_project_scope_keeps_a_search_to_that_project(memory_repository):
    tenant = uuid4()
    inside = await memory_repository.create_memory(tenant, memory_about_the_topic("Inside", 5, project="billing"))
    await memory_repository.create_memory(tenant, memory_about_the_topic("Outside", 5, project="search"))
    await memory_repository.create_memory(tenant, memory_about_the_topic("Unscoped", 5))

    found = {memory.id for memory, _ in await search(memory_repository, tenant, project="billing")}

    assert found == {inside.id}


async def linked_hub(repo, tenant, neighbours):
    """A memory linked to every neighbour; returns it."""
    hub = await repo.create_memory(tenant, memory_about_the_topic("Hub", importance=5))
    await repo.create_links_batch(tenant, hub.id, [neighbour.id for neighbour in neighbours])
    return hub


async def test_linked_memories_come_back_most_important_first_and_are_capped(memory_repository):
    tenant = uuid4()
    neighbours = [
        await memory_repository.create_memory(tenant, memory_about_the_topic(f"Neighbour {importance}", importance))
        for importance in (3, 9, 6, 1, 8)
    ]
    hub = await linked_hub(memory_repository, tenant, neighbours)

    linked = await memory_repository.get_linked_memories(tenant, hub.id, project=None, max_links=3)

    assert [memory.importance for memory in linked] == [9, 8, 6]


@pytest.mark.parametrize("chunk", [2, 3, 10])
async def test_the_most_important_linked_memories_are_returned_however_many_lookups_it_takes(memory_repository, monkeypatch, chunk):
    """A memory with more links than one lookup holds still returns its most important neighbours, not the first ones found."""
    monkeypatch.setattr(links_module, "LINK_CHUNK", chunk)
    tenant = uuid4()
    importances = (3, 9, 6, 1, 8, 2, 7, 5, 4)
    neighbours = [
        await memory_repository.create_memory(tenant, memory_about_the_topic(f"Neighbour {importance}", importance))
        for importance in importances
    ]
    hub = await linked_hub(memory_repository, tenant, neighbours)

    linked = await memory_repository.get_linked_memories(tenant, hub.id, project=None, max_links=4)

    assert [memory.importance for memory in linked] == [9, 8, 7, 6]
    assert {memory.id for memory in linked} == {neighbour.id for neighbour in neighbours if neighbour.importance >= 6}
    assert all(memory.linked_memory_ids for memory in linked)


async def test_linked_memories_over_several_lookups_leave_out_the_obsolete_and_the_excluded(memory_repository, monkeypatch):
    monkeypatch.setattr(links_module, "LINK_CHUNK", 2)
    tenant = uuid4()
    neighbours = [
        await memory_repository.create_memory(tenant, memory_about_the_topic(f"Neighbour {importance}", importance))
        for importance in (9, 8, 7, 6, 5)
    ]
    hub = await linked_hub(memory_repository, tenant, neighbours)
    await memory_repository.mark_obsolete(tenant, neighbours[0].id, "superseded")

    linked = await memory_repository.get_linked_memories(
        tenant, hub.id, project=None, max_links=2, exclude_ids=[neighbours[1].id],
    )

    assert [memory.importance for memory in linked] == [7, 6]


async def test_linked_memories_none_of_which_qualifies_come_back_empty_over_several_lookups(memory_repository, monkeypatch):
    monkeypatch.setattr(links_module, "LINK_CHUNK", 2)
    tenant = uuid4()
    neighbours = [
        await memory_repository.create_memory(tenant, memory_about_the_topic(f"Neighbour {number}", 5, project="search"))
        for number in range(5)
    ]
    hub = await linked_hub(memory_repository, tenant, neighbours)

    assert await memory_repository.get_linked_memories(tenant, hub.id, project="billing") == []


async def test_linked_memories_can_be_kept_to_a_project(memory_repository):
    tenant = uuid4()
    same = await memory_repository.create_memory(tenant, memory_about_the_topic("Same project", 5, project="billing"))
    other = await memory_repository.create_memory(tenant, memory_about_the_topic("Other project", 9, project="search"))
    hub = await linked_hub(memory_repository, tenant, [same, other])

    kept = await memory_repository.get_linked_memories(tenant, hub.id, project="billing")
    everything = await memory_repository.get_linked_memories(tenant, hub.id, project=None)

    assert [memory.id for memory in kept] == [same.id]
    assert {memory.id for memory in everything} == {same.id, other.id}


async def test_linked_memories_leave_out_the_excluded_ones_before_the_cap(memory_repository):
    tenant = uuid4()
    top = await memory_repository.create_memory(tenant, memory_about_the_topic("Top", 9))
    middle = await memory_repository.create_memory(tenant, memory_about_the_topic("Middle", 6))
    bottom = await memory_repository.create_memory(tenant, memory_about_the_topic("Bottom", 2))
    hub = await linked_hub(memory_repository, tenant, [top, middle, bottom])

    linked = await memory_repository.get_linked_memories(
        tenant, hub.id, project=None, max_links=2, exclude_ids=[top.id],
    )

    assert [memory.id for memory in linked] == [middle.id, bottom.id]


class ReadRecorder:
    """The store of a repository, recording whether each read asked for a majority of the replicas."""

    def __init__(self, store: QdrantStore):
        self.store = store
        self.reads: list[tuple[str, bool]] = []

    async def get(self, scope, collection, ids, **kwargs):
        self.reads.append((collection, kwargs.get("quorum", False)))
        return await self.store.get(scope, collection, ids, **kwargs)

    async def get_agreed(self, scope, collection, ids, **kwargs):
        self.reads.append((collection, True))  # a majority read, repeated while the replicas disagree
        return await self.store.get_agreed(scope, collection, ids, **kwargs)

    def __getattr__(self, name):
        return getattr(self.store, name)


@pytest.fixture
def recorder(memory_repository):
    recorder = ReadRecorder(memory_repository.store)
    memory_repository.store = recorder
    yield recorder
    memory_repository.store = recorder.store


async def test_the_lookups_that_follow_a_write_ask_a_majority_of_the_replicas(memory_repository, recorder):
    """The vector of the new memory, and the existence of the memories to link, are read where a stale node would hide them."""
    tenant = uuid4()
    first = await memory_repository.create_memory(tenant, memory_about_the_topic("First", 5))
    second = await memory_repository.create_memory(tenant, memory_about_the_topic("Second", 5))
    recorder.reads.clear()

    await memory_repository.find_similar_memories_scored(tenant, second.id, max_links=3)
    await memory_repository.find_obsolete_matches(tenant, second.id)
    await memory_repository.create_links_batch(tenant, second.id, [first.id])

    memories = memory_repository.memories
    assert recorder.reads[0] == (memories, True)  # the vector of the source, to find similar memories
    assert recorder.reads[1] == (memories, True)  # the vector of the source, to find obsolete matches
    assert (memories, True) in recorder.reads[2:]  # the source and the targets of the links
    assert all(quorum for collection, quorum in recorder.reads if collection == memories)
