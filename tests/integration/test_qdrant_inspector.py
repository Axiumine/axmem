"""The cluster check, with the nodes played by one real Qdrant node and skewed views of it.

A cluster test cannot make three real replicas disagree on cue, so a "node" here is a client of the one test
node, optionally wrapped so that its reads leave out a point or report another version of it. That is exactly
what the check has to notice: it reads each node on its own and compares the points by version, claim token and
obsolete flag.
"""
import pytest

from app.repositories.qdrant import client as client_module
from app.repositories.qdrant.client import NodePool
from app.repositories.qdrant.inspector import QdrantClusterInspector
from app.repositories.qdrant.store import SYSTEM, VERSION_KEY
from tests.backup_restore_cases import seed_memories

DEAD_NODE = "http://127.0.0.1:1"


class SkewedClient:
    """A node whose scrolls leave out the points in ``hide`` and report one more version for those in ``bump``."""

    def __init__(self, client, hide=(), bump=()):
        self._client = client
        self.hide = {str(point_id) for point_id in hide}
        self.bump = {str(point_id) for point_id in bump}

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def scroll(self, collection, **kwargs):
        records, offset = await self._client.scroll(collection, **kwargs)
        kept = []
        for record in records:
            if str(record.id) in self.hide:
                continue
            if str(record.id) in self.bump:
                record.payload[VERSION_KEY] = int(record.payload[VERSION_KEY]) + 1
            kept.append(record)
        return kept, offset


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(client_module, "RETRY_BACKOFF_SECONDS", 0)


@pytest.fixture
async def seeded(isolated_stack, tensorrt_embedder):
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    user_id, originals = await seed_memories(repo)
    return isolated_stack, repo, user_id, originals


def inspector(stack, *, nodes: int, dead: int = 0) -> tuple[QdrantClusterInspector, NodePool]:
    """An inspector over ``nodes`` views of the test node, then ``dead`` addresses nothing listens on."""
    pool = NodePool(urls=[stack.pool.urls[0]] * nodes + [DEAD_NODE] * dead)
    return QdrantClusterInspector(pool, stack.schema), pool


async def test_nodes_that_hold_the_same_points_agree(seeded):
    stack, *_ = seeded
    check, pool = inspector(stack, nodes=3)
    try:
        report = await check.check()
    finally:
        await pool.close()

    assert report.consistent
    assert [node.points for node in report.nodes] == [4, 4, 4]  # three memories and one link
    assert report.divergent == []


async def test_a_node_that_lacks_a_point_or_holds_another_version_of_it_is_named(seeded):
    stack, repo, user_id, originals = seeded
    check, pool = inspector(stack, nodes=3)
    [link_id] = [point.id async for point in stack.store.iterate(SYSTEM, stack.schema.names.links)]
    pool.clients[1] = SkewedClient(pool.clients[1], hide=[originals[0].id, link_id])
    pool.clients[2] = SkewedClient(pool.clients[2], bump=[originals[1].id])
    try:
        report = await check.check()
    finally:
        await pool.close()

    live = await repo.live_generation()
    assert not report.consistent
    assert set(report.divergent) == {
        f"{live}:{originals[0].id}", f"{live}:{originals[1].id}", f"{stack.schema.names.links}:{link_id}",
    }
    assert report.divergent == sorted(report.divergent)
    assert [node.points for node in report.nodes] == [4, 2, 4]


async def test_a_node_that_cannot_be_read_is_reported_and_the_others_are_still_compared(seeded):
    stack, *_ = seeded
    check, pool = inspector(stack, nodes=2, dead=1)
    try:
        report = await check.check()
    finally:
        await pool.close()

    assert not report.consistent  # an unreadable node is never "agreeing"
    assert report.divergent == []
    assert [(node.points, node.error) for node in report.nodes[:2]] == [(4, None), (4, None)]
    assert report.nodes[2].points is None
    assert report.nodes[2].url == DEAD_NODE
    assert report.nodes[2].error


async def test_when_no_node_can_be_read_nothing_is_compared(seeded):
    stack, *_ = seeded
    check, pool = inspector(stack, nodes=0, dead=2)
    try:
        report = await check.check()
    finally:
        await pool.close()

    assert not report.consistent
    assert report.divergent == []
    assert all(node.error for node in report.nodes)
