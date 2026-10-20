"""QdrantStore on a real node: scopes, create-if-absent, guarded writes, reads and the read-back of claim tokens.

The store is the one holder of the Qdrant connections (see test_tenant_chokepoint.py for the proof that
every repository method goes through it). Points are written with explicit payloads here so that each
guard can be tried on its own; the collection is the shared test one and every test acts as tenants of its own.
"""
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.config.settings import settings
from app.repositories.qdrant import store as store_module
from app.repositories.qdrant.store import (
    SYSTEM,
    TENANT_KEY,
    TOKEN_KEY,
    VERSION_KEY,
    NewPoint,
    Scope,
    Where,
    any_of,
    at_least,
    below,
    equals,
    scoped,
    since,
)

WIDTH = 384


@pytest.fixture
async def store(qdrant_stack):
    return qdrant_stack.store


@pytest.fixture
async def collection(qdrant_stack):
    return qdrant_stack.schema.names.live


def vector(position: int) -> list[float]:
    """A unit vector along one axis, so searches have an exact nearest neighbour."""
    return [1.0 if index == position else 0.0 for index in range(WIDTH)]


def point(tenant: UUID, position: int = 0, *, version: int = 1, claim: str = "t0", **payload) -> NewPoint:
    return NewPoint(
        uuid4(), vector(position),
        {TENANT_KEY: str(tenant), VERSION_KEY: version, TOKEN_KEY: claim, "is_obsolete": False, **payload},
    )


async def payload_of(store, scope, collection, point_id):
    [found] = await store.get(scope, collection, [point_id])
    return found.payload


# ---- filters -------------------------------------------------------------------------------------------


def test_a_tenant_scope_puts_its_tenant_condition_first():
    tenant = uuid4()

    flt = scoped(Scope.of(tenant), Where().also(equals("project", "p")).excluding(equals("is_obsolete", True)))

    assert [condition.key for condition in flt.must] == [TENANT_KEY, "project"]
    assert flt.must[0].match.value == str(tenant)
    assert [condition.key for condition in flt.must_not] == ["is_obsolete"]
    assert flt.should is None


def test_the_system_scope_adds_no_tenant_condition_and_an_empty_filter_has_no_clauses():
    flt = scoped(SYSTEM)

    assert (flt.must, flt.must_not, flt.should) == (None, None, None)


def test_where_collects_conditions_without_changing_the_original():
    base = Where()

    widened = base.also(equals("a", 1)).either(any_of("b", ["x"])).excluding(at_least("c", 2))

    assert base == Where()
    assert (len(widened.must), len(widened.should), len(widened.must_not)) == (1, 1, 1)
    assert below("c", 3).range.lt == 3
    moment = datetime.now(UTC)
    assert since("created_at", moment).range.gte == moment


# ---- create-if-absent ----------------------------------------------------------------------------------


async def test_insert_creates_a_point_and_leaves_an_existing_one_exactly_as_it_is(store, collection):
    tenant = uuid4()
    original = point(tenant, 0, title="first")
    await store.insert(Scope.of(tenant), collection, [original])

    await store.insert(Scope.of(tenant), collection, [NewPoint(original.id, vector(1), {**original.payload, "title": "second"})])

    kept = (await store.get(Scope.of(tenant), collection, [original.id], with_vectors=True))[0]
    assert kept.payload["title"] == "first"
    assert kept.vector == vector(0)


async def test_insert_stamps_the_tenant_of_the_scope_over_whatever_the_payload_says(store, collection):
    tenant, other = uuid4(), uuid4()
    mislabelled = point(other, 0)

    await store.insert(Scope.of(tenant), collection, [mislabelled])

    assert (await payload_of(store, Scope.of(tenant), collection, mislabelled.id))[TENANT_KEY] == str(tenant)
    assert await store.get(Scope.of(other), collection, [mislabelled.id]) == []


async def test_a_system_write_must_carry_its_own_tenant(store, collection):
    with pytest.raises(ValueError, match="must carry its tenant_id"):
        await store.insert(SYSTEM, collection, [NewPoint(uuid4(), vector(0), {VERSION_KEY: 1})])


async def test_inserting_nothing_does_nothing(store, collection):
    await store.insert(Scope.of(uuid4()), collection, [])
    await store.delete(Scope.of(uuid4()), collection, [])
    assert await store.get(Scope.of(uuid4()), collection, []) == []


# ---- guarded writes ------------------------------------------------------------------------------------


async def test_replace_applies_only_while_the_version_is_the_expected_one(store, collection):
    tenant = uuid4()
    scope = Scope.of(tenant)
    original = point(tenant, 0, title="v1")
    await store.insert(scope, collection, [original])

    await store.replace(
        scope, collection, NewPoint(original.id, vector(1), {**original.payload, "title": "stale", VERSION_KEY: 2}),
        expected_version=7,
    )
    assert (await payload_of(store, scope, collection, original.id))["title"] == "v1"

    await store.replace(
        scope, collection, NewPoint(original.id, vector(1), {**original.payload, "title": "v2", VERSION_KEY: 2}),
        expected_version=1,
    )
    assert (await payload_of(store, scope, collection, original.id))["title"] == "v2"


async def test_replace_with_an_older_than_guard_never_replaces_a_newer_version(store, collection):
    tenant = uuid4()
    scope = Scope.of(tenant)
    current = point(tenant, 0, version=5, title="current")
    await store.insert(scope, collection, [current])

    await store.replace(scope, collection, NewPoint(current.id, vector(1), {**current.payload, "title": "old"}), older_than=5)
    assert (await payload_of(store, scope, collection, current.id))["title"] == "current"

    await store.replace(scope, collection, NewPoint(current.id, vector(1), {**current.payload, "title": "new"}), older_than=6)
    assert (await payload_of(store, scope, collection, current.id))["title"] == "new"


async def test_replace_does_not_create_a_point_that_is_missing(store, collection):
    tenant = uuid4()
    ghost = point(tenant)

    await store.replace(Scope.of(tenant), collection, ghost, expected_version=1)

    assert await store.get(Scope.of(tenant), collection, [ghost.id]) == []


@pytest.mark.parametrize("arguments", [{}, {"expected_version": 1, "older_than": 2}])
async def test_replace_needs_exactly_one_guard(store, collection, arguments):
    with pytest.raises(ValueError, match="exactly one of expected_version and older_than"):
        await store.replace(Scope.of(uuid4()), collection, point(uuid4()), **arguments)


async def test_replace_cannot_touch_a_point_of_another_tenant(store, collection):
    owner, intruder = uuid4(), uuid4()
    theirs = point(owner, 0, title="theirs")
    await store.insert(Scope.of(owner), collection, [theirs])

    await store.replace(
        Scope.of(intruder), collection, NewPoint(theirs.id, vector(1), {**theirs.payload, "title": "taken"}),
        expected_version=1,
    )

    assert (await payload_of(store, Scope.of(owner), collection, theirs.id))["title"] == "theirs"


async def test_patch_sets_fields_only_while_the_guard_holds_and_only_for_the_named_point(store, collection):
    tenant = uuid4()
    scope = Scope.of(tenant)
    target, bystander = point(tenant, 0, title="t"), point(tenant, 1, title="b")
    await store.insert(scope, collection, [target, bystander])

    await store.patch(scope, collection, target.id, {"title": "no"}, Where().also(equals(VERSION_KEY, 9)))
    assert (await payload_of(store, scope, collection, target.id))["title"] == "t"

    await store.patch(scope, collection, target.id, {"title": "yes"}, Where().also(equals(VERSION_KEY, 1)))
    assert (await payload_of(store, scope, collection, target.id))["title"] == "yes"
    assert (await payload_of(store, scope, collection, bystander.id))["title"] == "b"


async def test_delete_removes_only_the_points_of_the_scope(store, collection):
    owner, other = uuid4(), uuid4()
    mine, theirs = point(owner, 0), point(other, 1)
    await store.insert(Scope.of(owner), collection, [mine])
    await store.insert(Scope.of(other), collection, [theirs])

    await store.delete(Scope.of(owner), collection, [mine.id, theirs.id, uuid4()])

    assert await store.get(Scope.of(owner), collection, [mine.id]) == []
    assert len(await store.get(Scope.of(other), collection, [theirs.id])) == 1


# ---- claim tokens --------------------------------------------------------------------------------------


async def test_tokens_report_the_claim_of_each_point_and_warn_about_one_that_cannot_be_read(store, collection, caplog):
    tenant = uuid4()
    scope = Scope.of(tenant)
    held = point(tenant, 0, claim="claim-1")
    missing = uuid4()
    await store.insert(scope, collection, [held])
    caplog.set_level(logging.WARNING, logger=store_module.logger.name)

    tokens = await store.tokens(scope, collection, [held.id, missing])

    assert tokens == {held.id: "claim-1"}
    [warning] = [record for record in caplog.records if "could not be read back" in record.getMessage()]
    assert warning.point_id == str(missing)


# ---- reads ---------------------------------------------------------------------------------------------


async def test_iterate_returns_every_matching_point_across_batches_in_id_order(store, collection):
    tenant = uuid4()
    scope = Scope.of(tenant)
    points = [point(tenant, index % WIDTH, project=["p"]) for index in range(7)]
    await store.insert(scope, collection, points)

    seen = [found.id async for found in store.iterate(scope, collection, batch=3)]

    assert seen == sorted(point_.id for point_ in points)


async def test_scroll_orders_by_a_payload_field_and_continues_from_an_id(store, collection):
    tenant = uuid4()
    scope = Scope.of(tenant)
    points = [point(tenant, index, importance=index + 1) for index in range(4)]
    await store.insert(scope, collection, points)

    descending = await store.scroll(scope, collection, limit=4, order_by=("importance", "desc"))
    ascending = await store.scroll(scope, collection, limit=4, order_by=("importance", "asc"))
    ordered_by_id = sorted(point_.id for point_ in points)
    continued = await store.scroll(scope, collection, limit=10, start_from=ordered_by_id[2])

    assert [found.payload["importance"] for found in descending] == [4, 3, 2, 1]
    assert [found.payload["importance"] for found in ascending] == [1, 2, 3, 4]
    assert [found.id for found in continued] == ordered_by_id[2:]


async def test_get_names_each_point_once_and_count_applies_the_filter(store, collection):
    tenant = uuid4()
    scope = Scope.of(tenant)
    kept, obsolete = point(tenant, 0), point(tenant, 1, is_obsolete=True)
    await store.insert(scope, collection, [kept, obsolete])

    found = await store.get(scope, collection, [kept.id, kept.id, obsolete.id], quorum=True)

    assert sorted(item.id for item in found) == sorted([kept.id, obsolete.id])
    assert await store.count(scope, collection) == 2
    assert await store.count(scope, collection, Where().excluding(equals("is_obsolete", True))) == 1


async def test_search_finds_the_nearest_point_of_the_tenant_only(store, collection):
    tenant, other = uuid4(), uuid4()
    mine_far, mine_near = point(tenant, 10), point(tenant, 20)
    theirs = point(other, 20)
    await store.insert(Scope.of(tenant), collection, [mine_far, mine_near])
    await store.insert(Scope.of(other), collection, [theirs])

    hits = await store.search(Scope.of(tenant), collection, query=vector(20), limit=5, with_vectors=True)
    close_only = await store.search(Scope.of(tenant), collection, query=vector(20), limit=5, score_threshold=0.5)

    assert [hit.id for hit in hits][0] == mine_near.id
    assert theirs.id not in {hit.id for hit in hits}
    assert hits[0].vector == vector(20)
    assert hits[0].score == pytest.approx(1.0)
    assert [hit.id for hit in close_only] == [mine_near.id]


async def test_a_write_is_retried_the_number_of_times_the_settings_allow(store, collection, monkeypatch):
    attempts = []

    async def failing(client):
        attempts.append(client)
        raise TimeoutError

    monkeypatch.setattr(settings, "QDRANT_WRITE_ATTEMPTS", 3)
    monkeypatch.setattr("app.repositories.qdrant.client.RETRY_BACKOFF_SECONDS", 0)

    with pytest.raises(TimeoutError):
        await store._write(failing)

    assert len(attempts) == 3
