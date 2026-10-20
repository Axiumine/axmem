"""Writes, reads and recovery while nodes of a real three-node cluster are stopped or killed.

Replication factor 3 with write consistency factor 2 lets one node be gone: the pool moves on to the next node,
writes are acknowledged by the two that remain, and the node that returns has to catch up. The fixture gives
every node back, and waits for all replicas to be Active, so a failure here cannot leak into the next test.
"""
from uuid import uuid4

import pytest

from app.exceptions import ClusterInconsistentError
from app.models.memory_models import MemoryUpdate
from app.repositories.qdrant.store import QdrantStore, Scope
from app.services.backup_service import BackupService
from tests.cluster.cluster_support import (
    a_memory,
    wait_for_active_replicas,
    wait_until_the_nodes_agree,
)
from tests.qdrant_support import NODES

pytestmark = pytest.mark.cluster


@pytest.fixture
def nodes_come_back(cluster, cluster_stack):
    """Hand the test the cluster; whatever it stopped is started again and healed before the next test."""
    yield cluster
    for index in range(NODES):
        cluster.start_node(index)  # starting a running container does nothing
    for name in (cluster_stack.schema.names.live, cluster_stack.schema.names.links):
        wait_for_active_replicas(cluster, name)


async def held_by_every_node(stack, repo, tenant, memory_ids) -> list[bool]:
    """Whether each node, read on its own, holds all of ``memory_ids``."""
    found = []
    for index in range(NODES):
        points = await QdrantStore(stack.pool.only(index)).get(Scope.of(tenant), repo.memories, memory_ids)
        found.append(sorted(point.id for point in points) == sorted(memory_ids))
    return found


@pytest.mark.parametrize("how", ["stop", "kill"])
async def test_a_cluster_with_one_node_gone_keeps_serving_and_the_node_catches_up(
    nodes_come_back, cluster_stack, repo, how,
):
    tenant = uuid4()
    before = await repo.create_memory(tenant, a_memory(0))
    getattr(nodes_come_back, f"{how}_node")(2)

    during = await repo.create_memory(tenant, a_memory(1))
    changed = await repo.update_memory(tenant, before.id, MemoryUpdate(title="Changed while a node is down"))
    await repo.create_links_batch(tenant, before.id, [during.id])

    assert changed.title == "Changed while a node is down"
    assert (await repo.get_memory_by_id(tenant, during.id)).linked_memory_ids == [before.id]
    memories, total = await repo.list_memories(tenant, limit=10)
    assert total == 2
    assert {memory.id for memory in memories} == {before.id, during.id}
    found = await repo.search_scored(
        user_id=tenant, query="Content of cluster memory 1", query_context="failover", k=5,
        importance_threshold=None, project=None, exclude_ids=None,
    )
    assert during.id in {memory.id for memory, _score in found}

    nodes_come_back.start_node(2)
    for name in (cluster_stack.schema.names.live, cluster_stack.schema.names.links):
        wait_for_active_replicas(nodes_come_back, name)
    report = await wait_until_the_nodes_agree(cluster_stack)

    assert report.consistent, report
    assert await held_by_every_node(cluster_stack, repo, tenant, [before.id, during.id]) == [True, True, True]
    assert (await repo.get_memory_by_id(tenant, before.id)).title == "Changed while a node is down"


async def test_a_backup_is_refused_while_a_node_cannot_be_read(nodes_come_back, cluster_stack, repo):
    from app.bootstrap import create_backup_service

    nodes_come_back.stop_node(1)
    service: BackupService = create_backup_service(cluster_stack)

    report = await service.check()
    assert not report.consistent
    assert [node.error is not None for node in report.nodes] == [False, True, False]

    with pytest.raises(ClusterInconsistentError, match="1 unreadable nodes"):
        await service.create_backup()
