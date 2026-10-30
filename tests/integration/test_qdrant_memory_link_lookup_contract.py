# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The links a memory read looks up: which links are asked of the store, with how much of each, and when none are.

Every read that returns memories adds the ids each one is linked to. The links come from one scan of the link collection
that the repository filters again in Python, so a filter or a payload field that is dropped would return the same memories
and only read more. The tests watch the scan the repository asks of its store (a spy that passes each call on).
"""
from uuid import uuid4

import pytest

from app.models.memory_models import MemoryCreate
from app.repositories.qdrant.store import Scope, Where, any_of


def a_memory(title: str) -> MemoryCreate:
    return MemoryCreate(
        title=title, content=f"{title} content", context="Link lookups", keywords=["links"], tags=["lookup"], importance=5,
    )


@pytest.fixture
async def scans(memory_repository, monkeypatch):
    """The repository, and the scans of the link collection it asks of its store from now on, as ``(arguments, options)``."""
    asked: list[tuple[tuple, dict]] = []
    scan = memory_repository.store.iterate

    def watched(*args, **kwargs):
        asked.append((args, kwargs))
        return scan(*args, **kwargs)

    monkeypatch.setattr(memory_repository.store, "iterate", watched)
    return memory_repository, asked


async def test_the_links_of_a_memory_are_scanned_for_the_ones_that_name_it_on_either_side_and_only_for_their_two_ends(scans):
    repo, asked = scans
    tenant = uuid4()
    first, second, third = [await repo.create_memory(tenant, a_memory(title)) for title in ("First", "Second", "Third")]
    await repo.create_links_batch(tenant, second.id, [first.id, third.id])
    asked.clear()

    found = await repo.get_memory_by_id(tenant, second.id)

    assert found.linked_memory_ids == sorted([first.id, third.id])
    wanted = [str(second.id)]
    assert asked == [
        ((Scope.of(tenant), repo.links, Where().either(any_of("a", wanted), any_of("b", wanted))), {"with_payload": ["a", "b"]}),
    ]


async def test_the_links_of_a_page_are_scanned_once_for_all_its_memories_in_the_order_of_the_page(scans):
    repo, asked = scans
    tenant = uuid4()
    memories = [await repo.create_memory(tenant, a_memory(f"Memory {number}")) for number in range(3)]
    await repo.create_links_batch(tenant, memories[0].id, [memories[2].id])
    asked.clear()

    page, _total = await repo.list_memories(tenant, 10)

    assert [memory.id for memory in page] == [memory.id for memory in reversed(memories)]
    assert {memory.id: memory.linked_memory_ids for memory in page} == {
        memories[0].id: [memories[2].id], memories[1].id: [], memories[2].id: [memories[0].id],
    }
    wanted = [str(memory.id) for memory in page]
    assert asked == [
        ((Scope.of(tenant), repo.links, Where().either(any_of("a", wanted), any_of("b", wanted))), {"with_payload": ["a", "b"]}),
    ]


async def test_no_links_are_scanned_for_a_page_without_memories(scans):
    repo, asked = scans

    assert await repo.list_memories(uuid4(), 10) == ([], 0)

    assert asked == []
