# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""How QdrantStore pages and scans, exactly: where a page ends, how big a read is, and which replicas answer.

A recorder sits between the pool and its real clients and notes each request, so that an option dropped or changed
fails here although the node would accept the request and answer in the same way. The rest runs against the real node.
"""
import pytest
from qdrant_client import models as qm

from app.repositories.qdrant import store as store_module
from app.repositories.qdrant.store import VERSION_KEY, Scope, Where, equals
from tests.qdrant_store_support import (
    TENANT,
    new_point,
    open_stack,
    ordered_points,
    reads_with,
    record_calls,
    undecided,
)


@pytest.fixture
async def stack(isolated_qdrant):
    async with open_stack() as opened:
        yield opened


@pytest.fixture
def collection(stack):
    return stack.schema.names.live


@pytest.fixture
def calls(stack):
    return record_calls(stack)


async def quorum_page(stack, collection, calls):
    """Insert three points and read the first two from a majority; the points and the page with where it goes on."""
    points = ordered_points(3)
    await stack.store.insert(Scope.of(TENANT), collection, points)
    calls.clear()

    page = await stack.store.page(
        Scope.of(TENANT), collection, limit=2, with_payload=[VERSION_KEY], with_vectors=True, quorum=True,
    )
    return points, page


async def test_a_page_ends_at_the_limit_and_names_where_to_continue_only_when_more_points_follow(stack, collection):
    store = stack.store
    scope = Scope.of(TENANT)
    points = ordered_points(4)
    await store.insert(scope, collection, points)
    ids = [point.id for point in points]

    exact, none_after = await store.page(scope, collection, limit=4)
    first, following = await store.page(scope, collection, limit=3)
    rest, end = await store.page(scope, collection, limit=3, start_from=following)

    assert ([point.id for point in exact], none_after) == (ids, None)
    assert ([point.id for point in first], following) == (ids[:3], ids[3])
    assert ([point.id for point in rest], end) == (ids[3:], None)


async def test_a_complete_page_is_listed_with_the_fields_it_was_asked_for(stack, collection, calls):
    points, (page, following) = await quorum_page(stack, collection, calls)

    assert ([point.id for point in page], following) == ([point.id for point in points[:2]], points[2].id)
    assert page[0].payload == {VERSION_KEY: 1}
    assert page[0].vector is not None


async def test_a_complete_page_is_first_counted_by_a_plain_read_of_one_point_more_than_its_limit(stack, collection, calls):
    await quorum_page(stack, collection, calls)

    census = calls[0]
    assert census[2]["limit"] == 3
    assert census[2]["with_payload"] is False
    assert census[2]["with_vectors"] is False
    assert census[2]["consistency"] is None


async def test_a_complete_page_is_then_read_from_a_majority_with_the_fields_it_was_asked_for(stack, collection, calls):
    await quorum_page(stack, collection, calls)

    quorum_read = calls[1]
    assert quorum_read[2]["consistency"] == qm.ReadConsistencyType.QUORUM
    assert quorum_read[2]["with_payload"] == [VERSION_KEY]
    assert quorum_read[2]["with_vectors"] is True


async def test_an_incomplete_page_read_is_not_taken_for_the_end_of_the_scan(stack, collection, monkeypatch):
    store = stack.store
    scope = Scope.of(TENANT)
    points = ordered_points(3)
    await store.insert(scope, collection, points)
    monkeypatch.setattr(store_module, "AGREEMENT_WAIT_SECONDS", 0.0)
    undecided(stack, {str(points[1].id)}, 1)

    page, following = await store.page(scope, collection, limit=2, quorum=True)

    assert [point.id for point in page] == [point.id for point in points[:2]]
    assert following == points[2].id


async def test_a_scan_reads_256_points_at_a_time_unless_told_otherwise(stack, collection, calls):
    scope = Scope.of(TENANT)
    await stack.store.insert(scope, collection, [new_point(TENANT, 0)])
    calls.clear()

    seen = [point async for point in stack.store.iterate(scope, collection)]

    assert len(seen) == 1
    assert calls[0][2]["limit"] == 257
    assert calls[0][2]["consistency"] is None
    assert calls[0][2]["with_payload"] is True
    assert calls[0][2]["with_vectors"] is False


async def test_a_scan_reads_from_a_majority_with_the_fields_it_was_asked_for_when_told_to(stack, collection, calls):
    scope = Scope.of(TENANT)
    await stack.store.insert(scope, collection, [new_point(TENANT, 0)])
    calls.clear()

    [point async for point in stack.store.iterate(scope, collection, batch=2, with_payload=[VERSION_KEY], with_vectors=True, quorum=True)]

    quorum_scrolls = reads_with(calls, qm.ReadConsistencyType.QUORUM)
    assert quorum_scrolls[0][2]["with_payload"] == [VERSION_KEY]
    assert quorum_scrolls[0][2]["with_vectors"] is True


async def test_a_scan_that_started_from_an_id_goes_on_after_the_last_page(stack, collection):
    store = stack.store
    scope = Scope.of(TENANT)
    points = ordered_points(5)
    await store.insert(scope, collection, points)

    seen = [point.id async for point in store.iterate(scope, collection, batch=2)]

    assert seen == [point.id for point in points]


@pytest.mark.parametrize("quorum", [False, True])
async def test_a_page_keeps_to_the_condition_the_starting_id_and_the_fields_it_was_given(stack, collection, quorum):
    scope = Scope.of(TENANT)
    mine = ordered_points(4, project=["p"])
    others = ordered_points(2, 10, project=["q"])
    await stack.store.insert(scope, collection, [*mine, *others])
    ids = [point.id for point in mine]
    where = Where().also(equals("project", "p"))
    fields = {"with_payload": [VERSION_KEY], "with_vectors": True, "quorum": quorum}

    first, following = await stack.store.page(scope, collection, where, limit=3, **fields)
    second, end = await stack.store.page(scope, collection, where, limit=3, start_from=following, **fields)
    whole, nothing_after = await stack.store.page(scope, collection, where, limit=4, **fields)

    assert ([point.id for point in first], following) == (ids[:3], ids[3])
    assert ([point.id for point in second], end) == (ids[3:], None)
    assert ([point.id for point in whole], nothing_after) == (ids, None)
    assert [point.payload for point in first] == [{VERSION_KEY: 1}] * 3
    assert all(point.vector is not None for point in first)


@pytest.mark.parametrize("quorum", [False, True])
async def test_a_scan_keeps_to_the_condition_it_was_given(stack, collection, quorum):
    scope = Scope.of(TENANT)
    mine = ordered_points(3, project=["p"])
    await stack.store.insert(scope, collection, [*mine, new_point(TENANT, 9, project=["q"])])

    seen = [
        point.id async for point in stack.store.iterate(
            scope, collection, Where().also(equals("project", "p")), batch=2, quorum=quorum,
        )
    ]

    assert seen == [point.id for point in mine]


async def test_a_page_reads_from_one_replica_without_vectors_unless_told_to_and_applies_the_condition_to_every_read(
    stack, collection, calls,
):
    scope = Scope.of(TENANT)
    mine = new_point(TENANT, 0, project=["p"])
    await stack.store.insert(scope, collection, [mine, new_point(TENANT, 1, project=["q"])])
    calls.clear()
    where = Where().also(equals("project", "p"))

    [plain], _ = await stack.store.page(scope, collection, where, limit=5)
    [agreed], _ = await stack.store.page(scope, collection, where, limit=5, quorum=True)

    assert (plain.id, plain.vector) == (mine.id, None)
    assert (agreed.id, agreed.vector) == (mine.id, None)
    plain_read, census, majority_read = calls
    assert (plain_read[2]["consistency"], plain_read[2]["with_vectors"], plain_read[2]["limit"]) == (None, False, 6)
    assert (census[2]["consistency"], census[2]["limit"]) == (None, 6)
    assert majority_read[2]["consistency"] == qm.ReadConsistencyType.QUORUM
    project = equals("project", "p")
    for read in calls:
        assert project in read[2]["scroll_filter"].must
