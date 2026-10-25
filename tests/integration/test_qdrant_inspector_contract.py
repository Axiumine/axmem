"""What the cluster check asks of each node, reports about it and logs, with the nodes played by views of one real node.

``test_qdrant_inspector.py`` shows that nodes holding the same points agree and that a missing or newer point is named.
This file pins the rest of what an operator reads: the fields the check pulls from a node (never a whole memory), the
address and the kind of failure of a node it could not read, the order in which it names the points that differ, and
the line it logs for a node it could not read.
"""
import logging
from uuid import uuid4

import pytest

from app.models.memory_models import MemoryCreate
from app.repositories.qdrant import client as client_module
from app.repositories.qdrant import inspector as inspector_module
from app.repositories.qdrant.client import NodePool
from app.repositories.qdrant.inspector import QdrantClusterInspector
from app.repositories.qdrant.store import TOKEN_KEY, VERSION_KEY

DEAD_NODE = "http://127.0.0.1:1"


class ScrollRecorder:
    """A node that remembers which collection each scroll read and which payload fields it asked for."""

    def __init__(self, client, hidden=()):
        self._client = client
        self.hidden = {str(point_id) for point_id in hidden}
        self.scrolls: list[tuple[str, object]] = []

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def scroll(self, collection, **kwargs):
        self.scrolls.append((collection, kwargs["with_payload"]))
        records, offset = await self._client.scroll(collection, **kwargs)
        return [record for record in records if str(record.id) not in self.hidden], offset


def memory(title: str) -> MemoryCreate:
    return MemoryCreate(title=title, content=f"Content of {title}", context="Inspector", keywords=["inspector"], tags=["contract"])


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(client_module, "RETRY_BACKOFF_SECONDS", 0)


@pytest.fixture
async def repo(isolated_stack, tensorrt_embedder):
    return isolated_stack.memory_repository(tensorrt_embedder)


def inspector(stack, *urls: str) -> tuple[QdrantClusterInspector, NodePool]:
    pool = NodePool(urls=list(urls))
    return QdrantClusterInspector(pool, stack.schema), pool


async def test_each_node_is_asked_only_for_the_fields_that_are_compared(isolated_stack, repo):
    user_id = uuid4()
    first = await repo.create_memory(user_id, memory("First"))
    second = await repo.create_memory(user_id, memory("Second"))
    await repo.create_links_batch(user_id, first.id, [second.id])
    check, pool = inspector(isolated_stack, isolated_stack.pool.urls[0], isolated_stack.pool.urls[0])
    recorders = [ScrollRecorder(client) for client in pool.clients]
    pool.clients[:] = recorders
    live = await repo.live_generation()
    try:
        report = await check.check()
    finally:
        await pool.close()

    assert report.consistent
    for recorder in recorders:
        assert recorder.scrolls == [
            (live, [VERSION_KEY, TOKEN_KEY, "is_obsolete"]),
            (isolated_stack.schema.names.links, [TOKEN_KEY]),
        ]


async def test_every_node_is_reported_with_its_own_address_and_a_node_in_the_middle_that_fails_does_not_stop_the_check(
    isolated_stack, repo,
):
    await repo.create_memory(uuid4(), memory("Only"))
    live_url = isolated_stack.pool.urls[0]
    check, pool = inspector(isolated_stack, live_url, DEAD_NODE, live_url)
    try:
        report = await check.check()
    finally:
        await pool.close()

    assert [(node.url, node.points, node.error) for node in report.nodes] == [
        (live_url, 1, None),
        (DEAD_NODE, None, "ResponseHandlingException"),
        (live_url, 1, None),
    ]
    assert report.divergent == []
    assert not report.consistent


async def test_a_node_that_cannot_be_read_is_logged_with_its_address_and_the_kind_of_failure(isolated_stack, repo, caplog):
    caplog.set_level(logging.WARNING, logger=inspector_module.logger.name)
    check, pool = inspector(isolated_stack, isolated_stack.pool.urls[0], DEAD_NODE)
    try:
        await check.check()
    finally:
        await pool.close()

    [warning] = [record for record in caplog.records if record.getMessage() == "A Qdrant node could not be read"]
    assert (warning.node, warning.error_type, warning.levelno) == (DEAD_NODE, "ResponseHandlingException", logging.WARNING)


async def test_the_points_that_differ_are_named_in_order_of_collection_and_then_of_id(isolated_stack, repo):
    user_id = uuid4()
    memories = [
        await repo.create_memory(user_id, memory(f"Memory {number}"))
        for number in range(8)
    ]
    live_url = isolated_stack.pool.urls[0]
    check, pool = inspector(isolated_stack, live_url, live_url)
    pool.clients[1] = ScrollRecorder(pool.clients[1], hidden=[memory.id for memory in memories])
    live = await repo.live_generation()
    try:
        report = await check.check()
    finally:
        await pool.close()

    assert report.divergent == [f"{live}:{point_id}" for point_id in sorted(str(memory.id) for memory in memories)]
    assert [node.points for node in report.nodes] == [8, 0]
