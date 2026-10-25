"""What QdrantStore sends to Qdrant when it writes and what it says when it refuses, exactly.

The write options are what the verified cluster profile needs (a write that waits, strong ordering, an explicit
update mode). A recorder sits between the pool and its real clients and notes each request, so that an option
dropped or changed fails here although the node would accept the request and answer in the same way.
"""
from typing import Any

import pytest
from qdrant_client import models as qm

from app.repositories.qdrant import store as store_module
from app.repositories.qdrant.store import (
    TENANT_KEY,
    VERSION_KEY,
    NewPoint,
    Scope,
    Where,
    below,
    equals,
)
from tests.qdrant_store_support import (
    TENANT,
    Call,
    Recorder,
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


class FailsTwice(Recorder):
    """A node whose first two writes time out before they reach it."""

    async def upsert(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("upsert", args, kwargs))
        if len(self.calls) <= 2:
            raise TimeoutError
        return await self._client.upsert(*args, **kwargs)


async def test_the_writes_wait_for_the_node_in_strong_order_and_say_how_they_apply(stack, collection, calls):
    store = stack.store
    scope = Scope.of(TENANT)
    created = new_point(TENANT, 0, title="first")
    other = new_point(TENANT, 1)
    await store.insert(scope, collection, [created, other])
    await store.replace(scope, collection, NewPoint(created.id, vector(2), {**created.payload, "title": "second"}), expected_version=1)
    await store.replace(scope, collection, NewPoint(created.id, vector(3), {**created.payload, VERSION_KEY: 2}), older_than=9)
    await store.patch(scope, collection, created.id, {"title": "third"}, Where().also(equals(VERSION_KEY, 1)))
    await store.delete(scope, collection, [other.id])

    insert, by_version, by_age, patch, delete = [call for call in calls if call[0] in {"upsert", "set_payload", "delete"}]
    structs = [
        qm.PointStruct(id=str(created.id), vector=vector(0), payload=created.payload),
        qm.PointStruct(id=str(other.id), vector=vector(1), payload=other.payload),
    ]
    assert insert == ("upsert", (collection, structs), {
        "wait": True, "ordering": qm.WriteOrdering.STRONG, "update_mode": qm.UpdateMode.INSERT_ONLY,
    })
    assert by_version[0:2] == ("upsert", (collection, [qm.PointStruct(
        id=str(created.id), vector=vector(2), payload={**created.payload, "title": "second"},
    )]))
    assert by_version[2] == {
        "wait": True, "ordering": qm.WriteOrdering.STRONG, "update_mode": qm.UpdateMode.UPDATE_ONLY,
        "update_filter": qm.Filter(must=[equals(TENANT_KEY, str(TENANT)), equals(VERSION_KEY, 1)]),
    }
    assert by_age[2]["update_filter"] == qm.Filter(must=[equals(TENANT_KEY, str(TENANT)), below(VERSION_KEY, 9)])
    assert by_age[2]["update_mode"] == qm.UpdateMode.UPDATE_ONLY
    assert patch == ("set_payload", (collection,), {
        "payload": {"title": "third"},
        "points": qm.Filter(must=[equals(TENANT_KEY, str(TENANT)), equals(VERSION_KEY, 1), qm.HasIdCondition(has_id=[str(created.id)])]),
        "wait": True, "ordering": qm.WriteOrdering.STRONG,
    })
    assert delete == ("delete", (collection,), {
        "points_selector": qm.FilterSelector(filter=qm.Filter(
            must=[equals(TENANT_KEY, str(TENANT)), qm.HasIdCondition(has_id=[str(other.id)])],
        )),
        "wait": True, "ordering": qm.WriteOrdering.STRONG,
    })


async def test_nothing_is_sent_for_a_write_or_a_delete_of_no_points(stack, collection, calls):
    await stack.store.insert(Scope.of(TENANT), collection, [])
    await stack.store.delete(Scope.of(TENANT), collection, [])
    await stack.store.delete(Scope.of(TENANT), collection, ())

    assert calls == []


async def test_a_replace_that_is_guarded_below_zero_replaces_nothing(stack, collection):
    store = stack.store
    scope = Scope.of(TENANT)
    unversioned = new_point(TENANT, 0, version=0, title="kept")
    await store.insert(scope, collection, [unversioned])

    await store.replace(scope, collection, NewPoint(unversioned.id, vector(1), {**unversioned.payload, "title": "taken"}), older_than=0)

    [found] = await store.get(scope, collection, [unversioned.id])
    assert found.payload["title"] == "kept"


@pytest.mark.parametrize("guards", [{}, {"expected_version": 1, "older_than": 2}])
async def test_a_replace_without_exactly_one_guard_is_refused_in_one_sentence(stack, collection, guards):
    with pytest.raises(ValueError) as refused:
        await stack.store.replace(Scope.of(TENANT), collection, new_point(TENANT), **guards)

    assert str(refused.value) == "replace needs exactly one of expected_version and older_than"


async def test_a_replace_guarded_on_version_zero_is_a_guard_like_any_other(stack, collection):
    scope = Scope.of(TENANT)
    unversioned = new_point(TENANT, 0, version=0, title="kept")
    await stack.store.insert(scope, collection, [unversioned])

    await stack.store.replace(scope, collection, NewPoint(unversioned.id, vector(1), {**unversioned.payload, "title": "taken"}), expected_version=0)

    [found] = await stack.store.get(scope, collection, [unversioned.id])
    assert found.payload["title"] == "taken"


async def test_a_write_is_repeated_as_often_as_the_settings_allow_and_not_more(stack, collection, monkeypatch):
    scope = Scope.of(TENANT)
    mine = new_point(TENANT, 0, title="mine")
    attempts: list[Call] = []
    monkeypatch.setattr("app.repositories.qdrant.client.RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(store_module.settings, "QDRANT_WRITE_ATTEMPTS", 3)
    stack.pool.clients = [FailsTwice(client, attempts) for client in stack.pool.clients]

    await stack.store.insert(scope, collection, [mine])

    assert len(attempts) == 3
    assert (await stack.store.get(scope, collection, [mine.id]))[0].payload["title"] == "mine"
    attempts.clear()
    monkeypatch.setattr(store_module.settings, "QDRANT_WRITE_ATTEMPTS", 2)
    other = new_point(TENANT, 1)
    with pytest.raises(TimeoutError):
        await stack.store.insert(scope, collection, [other])
    assert len(attempts) == 2
