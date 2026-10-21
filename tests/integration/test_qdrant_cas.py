"""Concurrent writers and lost acknowledgements on a real Qdrant node.

Edits and obsolete toggles are conditional writes that carry the version the writer read. These tests prove
the two properties that make that safe:

- writers that overlap never lose one another's change (every writer that is told it succeeded finds its
  change in the stored memory), and a toggle that several writers race for has exactly one winner;
- a write whose acknowledgement is lost, and is therefore repeated with the same claim token, takes effect
  once (the repeated request finds its own token on the point and reports success; it does not apply twice).

- a write that landed and was then overtaken by later writes before its writer could read it back is still
  counted as applied (each point keeps the claim tokens of its latest versions), so the writer does not replay its
  change over the newer state; and when more writes landed than a point remembers, the writer is told that the
  outcome is unknown instead of being told that it lost.

The race detector is tested too: a deliberately naive writer that reads, merges on the client and writes
without a condition has to be reported as losing changes by the very check the real repository passes.
"""
import asyncio
from uuid import UUID, uuid4

import pytest
from qdrant_client import models as qm
from qdrant_client.http.exceptions import ResponseHandlingException

from app.exceptions import (
    ConflictError,
    InvalidStateTransitionError,
    OutcomeUnknownError,
)
from app.models.memory_models import Memory, MemoryCreate, MemoryUpdate
from app.repositories.qdrant import client as client_module
from app.repositories.qdrant.memory_base import MAX_CAS_ATTEMPTS
from app.repositories.qdrant.store import CLAIM_HISTORY, VERSION_KEY, Scope

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
    # Every writer that was told it succeeded moved the version on exactly once: none replayed an applied change
    assert await stored_version(repo, tenant, memory.id) == 1 + len(applied)


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


# ---- writes overtaken before their read-back ---------------------------------------------------------


def after_the_write_lands_run(monkeypatch, store, other_writers):
    """Run ``other_writers`` once, between the first conditional write of the test and the read-back of its outcome."""
    original = store.landed
    ran = False

    async def landed(*args, **kwargs):
        nonlocal ran
        if not ran:
            ran = True
            await other_writers()
        return await original(*args, **kwargs)

    monkeypatch.setattr(store, "landed", landed)


async def test_a_toggle_overtaken_by_the_opposite_toggle_is_not_replayed(repo, monkeypatch):
    """A marks the memory obsolete; before A reads its write back, B restores it. A must not mark it obsolete again."""
    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())

    async def restore() -> None:
        assert await repo.restore_obsolete(tenant, memory.id) is True

    after_the_write_lands_run(monkeypatch, repo.store, restore)

    assert await repo.mark_obsolete(tenant, memory.id, "superseded") is True

    stored = await repo.get_memory_by_id(tenant, memory.id)
    assert not stored.is_obsolete  # B's restore came last and stands
    assert await stored_version(repo, tenant, memory.id) == 3  # one version per toggle, no replay


async def test_an_update_overtaken_by_an_update_of_the_same_field_does_not_overwrite_it(repo, monkeypatch):
    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())

    async def other_writer() -> None:
        await repo.update_memory(tenant, memory.id, MemoryUpdate(title="Second writer's title"))

    after_the_write_lands_run(monkeypatch, repo.store, other_writer)

    first = await repo.update_memory(tenant, memory.id, MemoryUpdate(title="First writer's title"))

    assert first.title == "First writer's title"  # what the first writer wrote, as it was told
    stored = await repo.get_memory_by_id(tenant, memory.id)
    assert stored.title == "Second writer's title"  # the later write stands; the first was not replayed over it
    assert await stored_version(repo, tenant, memory.id) == 3


async def test_a_write_overtaken_by_more_writes_than_a_point_remembers_has_an_unknown_outcome(repo, monkeypatch):
    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())

    async def a_crowd_of_writers() -> None:
        for importance in range(CLAIM_HISTORY + 1):
            await repo.update_memory(tenant, memory.id, MemoryUpdate(importance=1 + importance % 9))

    after_the_write_lands_run(monkeypatch, repo.store, a_crowd_of_writers)

    with pytest.raises(OutcomeUnknownError, match="may or may not have been applied") as raised:
        await repo.update_memory(tenant, memory.id, MemoryUpdate(title="Landed, then buried"))

    assert isinstance(raised.value, ConflictError)  # REST answers 409, MCP shows the message
    stored = await repo.get_memory_by_id(tenant, memory.id)
    assert stored.title == "Landed, then buried"  # it did apply: the error says only that the writer cannot tell


async def test_writers_of_the_same_field_and_togglers_move_the_version_once_per_reported_success(repo, monkeypatch):
    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())
    hold_writers_until_all_have_read(monkeypatch, repo.store, "replace", WRITERS)

    changes = [{"title": f"Writer {index}"} for index in range(WRITERS)]
    outcomes = await asyncio.gather(
        *(repo.update_memory(tenant, memory.id, MemoryUpdate(**change)) for change in changes),
        return_exceptions=True,
    )
    toggles = await asyncio.gather(
        *(repo.mark_obsolete(tenant, memory.id, f"reason {index}") for index in range(WRITERS)),
        return_exceptions=True,
    )

    successes = [outcome for outcome in outcomes if isinstance(outcome, Memory)]
    assert all(isinstance(outcome, Memory | ConflictError) for outcome in outcomes), outcomes
    assert [toggle for toggle in toggles if toggle is True] == [True]
    assert await stored_version(repo, tenant, memory.id) == 1 + len(successes) + 1


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


# ---- an edit that straddles a re-embedding switch --------------------------------------------------------------------


async def test_an_edit_that_straddles_the_switch_of_the_live_generation_is_made_again_on_the_new_one(
    isolated_stack, tensorrt_embedder, monkeypatch,
):
    """The vector read from the old generation must not be written into the new one (another model's vector space)."""
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())
    old_generation = await repo.live_generation()
    new_generation = await repo.create_generation()
    new_vector = await tensorrt_embedder.generate_embedding(text="the memory as another model sees it")
    [stored], _ = await repo.outdated_in(old_generation, new_generation, 10, None)
    await repo.copy_into(new_generation, [(stored, new_vector)])
    switched = []
    replace = repo.store.replace

    async def switching_before_the_first_write(*args, **kwargs):
        if not switched:
            switched.append(new_generation)
            await repo.activate_generation(new_generation)  # a re-embedding completes between the read and the write
        return await replace(*args, **kwargs)

    monkeypatch.setattr(repo.store, "replace", switching_before_the_first_write)

    updated = await repo.update_memory(tenant, memory.id, MemoryUpdate(importance=9))  # the text is not embedded again

    assert updated.importance == 9
    [live] = await repo.store.get(Scope.of(tenant), repo.memories, [memory.id], with_vectors=True)
    assert live.payload["importance"] == 9
    assert live.vector == pytest.approx(new_vector)  # the vector of the generation that serves, not the old one's
    assert int(live.payload[VERSION_KEY]) == 2
    assert (await repo.get_memory_by_id(tenant, memory.id)).importance == 9
