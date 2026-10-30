# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Reading memories from Qdrant: one memory by id, and the filtered, sorted pages of a listing, exactly.

The repository reads through the store, so the tests note every call it makes there (a spy that passes each call on) and
compare the page that comes back with the memories that were stored. The node and the embedder are the real ones.
"""
import inspect
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.exceptions import NotFoundError
from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.repositories.qdrant import memory_reads as reads_module
from app.repositories.qdrant.store import Scope, Where, equals


class StoreSpy:
    """The store of a repository, noting each call the repository makes on it and passing the call on."""

    def __init__(self, store: Any, calls: list[tuple[str, tuple, dict]]):
        self._store = store
        self.calls = calls

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._store, name)
        if inspect.isasyncgenfunction(target):
            def streaming(*args: Any, **kwargs: Any) -> Any:
                self.calls.append((name, args, kwargs))
                return target(*args, **kwargs)

            return streaming
        if not callable(target):
            return target

        async def recorded(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return await target(*args, **kwargs)

        return recorded


def a_memory(title: str, **fields: Any) -> MemoryCreate:
    values = {
        "content": f"{title} content", "context": "Reading memories", "keywords": ["reading"], "tags": ["reads"],
        "importance": 5, **fields,
    }
    return MemoryCreate(title=title, **values)


@pytest.fixture
async def world(isolated_stack, tensorrt_embedder):
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    calls: list[tuple[str, tuple, dict]] = []
    repo.store = StoreSpy(repo.store, calls)
    return repo, uuid4(), calls


async def seed(repo, tenant: UUID, *memories: MemoryCreate):
    return [await repo.create_memory(tenant, memory) for memory in memories]


def titles(page) -> list[str]:
    return [memory.title for memory in page]


# ---- one memory by id -----------------------------------------------------------------------------------------------


async def test_a_memory_is_read_by_id_from_a_majority_of_the_replicas_with_the_memories_it_is_linked_to(world):
    repo, tenant, calls = world
    first, second, third = await seed(repo, tenant, a_memory("First"), a_memory("Second"), a_memory("Third"))
    await repo.create_links_batch(tenant, second.id, [third.id, first.id])
    calls.clear()

    found = await repo.get_memory_by_id(tenant, second.id)

    assert (found.id, found.title) == (second.id, "Second")
    assert found.linked_memory_ids == sorted([first.id, third.id])
    assert calls[0] == ("get_agreed", (Scope.of(tenant), repo.memories, [second.id]), {})


async def test_a_memory_that_the_tenant_does_not_have_is_named_in_the_refusal(world):
    repo, tenant, _ = world
    [mine] = await seed(repo, tenant, a_memory("Mine"))
    stranger, missing = uuid4(), uuid4()

    with pytest.raises(NotFoundError) as absent:
        await repo.get_memory_by_id(tenant, missing)
    with pytest.raises(NotFoundError) as not_theirs:
        await repo.get_memory_by_id(stranger, mine.id)

    assert str(absent.value) == f"Memory with id {missing} not found"
    assert str(not_theirs.value) == f"Memory with id {mine.id} not found"


# ---- the options of a listing ---------------------------------------------------------------------------------------


async def test_a_listing_refuses_a_sort_it_does_not_know_and_names_the_ones_it_does(world):
    repo, tenant, calls = world

    with pytest.raises(ValueError) as by:
        await repo.list_memories(tenant, 5, sort_by="title")
    with pytest.raises(ValueError) as order:
        await repo.list_memories(tenant, 5, sort_order="sideways")

    assert str(by.value) == "sort_by must be one of: created_at, updated_at, importance"
    assert str(order.value) == "sort_order must be one of: asc, desc"
    assert calls == []


async def test_a_listing_without_options_is_the_newest_first_from_the_start_without_the_obsolete(world):
    repo, tenant, _ = world
    oldest, hidden, newest = await seed(repo, tenant, a_memory("Oldest"), a_memory("Hidden"), a_memory("Newest"))
    await repo.mark_obsolete(tenant, hidden.id, "no longer true")

    page, total = await repo.list_memories(tenant, 10)

    assert (titles(page), total) == (["Newest", "Oldest"], 2)
    assert [memory.id for memory in page] == [newest.id, oldest.id]


async def test_a_listing_can_include_the_obsolete(world):
    repo, tenant, _ = world
    await seed(repo, tenant, a_memory("Kept"), a_memory("Replaced"))
    [replaced] = [memory for memory in (await repo.list_memories(tenant, 10))[0] if memory.title == "Replaced"]
    await repo.mark_obsolete(tenant, replaced.id, "no longer true")

    page, total = await repo.list_memories(tenant, 10, include_obsolete=True)

    assert (titles(page), total) == (["Replaced", "Kept"], 2)
    assert page[0].is_obsolete is True


@pytest.fixture
async def filtered(world):
    """Three memories of the project alpha at importance 4, 5 and 6, and one of beta at 9, created in this order."""
    repo, tenant, _ = world
    memories = await seed(
        repo, tenant,
        a_memory("Low", importance=4, project="alpha", tags=["red"]),
        a_memory("Middle", importance=5, project="alpha", tags=["blue"]),
        a_memory("High", importance=6, project="alpha", tags=["red", "blue"]),
        a_memory("Other", importance=9, project="beta", tags=["green"]),
    )
    return repo, tenant, memories


async def titles_listed(repo, tenant: UUID, **options: Any) -> list[str]:
    return titles((await repo.list_memories(tenant, 10, **options))[0])


async def test_a_listing_keeps_to_a_project(filtered):
    repo, tenant, _ = filtered

    assert await titles_listed(repo, tenant, project="alpha") == ["High", "Middle", "Low"]
    assert await titles_listed(repo, tenant, project="beta") == ["Other"]


async def test_a_listing_keeps_to_the_memories_with_any_of_the_tags(filtered):
    repo, tenant, _ = filtered

    assert await titles_listed(repo, tenant, tags=["red"]) == ["High", "Low"]
    assert await titles_listed(repo, tenant, tags=["green", "blue"]) == ["Other", "High", "Middle"]


async def test_a_listing_keeps_to_the_memories_at_or_above_an_importance_floor(filtered):
    repo, tenant, _ = filtered

    assert await titles_listed(repo, tenant, importance_min=5) == ["Other", "High", "Middle"]
    assert await titles_listed(repo, tenant, importance_min=7) == ["Other"]


async def test_a_listing_keeps_to_the_memories_created_from_a_first_day_on(filtered):
    repo, tenant, (low, middle, high, other) = filtered
    far_east = timezone(timedelta(hours=9))

    since_middle = await repo.list_memories(tenant, 10, created_since=middle.created_at.astimezone(far_east))
    assert (titles(since_middle[0]), since_middle[1]) == (["Other", "High", "Middle"], 3)
    after_high = await repo.list_memories(tenant, 10, created_since=high.created_at + timedelta(microseconds=1))
    assert titles(after_high[0]) == ["Other"]
    assert low.created_at < middle.created_at < high.created_at < other.created_at


async def test_a_listing_combines_its_filters_and_ignores_an_empty_tag_list_and_an_empty_project(filtered):
    repo, tenant, _ = filtered

    assert await titles_listed(repo, tenant, project="alpha", tags=["red"], importance_min=5) == ["High"]
    assert (await repo.list_memories(tenant, 10, tags=[]))[1] == 4
    assert (await repo.list_memories(tenant, 10, project=""))[1] == 4


@pytest.fixture
def clock_in_japan(monkeypatch):
    """The local time zone of the process is nine hours ahead of UTC, so that a moment left in it shows."""
    monkeypatch.setenv("TZ", "JST-9")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


async def test_a_first_day_reaches_the_store_as_a_moment_in_utc_whatever_the_zone_it_was_given_in(world, clock_in_japan):
    repo, tenant, calls = world
    given = datetime(2026, 3, 1, 12, tzinfo=timezone(timedelta(hours=-5)))

    await repo.list_memories(tenant, 10, created_since=given)

    (counted,) = [where for name, (_, _, where), _ in calls if name == "count"]
    (condition,) = [condition for condition in counted.must if condition.key == "created_at"]
    assert condition.range.gte == given
    assert condition.range.gte.utcoffset() == timedelta(0)


async def test_a_listing_sorted_by_a_date_goes_both_ways_and_by_the_last_edit_when_asked(world):
    repo, tenant, _ = world
    first, second, third = await seed(repo, tenant, a_memory("One"), a_memory("Two"), a_memory("Three"))
    await repo.update_memory(tenant, first.id, MemoryUpdate(importance=6))

    assert titles((await repo.list_memories(tenant, 10, sort_by="created_at", sort_order="asc"))[0]) == ["One", "Two", "Three"]
    assert titles((await repo.list_memories(tenant, 10, sort_by="created_at", sort_order="desc"))[0]) == ["Three", "Two", "One"]
    assert titles((await repo.list_memories(tenant, 10, sort_by="updated_at", sort_order="desc"))[0]) == ["One", "Three", "Two"]
    assert titles((await repo.list_memories(tenant, 10, sort_by="updated_at", sort_order="asc"))[0]) == ["Two", "Three", "One"]
    assert second.created_at < third.created_at


async def test_a_page_skips_the_offset_and_holds_at_most_the_limit_while_the_total_counts_every_match(world):
    repo, tenant, calls = world
    await seed(repo, tenant, *(a_memory(f"Memory {number}") for number in range(5)))
    calls.clear()

    page, total = await repo.list_memories(tenant, 2, offset=1)

    assert (titles(page), total) == (["Memory 3", "Memory 2"], 5)
    count, scroll = [call for call in calls if call[0] != "iterate"]  # the links of the page are read after it
    assert count == ("count", (Scope.of(tenant), repo.memories, Where().also(equals("is_obsolete", False))), {})
    assert scroll == ("scroll", (Scope.of(tenant), repo.memories, Where().also(equals("is_obsolete", False))), {
        "limit": 3, "order_by": ("created_at", "desc"),
    })
    assert titles((await repo.list_memories(tenant, 2, offset=4))[0]) == ["Memory 0"]
    assert titles((await repo.list_memories(tenant, 10, offset=2))[0]) == ["Memory 2", "Memory 1", "Memory 0"]


@pytest.mark.parametrize("offset", [5, 6, 50])
async def test_a_page_that_starts_at_or_beyond_the_end_is_empty_and_reads_no_points(world, offset):
    repo, tenant, calls = world
    await seed(repo, tenant, *(a_memory(f"Memory {number}") for number in range(5)))
    calls.clear()

    page, total = await repo.list_memories(tenant, 3, offset=offset)

    assert (page, total) == ([], 5)
    assert [call[0] for call in calls] == ["count"]


# ---- a listing by importance ----------------------------------------------------------------------------------------


@pytest.fixture
async def by_importance(world):
    """Two memories at 3, three at 6 and one at 9, created in this order: each level's newest comes first."""
    repo, tenant, calls = world
    spec = [("A", 3), ("B", 6), ("C", 9), ("D", 3), ("E", 6), ("F", 6)]
    await seed(repo, tenant, *(a_memory(title, importance=importance) for title, importance in spec))
    return repo, tenant, calls


@pytest.mark.parametrize(("order", "offset", "limit", "expected"), [
    ("desc", 0, 10, ["C", "F", "E", "B", "D", "A"]),
    ("asc", 0, 10, ["D", "A", "F", "E", "B", "C"]),
    ("desc", 0, 2, ["C", "F"]),
    ("desc", 0, 4, ["C", "F", "E", "B"]),
    ("desc", 1, 2, ["F", "E"]),
    ("desc", 1, 1, ["F"]),
    ("desc", 3, 2, ["B", "D"]),
    ("desc", 4, 5, ["D", "A"]),
    ("desc", 5, 5, ["A"]),
    ("asc", 2, 3, ["F", "E", "B"]),
    ("asc", 2, 1, ["F"]),
    ("asc", 3, 2, ["E", "B"]),
    ("asc", 5, 4, ["C"]),
    ("asc", 1, 3, ["A", "F", "E"]),
])
async def test_a_page_by_importance_goes_through_the_levels_newest_first_and_skips_whole_levels(
    by_importance, order, offset, limit, expected,
):
    repo, tenant, _ = by_importance

    page, total = await repo.list_memories(tenant, limit, offset=offset, sort_by="importance", sort_order=order)

    assert (titles(page), total) == (expected, 6)


async def test_a_page_by_importance_reads_one_level_at_a_time_and_counts_a_level_only_while_it_skips(by_importance):
    repo, tenant, calls = by_importance
    calls.clear()

    page, _ = await repo.list_memories(tenant, 2, offset=4, sort_by="importance", sort_order="desc")

    def at(level: int) -> Where:
        return Where().also(equals("is_obsolete", False), equals("importance", level))

    assert titles(page) == ["D", "A"]
    assert calls[0] == ("count", (Scope.of(tenant), repo.memories, Where().also(equals("is_obsolete", False))), {})
    steps = [(call[0], call[1][2], call[2].get("limit")) for call in calls[1:] if call[0] != "iterate"]
    # four are skipped: the levels of nothing, the one of C (1) and the one of B, E, F (3), then the page is read
    assert steps == [
        *(("count", at(level), None) for level in (10, 9, 8, 7, 6)),
        *(("scroll", at(level), 2) for level in (5, 4, 3)),
    ]
    assert {call[2]["order_by"] for call in calls if call[0] == "scroll"} == {("created_at", "desc")}


async def test_a_page_by_importance_that_the_first_level_fills_reads_only_that_level(by_importance):
    repo, tenant, calls = by_importance
    calls.clear()

    page, _ = await repo.list_memories(tenant, 1, sort_by="importance", sort_order="desc")

    assert titles(page) == ["C"]
    levels = [call[1][2] for call in calls if call[0] == "scroll"]
    assert levels == [Where().also(equals("is_obsolete", False), equals("importance", 10)),
                      Where().also(equals("is_obsolete", False), equals("importance", 9))]
    assert [call[2]["limit"] for call in calls if call[0] == "scroll"] == [1, 1]
    assert [call[0] for call in calls].count("count") == 1


# ---- what a listing logs --------------------------------------------------------------------------------------------


@pytest.fixture
async def listings_logged(world, caplog):
    """The records of two listings of three memories: one with every option given, one with none."""
    repo, tenant, _ = world
    await seed(repo, tenant, *(a_memory(f"Memory {number}", project="alpha", tags=["red"]) for number in range(3)))
    caplog.set_level(logging.INFO, logger=reads_module.logger.name)

    await repo.list_memories(
        tenant, 2, offset=1, project="alpha", include_obsolete=True, sort_by="updated_at", sort_order="asc", tags=["red"],
    )
    await repo.list_memories(tenant, 5)

    asked, plain = [record for record in caplog.records if record.name == reads_module.logger.name]
    return tenant, asked, plain


async def test_a_listing_logs_what_was_asked_and_how_many_it_found(listings_logged):
    tenant, asked, _ = listings_logged

    assert asked.levelno == logging.INFO
    assert asked.getMessage() == "Retrieved recent memories"
    assert (asked.user_id, asked.count, asked.total, asked.limit, asked.offset) == (str(tenant), 2, 3, 2, 1)


async def test_a_listing_logs_the_filters_and_the_sort_it_was_given(listings_logged):
    _, asked, _ = listings_logged

    assert (asked.project_filtered, asked.include_obsolete, asked.tags_filtered) == (True, True, True)
    assert (asked.sort_by, asked.sort_order) == ("updated_at", "asc")


async def test_a_listing_without_options_logs_the_defaults(listings_logged):
    _, _, plain = listings_logged

    assert (plain.count, plain.total, plain.limit, plain.offset) == (3, 3, 5, 0)
    assert (plain.project_filtered, plain.include_obsolete, plain.tags_filtered) == (False, False, False)
    assert (plain.sort_by, plain.sort_order) == ("created_at", "desc")
