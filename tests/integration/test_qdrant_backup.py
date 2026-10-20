"""The restore side of the snapshot adapter, for the answers a real node does not give on cue.

The manifest of a backup set is input: a restore reads it before it uploads anything, and refuses one that names
a file outside the set or a collection that is not this deployment's.
"""
import hashlib
import json

import httpx
import pytest

from app.exceptions import OperatorCommandError
from app.repositories.qdrant.backup import MANIFEST, QdrantBackupAdapter
from app.repositories.qdrant.schema import CollectionNames, QdrantSchema

NAMES = CollectionNames("axmem")
MEMORIES = f"{NAMES.live}_20260101t000000abcd"
SNAPSHOT = b"snapshot bytes"


class RecordingPool:
    """A pool whose node answers every upload with ``status`` and remembers what it was asked for."""

    urls = ["http://node-1"]

    def __init__(self, status: int = 400) -> None:
        self.status = status
        self.uploads: list[str] = []

    def http(self, url: str) -> httpx.AsyncClient:
        def answer(request: httpx.Request) -> httpx.Response:
            self.uploads.append(request.url.path)
            return httpx.Response(self.status, json={"status": {"error": "bad snapshot"}})

        return httpx.AsyncClient(base_url=url, transport=httpx.MockTransport(answer))


def _entry(role: str, collection: str, **changes) -> dict:
    name = f"node1-{role}.snapshot"
    return {
        "node": 1, "role": role, "collection": collection, "file": name,
        "sha256": hashlib.sha256(SNAPSHOT).hexdigest(), **changes,
    }


def _backup(tmp_path, manifest, *, files=("node1-memories.snapshot", "node1-links.snapshot")):
    backup = tmp_path / "axmem-backup-test"
    backup.mkdir()
    for name in files:
        (backup / name).write_bytes(SNAPSHOT)
    text = manifest if isinstance(manifest, str) else json.dumps(manifest)
    (backup / MANIFEST).write_text(text, encoding="utf-8")
    return backup


def _adapter(pool):
    return QdrantBackupAdapter(pool, schema=QdrantSchema(pool, NAMES, 4))


async def test_a_snapshot_that_qdrant_refuses_is_reported_by_name_and_status(tmp_path):
    pool = RecordingPool()
    backup = _backup(tmp_path, {"files": [_entry("memories", MEMORIES), _entry("links", NAMES.links)]})

    with pytest.raises(OperatorCommandError, match=r"Qdrant refused the snapshot node1-memories\.snapshot: HTTP 400"):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == [f"/collections/{MEMORIES}/snapshots/upload"]


@pytest.mark.parametrize(
    ("manifest", "complaint"),
    [
        ("{not json", "is not a valid manifest"),
        pytest.param("[" * 200_000, "is not a valid manifest", id="nested-deeper-than-the-parser-goes"),
        ("[]", "no list of files"),
        ('{"files": {"a": 1}}', "no list of files"),
        ('{"files": ["node1-links.snapshot"]}', "does not name its node"),
        ('{"files": [{"role": "links"}]}', "does not name its node"),
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


async def test_the_entries_of_other_nodes_are_not_restored(tmp_path):
    """Node 1 holds the data that is restored; the replicas catch up from it, so the other nodes' entries are not read."""
    pool = RecordingPool()  # refuses the first upload, which ends the restore after it chose what to upload
    others = [_entry("memories", "other_memories", node=2, file="../node2-memories.snapshot")]
    backup = _backup(tmp_path, {"files": [*others, _entry("memories", MEMORIES), _entry("links", NAMES.links)]})

    with pytest.raises(OperatorCommandError, match=r"Qdrant refused the snapshot node1-memories\.snapshot"):
        await _adapter(pool).restore_backup(backup)

    assert pool.uploads == [f"/collections/{MEMORIES}/snapshots/upload"]


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
    entries[0 if role == "memories" else 1] = _entry(role, collection, file="node1-memories.snapshot" if role == "memories" else "node1-links.snapshot")
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
