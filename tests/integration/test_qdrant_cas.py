"""Concurrent writers and lost acknowledgements on a real Qdrant node.

Edits and obsolete toggles are conditional writes that carry the version the writer read. These tests prove
the two properties that make that safe:

- writers that overlap never lose one another's change (every writer that is told it succeeded finds its
  change in the stored memory), and a toggle that several writers race for has exactly one winner;
- a write whose acknowledgement is lost, and is therefore repeated with the same claim token, takes effect
  once (the repeated request finds its own token on the point and reports success; it does not apply twice).

The race detector is tested too: a deliberately naive writer that reads, merges on the client and writes
without a condition has to be reported as losing changes by the very check the real repository passes.
"""
import asyncio
from uuid import UUID, uuid4

import pytest
from qdrant_client import models as qm
from qdrant_client.http.exceptions import ResponseHandlingException

from app.exceptions import ConflictError, InvalidStateTransitionError
from app.models.memory_models import Memory, MemoryCreate, MemoryUpdate
from app.repositories.qdrant import client as client_module
from app.repositories.qdrant.memory_base import MAX_CAS_ATTEMPTS
from app.repositories.qdrant.store import VERSION_KEY, Scope

WRITERS = 8


def a_memory() -> MemoryCreate:
    return MemoryCreate(
        title="Original title", content="Original content", context="Original context", keywords=["original"],
        tags=["original"], importance=5,
    )


# One disjoint change per writer: if all of them are applied none overwrites another's field.
CHANGES = [
    {"title": "Changed title"},
    {"content": "Changed content"},
    {"context": "Changed context"},
    {"importance": 9},
    {"keywords": ["changed"]},
    {"tags": ["changed"]},
    {"source_repo": "changed/repo"},
    {"confidence": 0.25},
]
assert len(CHANGES) == WRITERS


def lost_changes(stored: Memory, applied: list[dict]) -> list[str]:
    """The fields that a writer reported as applied but the stored memory does not hold."""
    return [
        field
        for change in applied
        for field, value in change.items()
        if getattr(stored, field) != value
    ]


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(client_module, "RETRY_BACKOFF_SECONDS", 0)


@pytest.fixture
async def repo(memory_repository):
    return memory_repository


async def stored_version(repo, tenant: UUID, memory_id: UUID) -> int:
    [point] = await repo.store.get(Scope.of(tenant), repo.memories, [memory_id])
    return int(point.payload[VERSION_KEY])


def hold_writers_until_all_have_read(monkeypatch, store, method: str, parties: int) -> None:
    """Make the first ``parties`` calls of ``store.<method>`` wait for each other, so all have read before any writes."""
    barrier = asyncio.Barrier(parties)
    waiting = 0
    original = getattr(store, method)

    async def gated(*args, **kwargs):
        nonlocal waiting
        if waiting < parties:
            waiting += 1
            await barrier.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(store, method, gated)


async def run_updates(repo, tenant: UUID, memory_id: UUID, changes: list[dict]) -> list[dict]:
    """Apply the changes concurrently; return the ones whose writer was told it succeeded."""
    outcomes = await asyncio.gather(
        *(repo.update_memory(tenant, memory_id, MemoryUpdate(**change)) for change in changes),
        return_exceptions=True,
    )
    assert all(isinstance(outcome, Memory | ConflictError) for outcome in outcomes), outcomes
    return [change for change, outcome in zip(changes, outcomes, strict=True) if isinstance(outcome, Memory)]


async def test_overlapping_updates_never_lose_one_another(repo, monkeypatch):
    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())
    hold_writers_until_all_have_read(monkeypatch, repo.store, "replace", WRITERS)

    applied = await run_updates(repo, tenant, memory.id, CHANGES)

    stored = await repo.get_memory_by_id(tenant, memory.id)
    assert len(applied) >= 1
    assert lost_changes(stored, applied) == []
    assert await stored_version(repo, tenant, memory.id) >= 1 + len(applied)


async def test_the_detector_reports_the_changes_a_check_then_write_updater_loses(repo, monkeypatch):
    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())
    scope = Scope.of(tenant)
    barrier = asyncio.Barrier(WRITERS)

    async def naive_update(change: dict) -> None:
        """Read, merge on the client, write without a condition: the pattern the conditional writes replace."""
        [point] = await repo.store.get(scope, repo.memories, [memory.id], with_vectors=True)
        await barrier.wait()  # every writer has read the same state
        merged = {**point.payload, **change, VERSION_KEY: int(point.payload[VERSION_KEY]) + 1}
        async def write(client):
            await client.upsert(
                repo.memories,
                [qm.PointStruct(id=str(memory.id), vector=point.vector, payload=merged)],
                wait=True,
                ordering=qm.WriteOrdering.STRONG,
            )

        await repo.store.pool.run(write)

    await asyncio.gather(*(naive_update(change) for change in CHANGES))

    stored = await repo.get_memory_by_id(tenant, memory.id)
    assert len(lost_changes(stored, CHANGES)) >= WRITERS - 1


async def test_a_toggle_that_several_writers_race_for_has_one_winner(repo, monkeypatch):
    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())
    hold_writers_until_all_have_read(monkeypatch, repo.store, "patch", WRITERS)

    outcomes = await asyncio.gather(
        *(repo.mark_obsolete(tenant, memory.id, f"reason {index}") for index in range(WRITERS)),
        return_exceptions=True,
    )

    assert [outcome for outcome in outcomes if outcome is True] == [True]
    assert all(isinstance(outcome, InvalidStateTransitionError) for outcome in outcomes if outcome is not True)
    stored = await repo.get_memory_by_id(tenant, memory.id)
    assert stored.is_obsolete
    assert stored.obsolete_reason in {f"reason {index}" for index in range(WRITERS)}
    assert await stored_version(repo, tenant, memory.id) == 2


@pytest.mark.parametrize("toggle", ["mark_obsolete", "restore_obsolete"])
async def test_a_toggle_whose_write_never_lands_is_a_conflict_after_the_bounded_attempts(repo, monkeypatch, toggle):
    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())
    if toggle == "restore_obsolete":
        await repo.mark_obsolete(tenant, memory.id, "first")
    attempts = []

    async def never_lands(*_args, **_kwargs):
        attempts.append(1)

    monkeypatch.setattr(repo.store, "patch", never_lands)
    arguments = ("reason",) if toggle == "mark_obsolete" else ()

    with pytest.raises(ConflictError):
        await getattr(repo, toggle)(tenant, memory.id, *arguments)

    assert len(attempts) == MAX_CAS_ATTEMPTS
    assert (await repo.get_memory_by_id(tenant, memory.id)).is_obsolete == (toggle == "restore_obsolete")


# ---- lost acknowledgements ---------------------------------------------------------------------------


class LossyClient:
    """The real client, with a script for its writes: ``before`` fails without applying, ``after`` applies and then fails."""

    def __init__(self, client, behaviours: list[str]):
        self._client = client
        self.behaviours = behaviours
        self.writes = 0

    def __getattr__(self, name):
        target = getattr(self._client, name)
        if name not in {"upsert", "set_payload", "delete"}:
            return target

        async def call(*args, **kwargs):
            self.writes += 1
            behaviour = self.behaviours.pop(0) if self.behaviours else "ok"
            if behaviour == "before":
                raise ResponseHandlingException(Exception("connection reset before the request was sent"))
            result = await target(*args, **kwargs)
            if behaviour == "after":
                raise ResponseHandlingException(Exception("connection reset before the answer arrived"))
            return result

        return call


def lose_acknowledgements(repo, behaviours: list[str]) -> LossyClient:
    lossy = LossyClient(repo.store.pool.clients[0], behaviours)
    repo.store.pool.clients = [lossy]
    return lossy


@pytest.mark.parametrize("behaviour", ["before", "after"])
async def test_an_update_whose_answer_is_lost_takes_effect_once(repo, behaviour):
    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())
    lossy = lose_acknowledgements(repo, [behaviour])

    updated = await repo.update_memory(tenant, memory.id, MemoryUpdate(title="Changed once"))

    assert updated.title == "Changed once"
    assert lossy.writes == 2  # the lost request, then the repeat of the very same request
    assert await stored_version(repo, tenant, memory.id) == 2


@pytest.mark.parametrize("behaviour", ["before", "after"])
async def test_an_obsolete_mark_whose_answer_is_lost_takes_effect_once(repo, behaviour):
    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())
    lose_acknowledgements(repo, [behaviour])

    assert await repo.mark_obsolete(tenant, memory.id, "superseded") is True

    assert (await repo.get_memory_by_id(tenant, memory.id)).is_obsolete
    assert await stored_version(repo, tenant, memory.id) == 2


@pytest.mark.parametrize("behaviour", ["before", "after"])
async def test_a_create_whose_answer_is_lost_stores_one_memory(repo, behaviour):
    tenant = uuid4()
    lose_acknowledgements(repo, [behaviour])

    created = await repo.create_memory(tenant, a_memory())

    memories, total = await repo.list_memories(tenant, limit=10)
    assert total == 1
    assert [memory.id for memory in memories] == [created.id]


async def test_a_link_whose_answer_is_lost_is_stored_once(repo):
    tenant = uuid4()
    first, second = await repo.create_memory(tenant, a_memory()), await repo.create_memory(tenant, a_memory())
    lose_acknowledgements(repo, ["after"])

    await repo.create_links_batch(tenant, first.id, [second.id])

    assert (await repo.get_memory_by_id(tenant, first.id)).linked_memory_ids == [second.id]
    assert (await repo.get_memory_by_id(tenant, second.id)).linked_memory_ids == [first.id]


async def test_a_write_that_keeps_failing_is_raised_after_the_attempts(repo, monkeypatch):
    from app.config.settings import settings

    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())
    monkeypatch.setattr(settings, "QDRANT_WRITE_ATTEMPTS", 2)
    lossy = lose_acknowledgements(repo, ["before"] * 5)

    with pytest.raises(ResponseHandlingException):
        await repo.update_memory(tenant, memory.id, MemoryUpdate(title="Never stored"))

    assert lossy.writes == 2
    lossy.behaviours.clear()
    assert (await repo.get_memory_by_id(tenant, memory.id)).title == "Original title"
