"""A snapshot request that fails leaves no snapshot on the node, even one the node finishes after the client gave up.

The snapshot call waits for the node under a long timeout of its own. A request can still end without a name: the
timeout, a connection that drops, an error after the snapshot was made, an answer that holds no name. The node keeps
(or goes on making) the snapshot, unnamed, and it is as large as the node's shards. The pool lists the snapshots of the
collection before it asks and deletes the new ones after a failure. A real node makes a small snapshot in milliseconds,
so the far end here is an in-process HTTP server that makes it when the test says (tests/qdrant_fake_node.py).
"""
import asyncio
import logging
import time

import httpx
import pytest

from app.repositories.qdrant import client as client_module
from app.repositories.qdrant.client import NodePool
from tests.qdrant_fake_node import FakeQdrantNode

COLLECTION = "c"


@pytest.fixture
def node(monkeypatch, caplog):
    """A node to take snapshots on; the client gives up on a request after 0.3 s and looks for a late snapshot every 0.1 s."""
    monkeypatch.setattr(client_module, "SNAPSHOT_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(client_module, "SNAPSHOT_CLEANUP_POLL_SECONDS", 0.1)
    monkeypatch.setattr(client_module, "SNAPSHOT_CLEANUP_SECONDS", 20.0)
    caplog.set_level(logging.WARNING, logger=client_module.logger.name)
    with FakeQdrantNode() as fake:
        yield fake


@pytest.fixture
async def pool(node):
    pool = NodePool([node.url], api_key="", timeout=0.2)
    yield pool
    await pool.close()


def warnings_of(caplog, text: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if text in record.getMessage()]


async def test_a_snapshot_that_succeeds_is_named_and_left_alone(node, pool):
    node.snapshots = {COLLECTION: ["older.snapshot"]}

    name = await pool.create_snapshot(0, COLLECTION)

    assert name == f"{COLLECTION}-1.snapshot"
    assert node.snapshots == {COLLECTION: ["older.snapshot", name]}
    assert node.deleted == []


async def test_a_snapshot_the_node_finishes_after_the_client_gave_up_is_found_and_deleted(node, pool):
    node.snapshots = {COLLECTION: ["older.snapshot"]}
    node.create_delay = 1.0  # three times what the client waits

    with pytest.raises(httpx.ReadTimeout):
        await pool.create_snapshot(0, COLLECTION)

    assert node.snapshots == {COLLECTION: ["older.snapshot"]}  # the one that was there before is not touched
    assert node.deleted == [(COLLECTION, f"{COLLECTION}-1.snapshot")]


async def test_a_snapshot_that_does_not_appear_while_the_pool_looks_is_named_to_the_operator(node, pool, monkeypatch, caplog):
    monkeypatch.setattr(client_module, "SNAPSHOT_CLEANUP_SECONDS", 0.4)
    node.snapshots = {COLLECTION: ["older.snapshot"]}
    node.create_delay = 60.0  # released when the test ends

    with pytest.raises(httpx.ReadTimeout):
        await pool.create_snapshot(0, COLLECTION)

    [warning] = warnings_of(caplog, "may still be making a snapshot")
    assert (warning.collection, warning.before) == (COLLECTION, ["older.snapshot"])
    assert node.deleted == []


async def test_a_snapshot_made_before_an_error_answer_is_deleted_at_once(node, pool, caplog):
    node.create_status = 500
    started = time.monotonic()

    with pytest.raises(httpx.HTTPStatusError):
        await pool.create_snapshot(0, COLLECTION)

    assert node.deleted == [(COLLECTION, f"{COLLECTION}-1.snapshot")]
    assert node.snapshots == {COLLECTION: []}
    assert time.monotonic() - started < 5
    assert warnings_of(caplog, "Deleted the snapshot a failed request left")


async def test_a_refusal_that_made_no_snapshot_is_not_waited_on(node, pool, caplog):
    node.create_status = 404
    node.made_before_error = False
    started = time.monotonic()

    with pytest.raises(httpx.HTTPStatusError):
        await pool.create_snapshot(0, COLLECTION)

    assert time.monotonic() - started < 5  # the node answered: it is not still making one
    assert node.deleted == [] and not warnings_of(caplog, "may still be making")


async def test_an_answer_without_a_name_does_not_leave_the_snapshot_behind(node, pool):
    node.malformed = True

    with pytest.raises(KeyError):
        await pool.create_snapshot(0, COLLECTION)

    assert node.deleted == [(COLLECTION, f"{COLLECTION}-1.snapshot")]


async def test_a_cancelled_snapshot_request_looks_once_and_does_not_wait(node, pool, caplog):
    node.create_delay = 60.0
    task = asyncio.create_task(pool.create_snapshot(0, COLLECTION))
    # A task that has already ended without sending the request would keep this loop spinning for ever
    while ("POST", f"/collections/{COLLECTION}/snapshots") not in node.requests and not task.done():
        await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert node.listings == 2  # before the request, and once after the cancellation
    assert not warnings_of(caplog, "may still be making")


async def test_a_node_that_will_not_list_its_snapshots_after_the_failure_leaves_the_original_error(node, pool, caplog):
    node.create_status = 500
    node.list_fails_after = 1

    with pytest.raises(httpx.HTTPStatusError):
        await pool.create_snapshot(0, COLLECTION)

    assert warnings_of(caplog, "could not be listed after a failed snapshot")
    assert node.deleted == []


async def test_a_node_that_will_not_list_its_snapshots_before_is_asked_for_the_snapshot_all_the_same(node, pool, caplog):
    node.list_fails_after = 0

    name = await pool.create_snapshot(0, COLLECTION)

    assert name == f"{COLLECTION}-1.snapshot"
    assert warnings_of(caplog, "could not be listed before taking one")


async def test_a_failure_without_a_list_to_compare_with_deletes_nothing(node, pool):
    node.list_fails_after = 0
    node.create_status = 500

    with pytest.raises(httpx.HTTPStatusError):
        await pool.create_snapshot(0, COLLECTION)

    assert node.deleted == []


async def test_a_snapshot_that_cannot_be_deleted_is_named_and_the_original_error_surfaces(node, pool, caplog):
    node.create_status = 500
    node.delete_fails = True

    with pytest.raises(httpx.HTTPStatusError):
        await pool.create_snapshot(0, COLLECTION)

    [failure] = warnings_of(caplog, "could not be deleted")
    assert (failure.collection, failure.snapshot) == (COLLECTION, f"{COLLECTION}-1.snapshot")
