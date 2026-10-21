"""Helpers the cluster tests share: building memories, waiting for the nodes to agree, and the races themselves."""
import asyncio
import random
import time
from collections import Counter
from uuid import UUID

import httpx

from app.exceptions import ConflictError
from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.repositories.qdrant.inspector import QdrantClusterInspector
from app.repositories.qdrant.memory_repository import QdrantStack
from app.repositories.qdrant.store import VERSION_KEY, Scope
from tests.qdrant_support import QdrantCluster

AGREE_TIMEOUT_SECONDS = 120
HEAL_TIMEOUT_SECONDS = 180


def a_memory(index: int = 0) -> MemoryCreate:
    return MemoryCreate(
        title=f"Cluster memory {index}", content=f"Content of cluster memory {index}", context="Cluster test",
        keywords=["cluster"], tags=["original"], importance=5,
    )


# One disjoint change per writer: if all of them are applied none overwrites another's field.
CHANGES: list[dict] = [
    {"title": "Changed title"},
    {"content": "Changed content"},
    {"context": "Changed context"},
    {"importance": 9},
    {"keywords": ["changed"]},
    {"tags": ["changed"]},
    {"source_repo": "changed/repo"},
    {"confidence": 0.25},
]


def lost_changes(stored, applied: list[dict]) -> list[str]:
    """The fields that a writer reported as applied but the stored memory does not hold."""
    return [field for change in applied for field, value in change.items() if getattr(stored, field) != value]


async def wait_until_the_nodes_agree(stack: QdrantStack, timeout: float = AGREE_TIMEOUT_SECONDS):
    """Poll the per-node comparison until it reports no difference; return the last report."""
    inspector = QdrantClusterInspector(stack.pool, stack.schema)
    deadline = time.monotonic() + timeout
    report = await inspector.check()
    while not report.consistent and time.monotonic() < deadline:
        await asyncio.sleep(1)
        report = await inspector.check()
    return report


async def stored_version(repo, tenant: UUID, memory_id: UUID) -> int:
    [point] = await repo.store.get(Scope.of(tenant), repo.memories, [memory_id], quorum=True)
    return int(point.payload[VERSION_KEY])


def replica_states(cluster: QdrantCluster, collection: str) -> list[str]:
    """The state of every replica of every shard of ``collection``, as the first node reports it."""
    reply = httpx.get(
        f"{cluster.urls[0]}/collections/{collection}/cluster", headers={"api-key": cluster.api_key}, timeout=5,
    ).json()["result"]
    return [str(item["state"]) for item in reply["local_shards"] + reply["remote_shards"]]


def wait_for_active_replicas(
    cluster: QdrantCluster, collection: str, timeout: float = HEAL_TIMEOUT_SECONDS, replicas: int | None = None,
) -> None:
    """Block until the cluster is formed and all ``replicas`` replicas of ``collection`` (shards x replication factor) are Active.

    The default is the documented profile of the suites: a shard and a replica on every node.
    """
    replicas = replicas if replicas is not None else cluster.nodes * cluster.nodes
    deadline = time.monotonic() + timeout
    states: list[str] = []
    cluster.wait_formed(timeout)
    while time.monotonic() < deadline:
        try:
            states = replica_states(cluster, collection)
        except (httpx.HTTPError, KeyError, ValueError):
            states = []
        if states and set(states) == {"Active"} and len(states) == replicas:
            return
        time.sleep(1)
    raise RuntimeError(f"the replicas of {collection} did not all become Active: {states}")


async def rewrite_memories(repo, tenant: UUID, ids: list[UUID], stop: asyncio.Event, acked: Counter, seed: int) -> None:
    """Rewrite random memories among ``ids`` until ``stop`` is set, counting the updates the writer was told succeeded.

    An update writes the whole point and moves its version on by one, so the replicas of a point are in different
    versions for the moments between the writes landing on them: what a majority scan trips over.
    """
    chance = random.Random(seed)
    while not stop.is_set():
        memory_id = chance.choice(ids)
        try:
            await repo.update_memory(tenant, memory_id, MemoryUpdate(importance=chance.randint(1, 10)))
        except ConflictError:
            continue  # lost every attempt, and was told so
        acked[memory_id] += 1
