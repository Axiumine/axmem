# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""What QdrantStore asks of Qdrant when it reads by filter, by id and by vector, exactly: filter, page, order and fields.

A recorder sits between the pool and its real clients and notes each request, so that an option dropped or changed
fails here although the node would accept the request and answer in the same way. The rest runs against the real node.
"""
from typing import Any
from uuid import uuid4

import pytest
from qdrant_client import models as qm

from app.repositories.qdrant.store import (
    SYSTEM,
    TENANT_KEY,
    VERSION_KEY,
    Scope,
    Where,
    equals,
)
from tests.qdrant_store_support import (
    TENANT,
    new_point,
    open_stack,
    record_calls,
    vector,
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


class Unreachable:
    """A node that does not answer."""

    async def close(self) -> None:
        """Nothing to close."""

    def __getattr__(self, name: str) -> Any:
        async def refuse(*args: Any, **kwargs: Any) -> Any:
            raise TimeoutError

        return refuse


async def look_up_by_id(stack, collection, calls):
    """Look up one point of the tenant, with its id named twice next to an id that does not exist."""
    mine = new_point(TENANT, 0)
    await stack.store.insert(Scope.of(TENANT), collection, [mine])
    calls.clear()

    found = await stack.store.get(
        Scope.of(TENANT), collection, [mine.id, mine.id, uuid4()], where=Where().also(equals("is_obsolete", False)),
        with_payload=[VERSION_KEY], with_vectors=True, quorum=True,
    )
    return mine, found


async def test_a_scroll_sends_the_filter_the_page_the_order_and_the_fields_it_was_asked_for(stack, collection, calls):
    store = stack.store
    start = uuid4()

    await store.scroll(Scope.of(TENANT), collection, Where().also(equals("project", "p")), limit=3)
    await store.scroll(
        SYSTEM, collection, limit=5, start_from=start, with_payload=["version"], with_vectors=True, quorum=True,
    )
    await store.scroll(SYSTEM, collection, limit=4, order_by=("importance", "asc"))
    await store.scroll(SYSTEM, collection, limit=2, order_by=("importance", "desc"))

    tenant_condition = qm.FieldCondition(key=TENANT_KEY, match=qm.MatchValue(value=str(TENANT)))
    assert calls[0] == ("scroll", (collection,), {
        "scroll_filter": qm.Filter(must=[tenant_condition, equals("project", "p")]), "limit": 3, "order_by": None,
        "offset": None, "with_payload": True, "with_vectors": False, "consistency": None,
    })
    assert calls[1] == ("scroll", (collection,), {
        "scroll_filter": qm.Filter(), "limit": 5, "order_by": None, "offset": str(start), "with_payload": ["version"],
        "with_vectors": True, "consistency": qm.ReadConsistencyType.QUORUM,
    })
    assert calls[2][2]["order_by"] == qm.OrderBy(key="importance", direction=qm.Direction.ASC)
    assert calls[2][2]["limit"] == 4
    assert calls[3][2]["order_by"] == qm.OrderBy(key="importance", direction=qm.Direction.DESC)


async def test_a_lookup_by_id_returns_each_point_once_with_the_payload_and_vector_it_was_asked_for(
    stack, collection, calls,
):
    mine, found = await look_up_by_id(stack, collection, calls)

    assert [point.id for point in found] == [mine.id]
    assert found[0].payload == {VERSION_KEY: 1}
    assert found[0].vector == vector(0)


async def test_a_lookup_by_id_names_each_id_once_and_asks_for_no_more_points_than_that(stack, collection, calls):
    await look_up_by_id(stack, collection, calls)

    [call] = calls
    assert call[2]["limit"] == 2
    assert call[2]["with_payload"] == [VERSION_KEY]
    assert call[2]["with_vectors"] is True
    assert call[2]["consistency"] == qm.ReadConsistencyType.QUORUM
    assert [type(condition).__name__ for condition in call[2]["scroll_filter"].must] == [
        "FieldCondition", "FieldCondition", "HasIdCondition",
    ]


async def test_a_lookup_of_no_ids_sends_no_request(stack, collection, calls):
    await look_up_by_id(stack, collection, calls)

    assert await stack.store.get(Scope.of(TENANT), collection, []) == []
    assert len(calls) == 1


async def test_a_count_is_exact_and_a_search_asks_for_the_payload_and_the_threshold_it_was_given(stack, collection, calls):
    store = stack.store
    near, far = new_point(TENANT, 3), new_point(TENANT, 4)
    await store.insert(Scope.of(TENANT), collection, [near, far])
    calls.clear()

    assert await store.count(Scope.of(TENANT), collection, Where().also(equals("is_obsolete", False))) == 2
    hits = await store.search(Scope.of(TENANT), collection, query=vector(3), limit=7, score_threshold=0.5, with_vectors=True)
    unlimited = await store.search(Scope.of(TENANT), collection, query=vector(4), limit=1)

    assert [hit.id for hit in hits] == [near.id]
    assert hits[0].vector == vector(3)
    assert [hit.id for hit in unlimited] == [far.id]
    assert unlimited[0].vector is None
    tenant_filter = qm.Filter(must=[equals(TENANT_KEY, str(TENANT))])
    assert calls[0] == ("count", (collection,), {
        "count_filter": qm.Filter(must=[equals(TENANT_KEY, str(TENANT)), equals("is_obsolete", False)]), "exact": True,
    })
    assert calls[1] == ("query_points", (collection,), {
        "query": vector(3), "query_filter": tenant_filter, "limit": 7, "score_threshold": 0.5, "with_payload": True,
        "with_vectors": True,
    })
    assert calls[2][2]["score_threshold"] is None
    assert calls[2][2]["with_vectors"] is False


async def test_a_search_keeps_to_the_condition_it_was_given(stack, collection):
    scope = Scope.of(TENANT)
    active, obsolete = new_point(TENANT, 5), new_point(TENANT, 5, is_obsolete=True)
    await stack.store.insert(scope, collection, [active, obsolete])

    hits = await stack.store.search(
        scope, collection, query=vector(5), where=Where().also(equals("is_obsolete", False)), limit=5,
    )

    assert [hit.id for hit in hits] == [active.id]


async def test_a_lookup_returns_the_payload_without_the_vector_unless_it_is_asked_for(stack, collection, calls):
    scope = Scope.of(TENANT)
    mine = new_point(TENANT, 0, title="mine")
    await stack.store.insert(scope, collection, [mine])
    calls.clear()

    [plain] = await stack.store.get(scope, collection, [mine.id])
    [agreed] = await stack.store.get_agreed(scope, collection, [mine.id])
    [with_vector] = await stack.store.get_agreed(scope, collection, [mine.id], with_vectors=True)

    assert (plain.payload["title"], plain.vector) == ("mine", None)
    assert (agreed.payload["title"], agreed.vector) == ("mine", None)
    assert with_vector.vector == vector(0)
    assert [(call[2]["with_payload"], call[2]["with_vectors"], call[2]["consistency"]) for call in calls] == [
        (True, False, None),
        (True, False, qm.ReadConsistencyType.QUORUM),
        (True, True, qm.ReadConsistencyType.QUORUM),
    ]


async def test_a_read_goes_on_to_the_next_node_when_one_does_not_answer(stack, collection, monkeypatch):
    scope = Scope.of(TENANT)
    mine = new_point(TENANT, 0)
    await stack.store.insert(scope, collection, [mine])
    monkeypatch.setattr("app.repositories.qdrant.client.RETRY_BACKOFF_SECONDS", 0)
    stack.pool.clients = [Unreachable(), *stack.pool.clients]

    # requests start at a different node each time: one of the two reads starts at the node that does not answer
    assert await stack.store.count(scope, collection) == 1
    assert [point.id for point in await stack.store.get(scope, collection, [mine.id])] == [mine.id]
