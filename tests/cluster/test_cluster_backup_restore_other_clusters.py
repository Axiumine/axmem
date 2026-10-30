# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Backup and restore on a four-node cluster, and a restore into another cluster than the one the backup came from.

``test_cluster_backup_restore`` runs the documented three-node cluster in every replication layout. Two things stay
that a three-node run cannot show: a node count other than three (a shard count that is not the node count, a replica
set that leaves a node out of some shards), and a restore into a cluster whose nodes are not the ones that made the
backup (other containers, other ports, other peer ids, the addresses listed in another order). Each test runs on
collections of its own and removes them.
"""
from uuid import uuid4

import pytest

from app.bootstrap import create_backup_service
from app.config.settings import settings
from app.repositories.qdrant.memory_repository import QdrantStack
from tests.cluster.conftest import point_settings_at_the_cluster
from tests.cluster.test_cluster_backup_restore import (
    assert_as_backed_up,
    change_after_the_backup,
    lose_the_collections,
    seed,
    stored,
    use_layout,
)
from tests.qdrant_support import QdrantCluster, drop_prefix, start_cluster, stop_cluster

pytestmark = pytest.mark.cluster

FOUR = 4
# shards, replication factor, write consistency factor, whether the collections are gone (an empty Qdrant) at the restore.
# A replica on every node, two of four, one of four, and fewer shards than nodes with a replica set that leaves a node out.
FOUR_NODE_LAYOUTS = [(4, 4, 3, False), (4, 2, 1, False), (4, 1, 1, True), (2, 3, 2, True)]


@pytest.fixture(scope="module")
def four_nodes():
    """A cluster of four nodes, kept in memory (no test here stops a node), removed when this module has run."""
    running = start_cluster(FOUR, in_memory=True)
    yield running
    stop_cluster(running)


@pytest.fixture(scope="module")
def another_cluster():
    """A second three-node cluster in memory: other containers, other ports, other peer ids than the suite's."""
    running = start_cluster(in_memory=True)
    yield running
    stop_cluster(running)


@pytest.mark.parametrize(
    ("shards", "replication", "write_consistency", "emptied"), FOUR_NODE_LAYOUTS,
    ids=lambda value: str(value) if not isinstance(value, bool) else ("emptied" if value else "changed"),
)
async def test_a_restore_on_four_nodes_brings_back_every_memory_and_link(
    four_nodes, tensorrt_embedder, monkeypatch, tmp_path, shards, replication, write_consistency, emptied,
):
    prefix = use_layout(monkeypatch, four_nodes, tmp_path, replication, write_consistency)
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", shards)
    stack = QdrantStack.from_settings()
    try:
        await stack.schema.ensure()
        repo = stack.memory_repository(tensorrt_embedder)
        service = create_backup_service(stack)
        tenant = uuid4()
        memories = await seed(stack, repo, tenant, replication)
        wanted = await stored(repo, tenant, memories)

        backup = await service.create_backup()

        assert sorted({file.name for file in backup.files}) == sorted(
            f"node{node}-{role}.snapshot" for node in range(1, FOUR + 1) for role in ("memories", "links")
        )
        extra = await change_after_the_backup(repo, tenant, memories)
        live = await stack.schema.live_generation()
        if emptied:
            await lose_the_collections(stack, live)
        await service.restore_backup(backup.directory)

        assert await stack.schema.live_generation() == live
        await assert_as_backed_up(four_nodes, stack, repo, tenant, memories, wanted, extra, replication, shards)
    finally:
        await stack.close()
        drop_prefix(four_nodes.urls[0], four_nodes.api_key, prefix)


def point_at(monkeypatch, cluster: QdrantCluster, prefix: str, *, reverse: bool) -> None:
    """The settings of a deployment on ``cluster``, with the addresses in the order the nodes were not started in."""
    point_settings_at_the_cluster(monkeypatch, cluster, prefix)
    urls = list(reversed(cluster.urls)) if reverse else cluster.urls
    monkeypatch.setattr(settings, "QDRANT_URL", ",".join(urls))


@pytest.mark.parametrize(("replication", "write_consistency"), [(3, 2), (2, 1), (1, 1)])
async def test_a_backup_restores_into_another_cluster_with_other_nodes_and_another_order_of_addresses(
    cluster, another_cluster, tensorrt_embedder, monkeypatch, tmp_path, replication, write_consistency,
):
    prefix = use_layout(monkeypatch, cluster, tmp_path, replication, write_consistency)
    source = QdrantStack.from_settings()
    target = None
    try:
        await source.schema.ensure()
        repo = source.memory_repository(tensorrt_embedder)
        tenant = uuid4()
        memories = await seed(source, repo, tenant, replication)
        wanted = await stored(repo, tenant, memories)
        live = await source.schema.live_generation()
        backup = await create_backup_service(source).create_backup()
        extra = await change_after_the_backup(repo, tenant, memories)  # in the source only: the backup does not hold it
        await source.close()

        point_at(monkeypatch, another_cluster, prefix, reverse=True)
        monkeypatch.setattr(settings, "QDRANT_REPLICATION_FACTOR", replication)  # the deployment's layout, on other nodes
        monkeypatch.setattr(settings, "QDRANT_WRITE_CONSISTENCY_FACTOR", write_consistency)
        target = QdrantStack.from_settings()
        assert not await target.schema.live_exists()  # an empty cluster: nothing of the backup's deployment is there
        await create_backup_service(target).restore_backup(backup.directory)

        assert await target.schema.live_generation() == live
        await assert_as_backed_up(
            another_cluster, target, target.memory_repository(tensorrt_embedder), tenant, memories, wanted, extra, replication,
        )
    finally:
        if target is not None:
            await target.close()
        drop_prefix(cluster.urls[0], cluster.api_key, prefix)
        drop_prefix(another_cluster.urls[0], another_cluster.api_key, prefix)


async def test_a_backup_of_four_nodes_is_refused_on_a_cluster_of_three_and_nothing_is_uploaded(
    four_nodes, another_cluster, tensorrt_embedder, monkeypatch, tmp_path,
):
    prefix = use_layout(monkeypatch, four_nodes, tmp_path, 2, 1)
    source = QdrantStack.from_settings()
    target = None
    try:
        await source.schema.ensure()
        await seed(source, source.memory_repository(tensorrt_embedder), uuid4(), 2)
        backup = await create_backup_service(source).create_backup()
        await source.close()

        point_at(monkeypatch, another_cluster, prefix, reverse=False)
        target = QdrantStack.from_settings()
        with pytest.raises(ValueError, match=r"snapshots of node 4 but QDRANT_URL lists 3 nodes"):
            await create_backup_service(target).restore_backup(backup.directory)

        assert not await target.schema.live_exists()
    finally:
        if target is not None:
            await target.close()
        drop_prefix(four_nodes.urls[0], four_nodes.api_key, prefix)


async def test_a_backup_of_three_nodes_is_refused_on_a_cluster_of_four_and_nothing_is_uploaded(
    cluster, four_nodes, tensorrt_embedder, monkeypatch, tmp_path,
):
    prefix = use_layout(monkeypatch, cluster, tmp_path, 2, 1)
    source = QdrantStack.from_settings()
    target = None
    try:
        await source.schema.ensure()
        await seed(source, source.memory_repository(tensorrt_embedder), uuid4(), 2)
        backup = await create_backup_service(source).create_backup()
        await source.close()

        point_at(monkeypatch, four_nodes, prefix, reverse=False)
        target = QdrantStack.from_settings()
        with pytest.raises(ValueError, match=r"node 4 needs exactly one memories and one links snapshot"):
            await create_backup_service(target).restore_backup(backup.directory)

        assert not await target.schema.live_exists()
    finally:
        if target is not None:
            await target.close()
        drop_prefix(cluster.urls[0], cluster.api_key, prefix)
