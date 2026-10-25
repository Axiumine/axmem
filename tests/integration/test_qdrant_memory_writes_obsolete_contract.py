"""Marking a memory obsolete and restoring it in Qdrant: what each toggle writes, how often it tries, and when it stamps.

A toggle is one conditional patch, read back to learn whether it won. The repository writes through the store, so the
tests note the calls it makes there (a spy that can stand in for one write that did not take effect, as a writer that lost
a race sees it) and read the stored point back from the node (see ``tests/memory_writes_support.py``).
"""
from dataclasses import dataclass
from datetime import UTC
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.exceptions import (
    AssociationNotFoundError,
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
)
from app.repositories.qdrant.memory_base import MAX_CAS_ATTEMPTS
from app.repositories.qdrant.store import (
    HISTORY_KEY,
    TOKEN_KEY,
    VERSION_KEY,
    Scope,
    Where,
    equals,
)
from tests.memory_writes_support import (
    TOKEN,
    a_memory,
    build_world,
    does_not_reach_the_node,
    made,
    moment,
    stop_the_clock,
    stored_point,
)


@pytest.fixture
async def world(isolated_stack, embedding_spy):
    return build_world(isolated_stack, embedding_spy)


@pytest.fixture
def clock(monkeypatch):
    return stop_the_clock(monkeypatch)


@dataclass
class Marked:
    tenant: UUID
    old: Any
    newer: Any
    before: Any
    result: Any
    calls: list[tuple[str, tuple, dict]]

    @property
    def patch(self) -> tuple[str, tuple, dict]:
        [patch] = made(self.calls, "patch")
        return patch

    @property
    def payload(self) -> dict[str, Any]:
        return self.patch[1][3]


@pytest.fixture
async def marked(world):
    """A memory made obsolete by a newer one, with the calls that made it and the point as it was before."""
    repo, _, tenant, spy, _ = world
    old, newer = await repo.create_memory(tenant, a_memory("Old")), await repo.create_memory(tenant, a_memory("Newer"))
    before = await stored_point(repo, tenant, old.id)
    spy.calls.clear()

    result = await repo.mark_obsolete(tenant, old.id, "replaced by the newer one", newer.id)

    return Marked(tenant, old, newer, before, result, list(spy.calls))


async def test_marking_a_memory_obsolete_says_true(marked):
    assert marked.result is True


async def test_marking_a_memory_obsolete_looks_for_its_successor_without_the_payload(marked):
    [lookup] = [call for call in marked.calls if call[0] == "get_agreed" and call[1][2] == [marked.newer.id]]
    assert lookup[2] == {"with_payload": False}


async def test_marking_a_memory_obsolete_is_one_patch_of_that_memory_guarded_on_its_version_and_its_state(world, marked):
    repo, _, tenant, _, _ = world
    scope, collection, point_id = marked.patch[1][:3]
    assert (scope, collection, point_id) == (Scope.of(tenant), repo.memories, marked.old.id)
    assert marked.patch[2] == {"expected": Where().also(equals(VERSION_KEY, 1), equals("is_obsolete", False))}


async def test_marking_a_memory_obsolete_moves_the_version_on_and_keeps_the_token_it_read_in_the_history(marked):
    assert marked.payload["obsoleted_at"] == marked.payload["updated_at"]
    assert marked.payload[VERSION_KEY] == 2
    assert marked.payload[HISTORY_KEY] == [marked.before.payload[TOKEN_KEY]]


async def test_marking_a_memory_obsolete_patches_the_state_the_reason_and_the_successor(marked):
    patched = {
        key: value for key, value in marked.payload.items() if key not in {"obsoleted_at", "updated_at", TOKEN_KEY}
    }
    assert patched == {
        "is_obsolete": True, "obsolete_reason": "replaced by the newer one", "superseded_by": str(marked.newer.id),
        VERSION_KEY: 2, HISTORY_KEY: [marked.before.payload[TOKEN_KEY]],
    }


async def test_marking_a_memory_obsolete_is_confirmed_by_the_token_it_patched(marked):
    [landed] = made(marked.calls, "landed")
    assert TOKEN.fullmatch(marked.payload[TOKEN_KEY])
    assert landed[2] == {"from_version": 1, "token": marked.payload[TOKEN_KEY]}


async def test_an_obsolete_memory_is_stored_with_its_reason_and_its_successor(world, marked):
    repo, _, tenant, _, _ = world
    stored = (await stored_point(repo, tenant, marked.old.id)).payload
    assert (stored["is_obsolete"], stored["obsolete_reason"], stored["superseded_by"]) == (
        True, "replaced by the newer one", str(marked.newer.id),
    )


async def test_an_obsolete_memory_is_stamped_obsolete_when_it_was_updated(world, marked):
    repo, _, tenant, _, _ = world
    stored = (await stored_point(repo, tenant, marked.old.id)).payload
    assert stored["obsoleted_at"] == stored["updated_at"] > marked.before.payload["updated_at"]


async def test_marking_a_memory_obsolete_without_a_successor_looks_for_none_and_stores_none(world):
    repo, _, tenant, spy, _ = world
    memory = await repo.create_memory(tenant, a_memory("Alone"))
    spy.calls.clear()

    assert await repo.mark_obsolete(tenant, memory.id, "no longer true") is True

    assert [call for call in spy.calls if call[0] == "get_agreed" and "with_payload" in call[2]] == []
    stored = (await stored_point(repo, tenant, memory.id)).payload
    assert (stored.get("superseded_by"), stored["obsolete_reason"]) == (None, "no longer true")


async def test_a_successor_the_tenant_does_not_have_is_named_and_nothing_is_marked(world):
    repo, _, tenant, spy, _ = world
    memory = await repo.create_memory(tenant, a_memory("Waiting"))
    stranger = await repo.create_memory(uuid4(), a_memory("Somebody else's"))

    with pytest.raises(AssociationNotFoundError) as refused:
        await repo.mark_obsolete(tenant, memory.id, "replaced", stranger.id)

    assert str(refused.value) == f"Superseding memory {stranger.id} not found"
    assert made(spy.calls, "patch") == []
    assert (await stored_point(repo, tenant, memory.id)).payload["is_obsolete"] is False


async def test_a_memory_is_obsolete_only_once_and_a_missing_one_is_named(world):
    repo, _, tenant, spy, _ = world
    memory = await repo.create_memory(tenant, a_memory("Once"))
    await repo.mark_obsolete(tenant, memory.id, "first reason")
    missing = uuid4()

    with pytest.raises(InvalidStateTransitionError) as twice:
        await repo.mark_obsolete(tenant, memory.id, "second reason")
    with pytest.raises(NotFoundError) as absent:
        await repo.mark_obsolete(tenant, missing, "reason")

    assert str(twice.value) == f"Memory {memory.id} is already obsolete"
    assert str(absent.value) == f"Memory {missing} not found"
    assert (await stored_point(repo, tenant, memory.id)).payload["obsolete_reason"] == "first reason"
    assert len(made(spy.calls, "patch")) == 1


@dataclass
class Restored:
    tenant: UUID
    old: Any
    before: Any
    result: Any
    calls: list[tuple[str, tuple, dict]]


@pytest.fixture
async def restored(world):
    """A memory that was made obsolete by a newer one and then restored, with the calls that restored it."""
    repo, _, tenant, spy, _ = world
    old, newer = await repo.create_memory(tenant, a_memory("Old")), await repo.create_memory(tenant, a_memory("Newer"))
    await repo.mark_obsolete(tenant, old.id, "replaced", newer.id)
    marked = await stored_point(repo, tenant, old.id)
    spy.calls.clear()

    result = await repo.restore_obsolete(tenant, old.id)

    return Restored(tenant, old, marked, result, list(spy.calls))


async def test_a_restore_says_true(restored):
    assert restored.result is True


async def test_a_restore_is_one_patch_guarded_on_the_version_and_the_obsolete_state(restored):
    [patch] = made(restored.calls, "patch")
    assert patch[2] == {"expected": Where().also(equals(VERSION_KEY, 2), equals("is_obsolete", True))}


async def test_a_restore_clears_the_obsolete_state_moves_the_version_on_and_keeps_the_history(restored):
    [patch] = made(restored.calls, "patch")
    patched = {key: value for key, value in patch[1][3].items() if key not in {"updated_at", TOKEN_KEY}}
    assert patched == {
        "is_obsolete": False, "obsolete_reason": None, "superseded_by": None, "obsoleted_at": None,
        VERSION_KEY: 3, HISTORY_KEY: [restored.before.payload[TOKEN_KEY], *restored.before.payload[HISTORY_KEY]],
    }


async def test_a_restored_memory_is_stored_active_without_the_nulls_of_the_obsolete_state(world, restored):
    repo, _, tenant, _, _ = world
    stored = (await stored_point(repo, tenant, restored.old.id)).payload
    assert stored["is_obsolete"] is False
    assert {"obsolete_reason", "superseded_by", "obsoleted_at"} & stored.keys() == set()  # a null is not kept


async def test_a_restore_says_false_for_a_memory_that_was_not_obsolete_or_is_missing_or_not_the_tenants(world, restored):
    repo, _, tenant, _, _ = world
    assert await repo.restore_obsolete(tenant, restored.old.id) is False
    assert await repo.restore_obsolete(tenant, uuid4()) is False
    assert await repo.restore_obsolete(uuid4(), restored.old.id) is False


async def test_the_state_a_toggle_expected_is_named_when_the_memory_is_in_the_other_one(world):
    repo, _, tenant, _, _ = world
    memory = await repo.create_memory(tenant, a_memory("Active"))

    with pytest.raises(InvalidStateTransitionError) as refused:
        await repo._toggle(Scope.of(tenant), memory.id, was_obsolete=True, changes=lambda _now: {})

    assert str(refused.value) == f"Memory {memory.id} is not obsolete"


async def test_a_toggle_that_never_takes_effect_is_tried_the_set_number_of_times_and_then_refused(world):
    repo, _, tenant, spy, _ = world
    memory = await repo.create_memory(tenant, a_memory("Contested"))
    spy.replaced["patch"] = does_not_reach_the_node

    with pytest.raises(ConflictError) as refused:
        await repo.mark_obsolete(tenant, memory.id, "replaced")

    assert str(refused.value) == (
        f"Memory {memory.id} kept changing while this write was applied; nothing was changed, try again"
    )
    assert len(made(spy.calls, "patch")) == MAX_CAS_ATTEMPTS
    assert len(made(spy.calls, "landed")) == MAX_CAS_ATTEMPTS
    assert (await stored_point(repo, tenant, memory.id)).payload["is_obsolete"] is False


async def test_a_memory_is_read_for_a_write_from_a_majority_in_the_generation_asked_for_or_the_live_one(world):
    repo, stack, tenant, spy, _ = world
    memory = await repo.create_memory(tenant, a_memory("Read for a write"))
    live = await stack.schema.live_generation()
    spy.calls.clear()

    plain = await repo._current(Scope.of(tenant), memory.id)
    with_vector = await repo._current(Scope.of(tenant), memory.id, generation=live, with_vectors=True)

    assert (plain.vector, with_vector.vector is not None) == (None, True)
    assert spy.calls == [
        ("get_agreed", (Scope.of(tenant), repo.memories, [memory.id]), {"with_vectors": False}),
        ("get_agreed", (Scope.of(tenant), live, [memory.id]), {"with_vectors": True}),
    ]


async def test_an_obsoleted_and_a_restored_memory_are_stamped_with_the_moment_read_in_utc(world, clock):
    repo, _, tenant, _, _ = world
    created = await repo.create_memory(tenant, a_memory("Stamped"))

    assert await repo.mark_obsolete(tenant, created.id, "replaced") is True
    obsoleted = (await stored_point(repo, tenant, created.id)).payload
    assert await repo.restore_obsolete(tenant, created.id) is True
    restored = (await stored_point(repo, tenant, created.id)).payload

    assert clock.zones == [UTC, UTC, UTC]
    assert (obsoleted["obsoleted_at"], obsoleted["updated_at"]) == (moment(1), moment(1))
    assert ("obsoleted_at" in restored, restored["updated_at"]) == (False, moment(2))
