"""Backup and restore on a real three-node cluster, in each replication layout.

With a replication factor below the number of nodes a node stores only some of the shards, so its snapshot holds only
some of the data. A restore must therefore put every node's snapshot back on that node; restoring one node's
snapshot alone would bring back part of the memories. Each layout runs on collections of its own (the suite's shared
ones keep the documented 3/3/2 layout): memories are written, the set is taken, the data is changed, the set is
restored, and every memory and link must be what the backup held.
"""
from uuid import uuid4

import pytest

from app.bootstrap import create_backup_service
from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.memory_models import MemoryUpdate
from app.repositories.qdrant.memory_repository import QdrantStack
from app.repositories.qdrant.schema import EMBEDDER_KEY, embedder_identity
from tests.cluster.cluster_support import (
    a_memory,
    wait_for_active_replicas,
    wait_until_the_nodes_agree,
)
from tests.cluster.conftest import point_settings_at_the_cluster
from tests.qdrant_support import drop_prefix, unique_prefix

pytestmark = pytest.mark.cluster

SHARDS = 3
# After the last upload Qdrant re-syncs the replicas it marked Dead, with shard transfers that restart when another
# node restores meanwhile; on a loaded host that takes minutes
SETTLE_SECONDS = 900
# replication factor, write consistency factor, whether the collections are gone (an empty Qdrant) when the set is restored
LAYOUTS = [(3, 2, False), (2, 1, False), (1, 1, False), (3, 2, True), (2, 1, True), (1, 1, True)]


def use_layout(monkeypatch, cluster, tmp_path, replication: int, write_consistency: int) -> str:
    """Point the settings at collections of this test, in the layout under test; the prefix of the collections."""
    prefix = unique_prefix()
    point_settings_at_the_cluster(monkeypatch, cluster, prefix)
    monkeypatch.setattr(settings, "QDRANT_REPLICATION_FACTOR", replication)
    monkeypatch.setattr(settings, "QDRANT_WRITE_CONSISTENCY_FACTOR", write_consistency)
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    return prefix


async def seed(stack, repo, tenant, replication: int):
    """Twelve memories and three links; with replicas, once the nodes agree about them."""
    memories = [await repo.create_memory(tenant, a_memory(index)) for index in range(12)]
    await repo.create_links_batch(tenant, memories[0].id, [memories[1].id, memories[2].id])
    await repo.create_links_batch(tenant, memories[3].id, [memories[4].id])
    if replication > 1:
        assert (await wait_until_the_nodes_agree(stack)).consistent
    return memories


async def change_after_the_backup(repo, tenant, memories):
    """Edit, obsolete, unlink and add: everything a restore has to undo. The memory that did not exist at the backup."""
    await repo.update_memory(tenant, memories[5].id, MemoryUpdate(title="Changed after the backup"))
    await repo.mark_obsolete(tenant, memories[6].id, "changed after the backup")
    await repo.unlink_memories(tenant, memories[0].id, memories[1].id)
    return await repo.create_memory(tenant, a_memory(99))


async def stored(repo, tenant, memories) -> dict:
    return {memory.id: await repo.get_memory_by_id(tenant, memory.id) for memory in memories}


async def lose_the_collections(stack, live: str) -> None:
    """A Qdrant that lost its collections, as after a disk failure."""
    for collection in (live, stack.schema.names.links):
        await stack.schema.discard(collection)
    assert not await stack.schema.live_exists()


async def assert_as_backed_up(
    cluster, stack, repo, tenant, memories, wanted, extra, replication: int, shards: int = SHARDS,
) -> None:
    live = await stack.schema.live_generation()
    for collection in (live, stack.schema.names.links):
        wait_for_active_replicas(cluster, collection, SETTLE_SECONDS, replicas=shards * replication)
    restored = await stored(repo, tenant, memories)
    assert restored == wanted
    with pytest.raises(NotFoundError):
        await repo.get_memory_by_id(tenant, extra.id)
    assert set(restored[memories[0].id].linked_memory_ids) == {memories[1].id, memories[2].id}
    report = await wait_until_the_nodes_agree(stack, SETTLE_SECONDS)
    assert report.consistent, report
    assert len({node.points for node in report.nodes}) == 1
    assert report.nodes[0].points == len(memories) + 3  # twelve memories and three links
    # The embedder the generation records came back with the snapshot, which ensure() below would otherwise record again
    metadata = (await stack.pool.clients[0].get_collection(live)).config.metadata or {}
    assert metadata.get(EMBEDDER_KEY) == embedder_identity(settings.EMBEDDING_DIMENSIONS)
    await stack.schema.ensure()  # the restored collections are the ones the settings describe


@pytest.mark.parametrize(
    ("replication", "write_consistency", "emptied"), LAYOUTS,
    ids=lambda value: str(value) if not isinstance(value, bool) else ("emptied" if value else "changed"),
)
async def test_a_restore_brings_back_every_memory_and_link_whatever_the_replication_factor(
    cluster, tensorrt_embedder, monkeypatch, tmp_path, replication, write_consistency, emptied,
):
    prefix = use_layout(monkeypatch, cluster, tmp_path, replication, write_consistency)
    stack = QdrantStack.from_settings()
    try:
        await stack.schema.ensure()
        repo = stack.memory_repository(tensorrt_embedder)
        service = create_backup_service(stack)
        tenant = uuid4()
        memories = await seed(stack, repo, tenant, replication)
        wanted = await stored(repo, tenant, memories)

        backup = await service.create_backup()

        extra = await change_after_the_backup(repo, tenant, memories)
        live = await stack.schema.live_generation()
        if emptied:
            await lose_the_collections(stack, live)
        await service.restore_backup(backup.directory)

        assert await stack.schema.live_generation() == live
        await assert_as_backed_up(cluster, stack, repo, tenant, memories, wanted, extra, replication)
    finally:
        await stack.close()
        drop_prefix(cluster.urls[0], cluster.api_key, prefix)
