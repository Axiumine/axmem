"""Races between writers spread over the nodes of a real three-node cluster.

The pool starts every request at a different node, so concurrent writers reach different nodes of the cluster
and every conditional write has to be ordered by Qdrant, not by the node that received it. The invariants are
the same as on one node (tests/integration/test_qdrant_cas.py): a writer that is told it succeeded finds its
change in the stored memory, and a toggle that many writers race for has one winner. The detector is tested
too: a writer that checks on the client has to be reported.
"""
import asyncio
from uuid import uuid4

import pytest
from qdrant_client import models as qm

from app.exceptions import ConflictError, InvalidStateTransitionError
from app.models.memory_models import Memory, MemoryUpdate
from app.repositories.qdrant.store import VERSION_KEY, Scope
from tests.cluster.cluster_support import (
    CHANGES,
    a_memory,
    lost_changes,
    stored_version,
    wait_until_the_nodes_agree,
)

pytestmark = pytest.mark.cluster

MEMORIES = 4
TOGGLERS = 60
RACE_SECONDS = 180


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


async def run_updates(repo, tenant, memory_id, changes: list[dict]) -> list[dict]:
    """The changes whose writer was told it succeeded (the others lost every attempt and were told so)."""
    outcomes = await asyncio.gather(
        *(repo.update_memory(tenant, memory_id, MemoryUpdate(**change)) for change in changes),
        return_exceptions=True,
    )
    assert all(isinstance(outcome, Memory | ConflictError) for outcome in outcomes), outcomes
    return [change for change, outcome in zip(changes, outcomes, strict=True) if isinstance(outcome, Memory)]


async def test_overlapping_updates_through_every_node_never_lose_one_another(cluster_stack, repo, monkeypatch):
    tenant = uuid4()
    memories = [await repo.create_memory(tenant, a_memory(index)) for index in range(MEMORIES)]
    hold_writers_until_all_have_read(monkeypatch, repo.store, "replace", MEMORIES * len(CHANGES))

    async with asyncio.timeout(RACE_SECONDS):
        applied = await asyncio.gather(*(run_updates(repo, tenant, memory.id, CHANGES) for memory in memories))

    for memory, won in zip(memories, applied, strict=True):
        stored = await repo.get_memory_by_id(tenant, memory.id)
        assert len(won) >= 1
        assert lost_changes(stored, won) == []
        assert await stored_version(repo, tenant, memory.id) >= 1 + len(won)
    assert (await wait_until_the_nodes_agree(cluster_stack)).consistent


async def test_a_toggle_that_sixty_writers_race_for_has_one_winner(cluster_stack, repo, monkeypatch):
    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())
    hold_writers_until_all_have_read(monkeypatch, repo.store, "patch", TOGGLERS)

    async with asyncio.timeout(RACE_SECONDS):
        outcomes = await asyncio.gather(
            *(repo.mark_obsolete(tenant, memory.id, f"reason {index}") for index in range(TOGGLERS)),
            return_exceptions=True,
        )

    assert [outcome for outcome in outcomes if outcome is True] == [True]
    assert all(isinstance(outcome, InvalidStateTransitionError | ConflictError) for outcome in outcomes if outcome is not True)
    assert (await repo.get_memory_by_id(tenant, memory.id)).is_obsolete
    assert await stored_version(repo, tenant, memory.id) == 2
    assert (await wait_until_the_nodes_agree(cluster_stack)).consistent


# ---- the detectors -----------------------------------------------------------------------------------


async def test_the_detector_reports_a_check_then_write_updater_on_the_cluster(cluster_stack, repo):
    tenant = uuid4()
    memory = await repo.create_memory(tenant, a_memory())
    scope = Scope.of(tenant)
    barrier = asyncio.Barrier(len(CHANGES))

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

    async with asyncio.timeout(RACE_SECONDS):
        await asyncio.gather(*(naive_update(change) for change in CHANGES))

    stored = await repo.get_memory_by_id(tenant, memory.id)
    assert len(lost_changes(stored, CHANGES)) >= len(CHANGES) - 1
