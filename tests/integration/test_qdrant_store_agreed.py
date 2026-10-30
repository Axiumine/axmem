# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""How QdrantStore reads from a majority of the replicas and reads back the outcome of a write, exactly.

Replicas that disagree are simulated by a recorder between the pool and its real clients, which leaves points out of
the majority reads; the waits between the reads are taken at once. Everything else runs against the real node.
"""
import logging
from uuid import uuid4

import pytest
from qdrant_client import models as qm

from app.exceptions import ConflictError, OutcomeUnknownError
from app.repositories.qdrant import store as store_module
from app.repositories.qdrant.store import (
    AGREEMENT_ATTEMPTS,
    CLAIM_HISTORY,
    HISTORY_KEY,
    TOKEN_KEY,
    VERSION_KEY,
    Scope,
    Where,
    equals,
)
from tests.qdrant_store_support import (
    TENANT,
    ids_asked,
    new_point,
    open_stack,
    reads_with,
    record_calls,
    undecided,
)

QUORUM = qm.ReadConsistencyType.QUORUM


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


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    """The waits a majority read makes before it asks again, taken at once."""
    seconds: list[float] = []

    async def record(delay: float) -> None:
        seconds.append(delay)

    monkeypatch.setattr(store_module.asyncio, "sleep", record)
    monkeypatch.setattr(store_module, "AGREEMENT_WAIT_SECONDS", 1.0)
    return seconds


async def agree_late(stack, collection):
    """Read three points, one of them twice, while the replicas leave the second out of the next two majority reads."""
    scope = Scope.of(TENANT)
    points = [new_point(TENANT, index) for index in range(3)]
    await stack.store.insert(scope, collection, points)
    asked = undecided(stack, {str(points[1].id)}, 2)

    found = await stack.store.get_agreed(
        scope, collection, [points[0].id, points[1].id, points[2].id, points[0].id], with_payload=[VERSION_KEY],
    )
    return points, found, asked


# ---- majority reads -------------------------------------------------------------------------------------------------


async def test_replicas_that_agree_late_are_awaited_and_the_points_come_back_once_each_in_the_order_asked(
    stack, collection, slept,
):
    (first, second, third), found, _ = await agree_late(stack, collection)

    assert [point.id for point in found] == [first.id, second.id, third.id]
    assert [point.payload for point in found] == [{VERSION_KEY: 1}] * 3


async def test_only_the_missing_points_are_asked_again_from_a_majority(stack, collection, slept):
    (first, second, third), _, asked = await agree_late(stack, collection)

    quorum = reads_with(asked, QUORUM)
    assert [ids_asked(call) for call in quorum] == [
        [str(first.id), str(second.id), str(third.id)], [str(second.id)], [str(second.id)],
    ]
    assert all(call[2]["with_payload"] == [VERSION_KEY] for call in quorum)


async def test_the_points_that_are_still_missing_are_checked_by_plain_reads_without_payload(stack, collection, slept):
    (_, second, _), _, asked = await agree_late(stack, collection)

    plain = reads_with(asked, None)
    assert [ids_asked(call) for call in plain] == [[str(second.id)], [str(second.id)]]
    assert all(call[2]["with_payload"] is False for call in plain)


async def test_the_waits_between_the_majority_reads_double(stack, collection, slept):
    await agree_late(stack, collection)

    assert slept == [1.0, 2.0]


async def test_replicas_that_never_agree_are_asked_the_set_number_of_times_and_the_read_names_the_points(
    stack, collection, slept,
):
    store = stack.store
    scope = Scope.of(TENANT)
    first, second = new_point(TENANT, 0), new_point(TENANT, 1)
    await store.insert(scope, collection, [first, second])
    asked = undecided(stack, {str(first.id), str(second.id)}, 1000)

    with pytest.raises(ConflictError) as refused:
        await store.get_agreed(scope, collection, [first.id, second.id])

    assert str(refused.value) == (
        f"The replicas of {collection} kept holding different versions of {first.id}, {second.id}; "
        "nothing was changed, try again"
    )
    assert slept == [1.0, 2.0, 4.0]
    assert len(reads_with(asked, QUORUM)) == AGREEMENT_ATTEMPTS


async def test_a_point_that_exists_nowhere_is_reported_missing_without_waiting(stack, collection, slept):
    assert await stack.store.get_agreed(Scope.of(TENANT), collection, [uuid4(), uuid4()]) == []
    assert slept == []


async def test_a_point_that_the_condition_leaves_out_is_neither_returned_nor_awaited(stack, collection, slept):
    scope = Scope.of(TENANT)
    left_out = new_point(TENANT, 0, is_obsolete=True)
    shown = new_point(TENANT, 1)
    await stack.store.insert(scope, collection, [left_out, shown])

    found = await stack.store.get_agreed(
        scope, collection, [left_out.id, shown.id], where=Where().also(equals("is_obsolete", False)),
    )

    assert [point.id for point in found] == [shown.id]
    assert slept == []


# ---- reading back the outcome of a write ---------------------------------------------------------------------------


async def test_a_read_back_that_never_gets_an_agreed_answer_says_that_the_outcome_cannot_be_told(stack, collection, slept):
    scope = Scope.of(TENANT)
    contested = new_point(TENANT, 0)
    await stack.store.insert(scope, collection, [contested])
    undecided(stack, {str(contested.id)}, 1000)

    with pytest.raises(OutcomeUnknownError) as refused:
        await stack.store.landed(scope, collection, contested.id, from_version=1, token="t")

    assert str(refused.value) == (
        f"The outcome of a write to {contested.id} cannot be told: the replicas of {collection} kept holding different "
        "versions of it when it was read back. It may or may not have been applied; read it again and repeat the change "
        "if it is missing"
    )
    assert isinstance(refused.value.__cause__, ConflictError)


async def test_a_read_back_of_a_history_that_has_been_overrun_says_how_many_writes_it_takes(stack, collection, calls):
    scope = Scope.of(TENANT)
    crowded = new_point(TENANT, 0, version=40, **{TOKEN_KEY: "newest", HISTORY_KEY: [f"h{n}" for n in range(CLAIM_HISTORY)]})
    await stack.store.insert(scope, collection, [crowded])
    calls.clear()

    with pytest.raises(OutcomeUnknownError) as refused:
        await stack.store.landed(scope, collection, crowded.id, from_version=3, token="old")

    assert str(refused.value) == (
        f"The outcome of a write to {crowded.id} cannot be told: more than 16 other writes landed before it was read "
        "back. It may or may not have been applied; read it again and repeat the change if it is missing"
    )
    assert calls[0][2]["with_payload"] == [VERSION_KEY, TOKEN_KEY, HISTORY_KEY]
    assert calls[0][2]["consistency"] == QUORUM


async def test_a_read_back_of_a_point_that_does_not_exist_warns_with_the_point_and_the_collection(stack, collection, caplog):
    missing = uuid4()
    caplog.set_level(logging.DEBUG, logger=store_module.logger.name)

    assert await stack.store.landed(Scope.of(TENANT), collection, missing, from_version=1, token="t") is False

    [record] = [record for record in caplog.records if record.name == store_module.logger.name]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == (
        "A point could not be read back after a write because it does not exist; the write was not applied"
    )
    assert (record.point_id, record.collection) == (str(missing), collection)


async def test_a_read_back_finds_the_token_of_a_write_that_applied_and_not_that_of_one_that_did_not(stack, collection):
    scope = Scope.of(TENANT)
    written = new_point(TENANT, 0, version=2, **{TOKEN_KEY: "t2", HISTORY_KEY: ["t1"]})
    await stack.store.insert(scope, collection, [written])

    assert await stack.store.landed(scope, collection, written.id, from_version=1, token="t2") is True
    assert await stack.store.landed(scope, collection, written.id, from_version=1, token="lost") is False
    assert await stack.store.landed(scope, collection, written.id, from_version=2, token="t2") is False
