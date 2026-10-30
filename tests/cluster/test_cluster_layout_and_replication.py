# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The collection layout and the replication of writes on a real three-node cluster."""
from uuid import uuid4

import pytest

from app.repositories.qdrant.store import QdrantStore, Scope
from tests.cluster.cluster_support import (
    a_memory,
    wait_for_active_replicas,
    wait_until_the_nodes_agree,
)

pytestmark = pytest.mark.cluster


async def test_the_collections_have_the_cluster_layout_and_every_replica_is_active(cluster, cluster_stack):
    live = await cluster_stack.schema.live_generation()
    links = cluster_stack.schema.names.links

    for name in (live, links):
        params = (await cluster_stack.pool.run(lambda client, name=name: client.get_collection(name))).config.params
        assert (params.shard_number, params.replication_factor, params.write_consistency_factor) == (3, 3, 2)
        wait_for_active_replicas(cluster, name)

    await cluster_stack.schema.ensure()  # a second start finds the layout it wants and changes nothing


async def test_a_memory_written_through_the_pool_ends_up_on_every_node(cluster, cluster_stack, repo):
    tenant = uuid4()
    created = [await repo.create_memory(tenant, a_memory(index)) for index in range(6)]
    await repo.create_links_batch(tenant, created[0].id, [created[1].id, created[2].id])

    report = await wait_until_the_nodes_agree(cluster_stack)

    assert report.consistent, report
    assert len({node.points for node in report.nodes}) == 1
    assert [node.error for node in report.nodes] == [None, None, None]
    for index in range(len(cluster.urls)):
        node = QdrantStore(cluster_stack.pool.only(index))
        held = await node.get(Scope.of(tenant), repo.memories, [memory.id for memory in created])
        assert sorted(point.id for point in held) == sorted(memory.id for memory in created)
