# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The restore side of the snapshot adapter, for the answers a real node does not give on cue.

The manifest of a backup set is input: a restore reads it before it uploads anything, and refuses one that names
a file outside the set or a collection that is not this deployment's.
"""
import hashlib
import json
import logging
import os
from pathlib import Path

import httpx
import pytest

from app.config.settings import settings
from app.exceptions import OperatorCommandError
from app.repositories.qdrant import backup as backup_module
from app.repositories.qdrant.backup import MANIFEST, QdrantBackupAdapter
from app.repositories.qdrant.client import SNAPSHOT_TIMEOUT_SECONDS
from app.repositories.qdrant.schema import CollectionNames

NAMES = CollectionNames("axmem")
MEMORIES = f"{NAMES.live}_20260101t000000abcd"
SNAPSHOT = b"snapshot bytes"


class RecordingPool:
    """A pool whose nodes answer every upload with ``status``, report the replicas they hold, and remember what they were asked."""

    def __init__(self, status: int = 400, nodes: int = 1, accepted: int = 0) -> None:
        self.urls = [f"http://node-{number}" for number in range(1, nodes + 1)]
        self.status = status
        self.accepted = accepted  # how many uploads are answered 200 before the rest get ``status``
        self.uploads: list[str] = []
        self.requests: list[tuple[str, str, dict[str, str], float | None]] = []  # node, path, query, timeout
        self.events: list[str] = []  # "upload" and "poll", in the order the nodes saw them
        self.replica_states: list[list[str]] = []  # what the next polls report; the last one is repeated, then all Active
        self.report_status = 200
        self.stuck_until_uploads = 0  # a replica stays in ManualRecovery until this many uploads were made
        self.refusal: dict = {"json": {"status": {"error": "bad snapshot"}}}  # the body of a refused upload

    def http(self, url: str, timeout: float | None = None) -> httpx.AsyncClient:
        def answer(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                self.events.append("poll")
                if self.report_status != 200:
                    return httpx.Response(self.report_status, json={})
                if len(self.uploads) < self.stuck_until_uploads:
                    return httpx.Response(200, json={"result": {"local_shards": [{"state": "ManualRecovery"}], "remote_shards": []}})
                states = self.replica_states.pop(0) if len(self.replica_states) > 1 else (self.replica_states or [["Active"]])[0]
                return httpx.Response(200, json={"result": {"local_shards": [{"state": state} for state in states], "remote_shards": []}})
            self.events.append("upload")
            self.uploads.append(request.url.path)
            self.requests.append((url, request.url.path, dict(request.url.params), timeout))
            if len(self.uploads) <= self.accepted:
                return httpx.Response(200, json={"result": True})
            return httpx.Response(self.status, **self.refusal)

        return httpx.AsyncClient(base_url=url, transport=httpx.MockTransport(answer))


class RecordingSchema:
    """What a restore asks of the schema: the names, and the one memory collection to make live at the end."""

    names = NAMES

    def __init__(self, pool: RecordingPool) -> None:
        self.pool = pool
        self.made_live: list[str] = []
        self.discarded: list[str] = []

    async def make_live(self, generation: str) -> None:
        assert len(self.pool.uploads) >= 2 * len(self.pool.urls), "the collection goes live only when every file is in"
        self.made_live.append(generation)

    async def discard(self, collection: str) -> None:
        assert self.made_live, "the staging collection of an unfinished re-embed goes when the restored data is live"
        self.discarded.append(collection)


@pytest.fixture(autouse=True)
def quick_polls(monkeypatch):
    monkeypatch.setattr(backup_module, "REPLICA_POLL_SECONDS", 0)


def _entry(role: str, collection: str, node: int = 1, **changes) -> dict:
    return {
        "node": node, "role": role, "collection": collection, "file": f"node{node}-{role}.snapshot",
        "sha256": hashlib.sha256(SNAPSHOT).hexdigest(), **changes,
    }


def _entries(nodes: int, memories: str = MEMORIES) -> list[dict]:
    return [
        _entry(role, collection, node)
        for node in range(1, nodes + 1)
        for role, collection in (("memories", memories), ("links", NAMES.links))
    ]


def _backup(tmp_path, manifest, *, nodes=1):
    backup = tmp_path / "axmem-backup-test"
    backup.mkdir()
    for node in range(1, nodes + 1):
        for role in ("memories", "links"):
            (backup / f"node{node}-{role}.snapshot").write_bytes(SNAPSHOT)
    text = manifest if isinstance(manifest, str) else json.dumps(manifest)
    (backup / MANIFEST).write_text(text, encoding="utf-8")
    return backup


def _adapter(pool):
    return QdrantBackupAdapter(pool, schema=RecordingSchema(pool))


async def test_a_snapshot_that_qdrant_refuses_is_reported_by_name_and_status(tmp_path):
    pool = RecordingPool()
    backup = _backup(tmp_path, {"files": [_entry("memories", MEMORIES), _entry("links", NAMES.links)]})

    with pytest.raises(OperatorCommandError, match=r"Qdrant refused the snapshot node1-memories\.snapshot: HTTP 400 \(bad snapshot\)$"):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == [f"/collections/{MEMORIES}/snapshots/upload"]


@pytest.mark.parametrize(
    "refusal",
    [
        {"content": b"<html>bad gateway</html>"},  # not JSON
        {"json": {"status": {}}},  # no error in it
        {"json": ["not", "an", "object"]},  # not the shape Qdrant answers in
        {"json": {"status": {"error": ""}}},  # nothing said
    ],
    ids=["not-json", "no-error", "not-an-object", "empty-error"],
)
async def test_a_refusal_without_a_readable_reason_is_reported_by_name_and_status_only(tmp_path, refusal):
    pool = RecordingPool(status=502)
    pool.refusal = refusal
    backup = _backup(tmp_path, {"files": [_entry("memories", MEMORIES), _entry("links", NAMES.links)]})

    with pytest.raises(OperatorCommandError, match=r"Qdrant refused the snapshot node1-memories\.snapshot: HTTP 502$"):
        await _adapter(pool).restore_backup(backup)


async def test_the_reason_qdrant_gives_for_a_refusal_is_cut_to_a_line(tmp_path):
    pool = RecordingPool(status=500)
    pool.refusal = {"json": {"status": {"error": "x" * 5000}}}
    backup = _backup(tmp_path, {"files": [_entry("memories", MEMORIES), _entry("links", NAMES.links)]})

    with pytest.raises(OperatorCommandError) as raised:
        await _adapter(pool).restore_backup(backup)

    assert str(raised.value).endswith(f"HTTP 500 ({'x' * 300})")


@pytest.mark.parametrize(
    ("manifest", "complaint"),
    [
        ("{not json", "is not a valid manifest"),
        pytest.param("[" * 200_000, "is not a valid manifest", id="nested-deeper-than-the-parser-goes"),
        ("[]", "no list of files"),
        ('{"files": {"a": 1}}', "no list of files"),
        ('{"files": ["node1-links.snapshot"]}', "does not name its node"),
        ('{"files": [{"role": "links"}]}', "does not name its node"),
        ('{"files": [{"node": true, "role": "links"}]}', "does not name its node"),
        ('{"files": [{"node": 1, "role": "links", "collection": "axmem_links", "file": 7, "sha256": "x"}]}', "lacks its role"),
        ('{"files": []}', "exactly one memories and one links"),
    ],
)
async def test_a_manifest_that_is_not_one_is_refused_before_anything_is_uploaded(tmp_path, manifest, complaint):
    pool = RecordingPool(status=200)
    backup = _backup(tmp_path, manifest)

    with pytest.raises(ValueError, match=complaint):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == []


async def test_a_manifest_that_is_not_utf8_is_refused(tmp_path):
    pool = RecordingPool(status=200)
    backup = _backup(tmp_path, "{}")
    (backup / MANIFEST).write_bytes(b'{"files": "\xff\xfe"}')

    with pytest.raises(ValueError, match="is not a valid manifest"):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == []


async def test_every_nodes_files_go_back_to_that_node_and_the_collection_goes_live_after_the_last_one(tmp_path):
    """With a replication factor below the number of nodes a node's snapshot holds only some of the shards."""
    pool = RecordingPool(status=200, nodes=3)
    backup = _backup(tmp_path, {"files": _entries(3)}, nodes=3)
    schema = RecordingSchema(pool)

    await QdrantBackupAdapter(pool, schema=schema).restore_backup(backup)

    upload = {"priority": "snapshot", "wait": "true"}
    assert pool.requests == [
        (f"http://node-{node}", f"/collections/{collection}/snapshots/upload", upload, SNAPSHOT_TIMEOUT_SECONDS)
        for node in (1, 2, 3)
        for collection in (MEMORIES, NAMES.links)
    ]
    assert schema.made_live == [MEMORIES]
    # The snapshot is the source of truth: links an unfinished re-embed left in staging are not the restored ones
    assert schema.discarded == [NAMES.links_staging]


async def test_a_refusal_after_some_files_were_restored_says_how_many_and_that_the_restore_can_be_repeated(tmp_path):
    pool = RecordingPool(status=503, nodes=2, accepted=3)
    backup = _backup(tmp_path, {"files": _entries(2)}, nodes=2)
    schema = RecordingSchema(pool)

    with pytest.raises(
        OperatorCommandError,
        match=r"snapshot node2-links\.snapshot: HTTP 503 \(bad snapshot\)\. 3 of 4 files were already restored: .*run the restore again",
    ):
        await QdrantBackupAdapter(pool, schema=schema).restore_backup(backup)

    assert schema.made_live == []  # the collection does not go live on a half restore


async def test_the_next_file_goes_up_only_when_the_replicas_of_the_last_one_are_active(tmp_path):
    pool = RecordingPool(status=200, nodes=1)
    pool.replica_states = [["Active", "Dead"], ["Recovery", "Active"], ["Active", "Active"]]
    backup = _backup(tmp_path, {"files": _entries(1)})

    await _adapter(pool).restore_backup(backup)

    assert pool.events == ["upload", "poll", "poll", "poll", "upload", "poll"]


async def test_a_replica_that_stays_in_manual_recovery_is_activated_by_uploading_its_file_again(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_module, "REPLICA_RETRY_SECONDS", 0.05)
    pool = RecordingPool(status=200)
    pool.stuck_until_uploads = 2  # the first upload leaves a replica in ManualRecovery, the second one does not
    backup = _backup(tmp_path, {"files": _entries(1)})
    schema = RecordingSchema(pool)

    await QdrantBackupAdapter(pool, schema=schema).restore_backup(backup)

    assert [path.split("/")[2] for path in pool.uploads] == [MEMORIES, MEMORIES, NAMES.links]
    assert schema.made_live == [MEMORIES]


async def test_a_restore_that_leaves_replicas_inactive_stops_with_the_states_and_uploads_nothing_more(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_module, "REPLICA_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(backup_module, "REPLICA_SETTLE_SECONDS", 0.05)
    pool = RecordingPool(status=200, nodes=2)
    pool.replica_states = [["Active", "Dead", "Dead"]]
    backup = _backup(tmp_path, {"files": _entries(2)}, nodes=2)
    schema = RecordingSchema(pool)

    with pytest.raises(
        OperatorCommandError,
        match=r"did not all become Active within 0 s of the restore of node1-memories\.snapshot \(states: Active, Dead, Dead\)",
    ) as refused:
        await QdrantBackupAdapter(pool, schema=schema).restore_backup(backup)

    assert "files were already restored" not in str(refused.value)  # the first file is the one that did not settle
    assert [path.split("/")[2] for path in pool.uploads] == [MEMORIES, MEMORIES]  # once more, and then no further file
    assert schema.made_live == []


async def test_a_later_file_that_leaves_replicas_inactive_says_how_many_files_were_in(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_module, "REPLICA_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(backup_module, "REPLICA_SETTLE_SECONDS", 0.05)
    pool = RecordingPool(status=200, nodes=2)
    pool.replica_states = [["Active"], ["Active"], []]  # the third file's collection reports no replica at all
    backup = _backup(tmp_path, {"files": _entries(2)}, nodes=2)

    with pytest.raises(
        OperatorCommandError, match=r"states: none reported\)\. .*run the restore again\. 2 of 4 files were already restored",
    ):
        await _adapter(pool).restore_backup(backup)


async def test_a_node_that_will_not_report_its_replicas_stops_the_restore(tmp_path):
    pool = RecordingPool(status=200)
    pool.report_status = 500
    backup = _backup(tmp_path, {"files": _entries(1)})

    with pytest.raises(
        OperatorCommandError, match=r"Qdrant at http://node-1 would not report the replicas of axmem_memories_\w+: HTTP 500",
    ):
        await _adapter(pool).restore_backup(backup)


async def test_the_entries_of_a_backup_are_restored_in_node_order_whatever_the_order_of_the_manifest(tmp_path):
    pool = RecordingPool(status=200, nodes=2)
    backup = _backup(tmp_path, {"files": list(reversed(_entries(2)))}, nodes=2)

    await _adapter(pool).restore_backup(backup)

    assert [(node, path.split("/")[2]) for node, path, _query, _timeout in pool.requests] == [
        ("http://node-1", MEMORIES), ("http://node-1", NAMES.links),
        ("http://node-2", MEMORIES), ("http://node-2", NAMES.links),
    ]


@pytest.mark.parametrize(
    ("nodes", "backup_nodes", "complaint"),
    [
        (2, 1, "node 2 needs exactly one memories and one links snapshot"),
        (1, 2, "it holds snapshots of node 2 but QDRANT_URL lists 1 nodes"),
        (3, 2, "node 3 needs exactly one memories and one links snapshot"),
    ],
)
async def test_a_backup_of_other_nodes_than_the_configured_ones_is_refused_before_anything_is_uploaded(
    tmp_path, nodes, backup_nodes, complaint,
):
    pool = RecordingPool(status=200, nodes=nodes)
    backup = _backup(tmp_path, {"files": _entries(backup_nodes)}, nodes=backup_nodes)

    with pytest.raises(ValueError, match=complaint):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == []


async def test_a_node_with_two_snapshots_of_one_kind_is_refused(tmp_path):
    pool = RecordingPool(status=200, nodes=2)
    entries = [*_entries(2), _entry("memories", MEMORIES, node=2)]
    backup = _backup(tmp_path, {"files": entries}, nodes=2)

    with pytest.raises(ValueError, match="node 2 needs exactly one memories and one links snapshot"):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == []


async def test_memory_snapshots_of_different_generations_on_different_nodes_are_refused(tmp_path):
    pool = RecordingPool(status=200, nodes=2)
    entries = _entries(2)
    entries[2]["collection"] = f"{NAMES.live}_20260202t000000ffff"
    backup = _backup(tmp_path, {"files": entries}, nodes=2)

    with pytest.raises(ValueError, match="the memories snapshots of the nodes are of different collections"):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == []


async def test_a_file_that_is_another_nodes_snapshot_than_the_entry_says_is_refused(tmp_path):
    pool = RecordingPool(status=200, nodes=2)
    entries = _entries(2)
    entries[2]["file"] = "node1-memories.snapshot"
    backup = _backup(tmp_path, {"files": entries}, nodes=2)

    with pytest.raises(ValueError, match=r"'node1-memories\.snapshot' is not the memories snapshot of node 2"):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == []


async def test_a_backup_of_the_configured_nodes_in_another_order_is_refused_before_anything_is_uploaded(tmp_path):
    pool = RecordingPool(status=200, nodes=3)
    entries = [{**entry, "url": f"http://node-{(entry['node'] % 3) + 1}/"} for entry in _entries(3)]  # 1 -> 2, 2 -> 3, 3 -> 1
    backup = _backup(tmp_path, {"files": entries}, nodes=3)

    with pytest.raises(ValueError, match=r"QDRANT_URL lists the nodes in another order than the backup was taken from \(http://node-2"):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == []


async def test_a_backup_whose_nodes_have_other_addresses_now_is_restored_node_by_node(tmp_path):
    pool = RecordingPool(status=200, nodes=2)
    entries = [{**entry, "url": f"http://old-host-{entry['node']}:6333"} for entry in _entries(2)]
    backup = _backup(tmp_path, {"files": entries}, nodes=2)

    await _adapter(pool).restore_backup(backup)

    assert [node for node, *_ in pool.requests] == ["http://node-1", "http://node-1", "http://node-2", "http://node-2"]


async def test_a_manifest_without_addresses_is_restored_by_node_number(tmp_path):
    pool = RecordingPool(status=200, nodes=2)
    backup = _backup(tmp_path, {"files": _entries(2)}, nodes=2)  # the manifest of an older backup names no address

    await _adapter(pool).restore_backup(backup)

    assert len(pool.requests) == 4


async def test_a_damaged_snapshot_of_a_later_node_is_found_before_the_first_node_is_touched(tmp_path):
    pool = RecordingPool(status=200, nodes=2)
    backup = _backup(tmp_path, {"files": _entries(2)}, nodes=2)
    (backup / "node2-links.snapshot").write_bytes(b"damaged")

    with pytest.raises(ValueError, match=r"node2-links\.snapshot does not match the checksum"):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == []


def test_a_checksum_is_computed_in_chunks_never_from_the_whole_file(tmp_path, monkeypatch):
    path = tmp_path / "big.snapshot"
    data = os.urandom(5000)
    path.write_bytes(data)
    monkeypatch.setattr(backup_module, "_CHUNK", 1000)
    sizes: list[int] = []
    real_open = Path.open

    class Reading:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.handle.close()

        def read(self, size=-1):
            sizes.append(size)
            return self.handle.read(size)

    monkeypatch.setattr(Path, "open", lambda self, *args, **kwargs: Reading(real_open(self, *args, **kwargs)))
    monkeypatch.setattr(Path, "read_bytes", lambda _self: pytest.fail("a snapshot must not be read whole"))

    assert backup_module._sha256_of(path) == hashlib.sha256(data).hexdigest()
    assert sizes == [1000] * 6


# ---- a backup leaves no snapshot on the node ---------------------------------------------------------------------------


class SnapshotPool:
    """Nodes that take snapshots and serve them as ``body``, answering the download with ``status``; the deletions are recorded."""

    def __init__(self, status: int = 200, body: bytes = SNAPSHOT, nodes: int = 2, delete_fails: bool = False) -> None:
        self.urls = [f"http://node-{number}" for number in range(1, nodes + 1)]
        self.status = status
        self.body = body
        self.delete_fails = delete_fails
        self.created: list[tuple[int, str]] = []
        self.deleted: list[tuple[int, str, str]] = []

    async def create_snapshot(self, index: int, collection: str) -> str:
        self.created.append((index, collection))
        return f"{collection}-{index}.snapshot"

    async def delete_snapshot(self, index: int, collection: str, snapshot: str) -> None:
        self.deleted.append((index, collection, snapshot))
        if self.delete_fails:
            raise httpx.ConnectError("node went away")

    def http(self, url: str, timeout: float | None = None) -> httpx.AsyncClient:
        def answer(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(self.status, content=self.body)

        return httpx.AsyncClient(base_url=url, transport=httpx.MockTransport(answer))


class LiveSchema:
    names = NAMES

    async def live_generation(self) -> str:
        return MEMORIES


@pytest.fixture
def backups(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    return tmp_path / "backups"


async def test_a_backup_records_the_node_of_every_file_and_deletes_every_snapshot_from_its_node(backups):
    pool = SnapshotPool()

    result = await QdrantBackupAdapter(pool, schema=LiveSchema()).create_backup("20260101t000000")

    manifest = json.loads((result.directory / MANIFEST).read_text(encoding="utf-8"))
    assert [(entry["node"], entry["role"], entry["url"]) for entry in manifest["files"]] == [
        (1, "memories", "http://node-1"), (1, "links", "http://node-1"),
        (2, "memories", "http://node-2"), (2, "links", "http://node-2"),
    ]
    assert sorted(pool.deleted) == sorted((index, collection, f"{collection}-{index}.snapshot") for index, collection in pool.created)
    assert len(pool.deleted) == 4


async def test_a_download_that_fails_still_deletes_the_snapshot_that_was_taken_and_leaves_no_directory(backups):
    pool = SnapshotPool(status=404)

    with pytest.raises(httpx.HTTPStatusError):
        await QdrantBackupAdapter(pool, schema=LiveSchema()).create_backup("20260101t000000")

    assert pool.deleted == [(0, MEMORIES, f"{MEMORIES}-0.snapshot")]
    assert list(backups.iterdir()) == []


async def test_a_snapshot_that_cannot_be_deleted_is_named_in_the_log_and_does_not_fail_the_backup(backups, caplog):
    pool = SnapshotPool(nodes=1, delete_fails=True)
    caplog.set_level(logging.ERROR, logger=backup_module.logger.name)

    result = await QdrantBackupAdapter(pool, schema=LiveSchema()).create_backup("20260101t000000")

    assert len(result.files) == 2
    records = [record for record in caplog.records if "could not be deleted from its node" in record.getMessage()]
    assert [(record.node, record.collection, record.snapshot) for record in records] == [
        ("http://node-1", MEMORIES, f"{MEMORIES}-0.snapshot"), ("http://node-1", NAMES.links, f"{NAMES.links}-0.snapshot"),
    ]


@pytest.mark.parametrize(
    "file",
    [
        "../outside.snapshot", "../../etc/passwd", "sub/node1-links.snapshot", "/etc/passwd", "node1-links.snapshot/..",
        "..", "node1-links.snap", "node1-other.snapshot",
    ],
)
async def test_a_manifest_that_names_a_file_outside_the_set_is_refused_before_anything_is_uploaded(tmp_path, file):
    pool = RecordingPool(status=200)
    entries = [_entry("memories", MEMORIES), _entry("links", NAMES.links, file=file)]
    backup = _backup(tmp_path, {"files": entries})

    with pytest.raises(ValueError, match="not a snapshot of this backup set"):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == []


@pytest.mark.parametrize(
    ("role", "collection"),
    [
        ("links", "other_links"),
        ("links", "axmem_memories"),
        ("memories", "other_memories_20260101t000000abcd"),
        ("memories", NAMES.live),  # the live alias itself, which must never be a restore target
        ("memories", f"{MEMORIES}-extra"),
        ("memories", f"{NAMES.live}_20260101t000000abcd/../x"),
        ("tokens", "axmem_tokens"),
    ],
)
async def test_a_manifest_that_names_a_collection_of_another_deployment_is_refused(tmp_path, role, collection):
    pool = RecordingPool(status=200)
    entries = [_entry("memories", MEMORIES), _entry("links", NAMES.links)]
    entries[0 if role == "memories" else 1] = _entry(
        role, collection, file="node1-memories.snapshot" if role == "memories" else "node1-links.snapshot",
    )
    backup = _backup(tmp_path, {"files": entries})

    with pytest.raises(ValueError, match="does not belong to this deployment"):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == []


async def test_a_damaged_snapshot_is_refused_before_anything_is_uploaded(tmp_path):
    pool = RecordingPool(status=200)
    backup = _backup(tmp_path, {"files": [_entry("memories", MEMORIES), _entry("links", NAMES.links)]})
    (backup / "node1-links.snapshot").write_bytes(b"damaged")

    with pytest.raises(ValueError, match=r"node1-links\.snapshot does not match the checksum"):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == []


async def test_a_directory_without_a_manifest_is_not_a_backup(tmp_path):
    with pytest.raises(FileNotFoundError, match="Backup not found"):
        await _adapter(RecordingPool()).restore_backup(tmp_path)
