# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The exact files, requests, log lines and refusals of the snapshot backup and restore.

``test_qdrant_backup.py`` shows which manifests are refused and in what order the nodes are restored. This file pins
what an operator and a script can rely on: the layout and permissions of a backup set, the text of its manifest, the
requests a restore sends to each node, the replicas it waits for and the words of every message it logs or raises.
"""
import hashlib
import itertools
import json
import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
BODY = bytes(range(256)) * 12  # 3072 bytes, so that a chunk of 1000 bytes is read four times
TIMESTAMP = "20260101t000000"


class ServingPool:
    """Nodes that take snapshots and serve them as ``BODY``; every download and every deletion is recorded."""

    def __init__(self, nodes: int = 2, status: int = 200, on_download: Callable[[httpx.Request], None] | None = None) -> None:
        self.urls = [f"http://node-{number}" for number in range(1, nodes + 1)]
        self.status = status
        self.on_download = on_download
        self.downloads: list[tuple[str, str, str]] = []  # method, node, path
        self.streams: list[tuple[str, str]] = []  # what the client was asked to stream: the method and the path as given
        self.deleted: list[tuple[int, str, str]] = []

    async def create_snapshot(self, index: int, collection: str) -> str:
        return f"{collection}-{index}.snapshot"

    async def delete_snapshot(self, index: int, collection: str, snapshot: str) -> None:
        self.deleted.append((index, collection, snapshot))

    def http(self, url: str, timeout: float | None = None) -> httpx.AsyncClient:
        def answer(request: httpx.Request) -> httpx.Response:
            self.downloads.append((request.method, request.url.host or "", request.url.path))
            if self.on_download:
                self.on_download(request)
            return httpx.Response(self.status, content=BODY)

        client = httpx.AsyncClient(base_url=url, transport=httpx.MockTransport(answer))
        stream = client.stream

        def recording(method: str, path: str, **kwargs: Any):
            self.streams.append((method, path))
            return stream(method, path, **kwargs)

        client.stream = recording  # type: ignore[method-assign]
        return client


class LiveSchema:
    names = NAMES

    async def live_generation(self) -> str:
        return MEMORIES


def shards(local: list[str], remote: list[str] | None = None) -> dict[str, Any]:
    return {"result": {
        "local_shards": [{"state": state} for state in local],
        "remote_shards": [{"state": state} for state in remote or []],
    }}


class NodesOfARestore:
    """Nodes that accept every upload and report the replicas they hold, recording what they were sent."""

    def __init__(self, nodes: int = 1) -> None:
        self.urls = [f"http://node-{number}" for number in range(1, nodes + 1)]
        self.uploads: list[httpx.Request] = []
        self.upload_bodies: list[bytes] = []
        self.polls: list[tuple[str, str]] = []  # node, path
        self.poll_status = 200
        self.reports: list[dict[str, Any]] = [shards(["Active"])]  # what the nodes say after the first upload, then after the second

    def http(self, url: str, timeout: float | None = None) -> httpx.AsyncClient:
        def answer(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                self.polls.append((request.url.host or "", request.url.path))
                if self.poll_status != 200:
                    return httpx.Response(self.poll_status, json={})
                return httpx.Response(200, json=self.reports[min(len(self.uploads), len(self.reports)) - 1])
            self.uploads.append(request)
            self.upload_bodies.append(request.read())
            return httpx.Response(200, json={"result": True})

        return httpx.AsyncClient(base_url=url, transport=httpx.MockTransport(answer))


class Schema:
    names = NAMES

    async def make_live(self, generation: str) -> None:
        return None

    async def discard(self, collection: str) -> None:
        return None


def entries(nodes: int = 1) -> list[dict[str, Any]]:
    return [
        {
            "node": node, "role": role, "collection": collection, "file": f"node{node}-{role}.snapshot",
            "sha256": hashlib.sha256(BODY).hexdigest(),
        }
        for node in range(1, nodes + 1)
        for role, collection in (("memories", MEMORIES), ("links", NAMES.links))
    ]


def backup_set(tmp_path, files: list[dict[str, Any]]):
    directory = tmp_path / "axmem-backup-test"
    directory.mkdir()
    for entry in files:
        (directory / entry["file"]).write_bytes(BODY)
    (directory / MANIFEST).write_text(json.dumps({"files": files}), encoding="utf-8")
    return directory


def records_of(caplog, text: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == text]


@pytest.fixture
def backups(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    return tmp_path / "backups"


@pytest.fixture(autouse=True)
def quick_polls(monkeypatch):
    monkeypatch.setattr(backup_module, "REPLICA_POLL_SECONDS", 0)


# ---- the set a backup writes --------------------------------------------------------------------------------------------


async def test_a_backup_is_a_private_directory_of_private_snapshot_files_and_a_manifest_that_describes_them(backups, monkeypatch):
    monkeypatch.setattr(backup_module, "_CHUNK", 1000)
    pool = ServingPool(nodes=2)

    result = await QdrantBackupAdapter(pool, schema=LiveSchema()).create_backup(TIMESTAMP)

    directory = backups / f"axmem-backup-{TIMESTAMP}"
    names = ["node1-memories.snapshot", "node1-links.snapshot", "node2-memories.snapshot", "node2-links.snapshot"]
    assert result.directory == directory
    assert result.files == [directory / name for name in names]
    assert sorted(path.name for path in directory.iterdir()) == sorted([*names, MANIFEST])
    assert [(directory / name).read_bytes() for name in names] == [BODY] * 4
    assert directory.stat().st_mode & 0o777 == 0o700
    assert [(directory / name).stat().st_mode & 0o777 for name in (*names, MANIFEST)] == [0o600] * 5
    assert pool.downloads == [
        ("GET", node, f"/collections/{collection}/snapshots/{collection}-{index}.snapshot")
        for index, node in enumerate(("node-1", "node-2"))
        for collection in (MEMORIES, NAMES.links)
    ]


async def test_the_manifest_of_a_backup_names_each_file_by_node_collection_checksum_size_and_address(backups):
    pool = ServingPool(nodes=1)
    digest = hashlib.sha256(BODY).hexdigest()

    await QdrantBackupAdapter(pool, schema=LiveSchema()).create_backup(TIMESTAMP)

    text = (backups / f"axmem-backup-{TIMESTAMP}" / MANIFEST).read_text(encoding="utf-8")
    assert text == f"""{{
  "created": "{TIMESTAMP}",
  "files": [
    {{
      "node": 1,
      "role": "memories",
      "collection": "{MEMORIES}",
      "file": "node1-memories.snapshot",
      "sha256": "{digest}",
      "bytes": {len(BODY)},
      "url": "http://node-1"
    }},
    {{
      "node": 1,
      "role": "links",
      "collection": "{NAMES.links}",
      "file": "node1-links.snapshot",
      "sha256": "{digest}",
      "bytes": {len(BODY)},
      "url": "http://node-1"
    }}
  ]
}}"""


async def test_a_backup_that_was_made_is_logged_with_its_directory_and_the_number_of_files(backups, caplog):
    caplog.set_level(logging.INFO, logger=backup_module.logger.name)

    await QdrantBackupAdapter(ServingPool(nodes=2), schema=LiveSchema()).create_backup(TIMESTAMP)

    [record] = records_of(caplog, "Qdrant backup created")
    assert (record.backup_path, record.files, record.levelno) == (
        str(backups / f"axmem-backup-{TIMESTAMP}"), 4, logging.INFO,
    )


async def test_a_failed_backup_removes_its_directory_without_failing_on_what_it_cannot_remove(backups, monkeypatch):
    removed: list[tuple[Any, dict[str, Any]]] = []
    real = shutil.rmtree

    def recording(path, **kwargs):
        removed.append((path, kwargs))
        real(path, **kwargs)

    monkeypatch.setattr(backup_module.shutil, "rmtree", recording)

    with pytest.raises(httpx.HTTPStatusError):
        await QdrantBackupAdapter(ServingPool(status=500), schema=LiveSchema()).create_backup(TIMESTAMP)

    directory = backups / f"axmem-backup-{TIMESTAMP}"
    assert removed == [(directory, {"ignore_errors": True})]
    assert not directory.exists()


async def test_a_snapshot_that_cannot_be_deleted_is_logged_as_an_error_with_the_words_that_tell_what_to_do(backups, caplog):
    class Stubborn(ServingPool):
        async def delete_snapshot(self, index: int, collection: str, snapshot: str) -> None:
            raise httpx.ConnectError("node went away")

    caplog.set_level(logging.ERROR, logger=backup_module.logger.name)

    await QdrantBackupAdapter(Stubborn(nodes=1), schema=LiveSchema()).create_backup(TIMESTAMP)

    records = records_of(caplog, "A snapshot could not be deleted from its node; delete it with the Qdrant API")
    assert [(record.node, record.collection, record.snapshot, record.levelno) for record in records] == [
        ("http://node-1", MEMORIES, f"{MEMORIES}-0.snapshot", logging.ERROR),
        ("http://node-1", NAMES.links, f"{NAMES.links}-0.snapshot", logging.ERROR),
    ]
    assert all(record.exc_info is not None for record in records)


# ---- what a restore sends and waits for --------------------------------------------------------------------------------


async def test_a_snapshot_is_uploaded_as_a_file_part_named_snapshot_and_the_replicas_are_read_from_the_cluster_endpoint(tmp_path):
    nodes = NodesOfARestore()
    directory = backup_set(tmp_path, entries())

    await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    memories, links = nodes.uploads
    assert [(request.method, request.url.path, dict(request.url.params)) for request in (memories, links)] == [
        ("POST", f"/collections/{MEMORIES}/snapshots/upload", {"priority": "snapshot", "wait": "true"}),
        ("POST", f"/collections/{NAMES.links}/snapshots/upload", {"priority": "snapshot", "wait": "true"}),
    ]
    body = nodes.upload_bodies[0]
    assert b'name="snapshot"; filename="node1-memories.snapshot"' in body
    assert b"Content-Type: application/octet-stream" in body
    assert BODY in body
    assert nodes.polls == [("node-1", f"/collections/{MEMORIES}/cluster"), ("node-1", f"/collections/{NAMES.links}/cluster")]


async def test_replicas_on_other_nodes_count_as_much_as_the_local_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_module, "REPLICA_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(backup_module, "REPLICA_SETTLE_SECONDS", 0.05)
    nodes = NodesOfARestore()
    nodes.reports = [shards(["Active"], ["Dead", "Active"])]
    directory = backup_set(tmp_path, entries())

    with pytest.raises(OperatorCommandError) as refused:
        await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    assert "(states: Active, Active, Dead)" in str(refused.value)


async def test_replicas_that_are_all_active_wherever_they_are_settle_the_restore_at_once(tmp_path):
    nodes = NodesOfARestore()
    nodes.reports = [shards(["Active", "Active"], ["Active"])]
    directory = backup_set(tmp_path, entries())

    await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    assert len(nodes.uploads) == 2  # one upload per file: nothing was uploaded again


async def test_replicas_that_do_not_settle_are_logged_once_with_their_states_before_the_file_goes_up_again(
    tmp_path, monkeypatch, caplog,
):
    monkeypatch.setattr(backup_module, "REPLICA_RETRY_SECONDS", 0.05)
    nodes = NodesOfARestore()
    nodes.reports = [shards(["Dead"], ["Active", "Recovery"]), shards(["Active"], ["Active"])]
    caplog.set_level(logging.WARNING, logger=backup_module.logger.name)
    directory = backup_set(tmp_path, entries())

    await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    [warning] = records_of(
        caplog, "The replicas did not all become Active after the restore of a snapshot; uploading it again",
    )
    assert (warning.node, warning.collection, warning.snapshot, warning.states, warning.levelno) == (
        "http://node-1", MEMORIES, "node1-memories.snapshot", "Active, Dead, Recovery", logging.WARNING,
    )
    assert len(nodes.uploads) == 3  # the memories file twice, then the links file
    assert {path for _node, path in nodes.polls} == {f"/collections/{MEMORIES}/cluster", f"/collections/{NAMES.links}/cluster"}


async def test_replicas_that_never_settle_stop_the_restore_with_a_message_that_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_module, "REPLICA_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(backup_module, "REPLICA_SETTLE_SECONDS", 0.05)
    nodes = NodesOfARestore()
    nodes.reports = [shards([])]
    directory = backup_set(tmp_path, entries())

    with pytest.raises(OperatorCommandError) as refused:
        await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    assert str(refused.value) == (
        f"The replicas of {MEMORIES} did not all become Active within 0 s of the restore of node1-memories.snapshot "
        "(states: none reported). Look at the cluster (docs/configuration.md), then run the restore again"
    )


async def test_a_restore_that_ends_is_logged_with_its_directory_and_the_number_of_nodes(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=backup_module.logger.name)
    nodes = NodesOfARestore(nodes=2)
    directory = backup_set(tmp_path, entries(nodes=2))

    await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    [record] = records_of(caplog, "Qdrant backup restored")
    assert (record.backup_path, record.nodes, record.levelno) == (str(directory), 2, logging.INFO)


async def test_a_directory_that_is_not_a_backup_is_named_in_the_refusal(tmp_path):
    with pytest.raises(FileNotFoundError) as refused:
        await QdrantBackupAdapter(NodesOfARestore(), schema=Schema()).restore_backup(tmp_path)

    assert str(refused.value) == f"Backup not found: {tmp_path}"


async def test_the_timeout_of_an_upload_is_the_snapshot_timeout(tmp_path):
    timeouts: list[float | None] = []

    class Timed(NodesOfARestore):
        def http(self, url: str, timeout: float | None = None) -> httpx.AsyncClient:
            timeouts.append(timeout)
            return super().http(url, timeout)

    directory = backup_set(tmp_path, entries())

    await QdrantBackupAdapter(Timed(), schema=Schema()).restore_backup(directory)

    assert timeouts[0] == SNAPSHOT_TIMEOUT_SECONDS  # the upload; the polls that follow use the timeout of the pool
    assert timeouts[1] is None


# ---- the words of the refusals ----------------------------------------------------------------------------------------


async def test_a_manifest_that_lists_more_nodes_than_are_configured_says_what_a_restore_needs(tmp_path):
    nodes = NodesOfARestore(nodes=1)
    directory = backup_set(tmp_path, entries(nodes=3))

    with pytest.raises(ValueError) as refused:
        await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    assert str(refused.value) == (
        f"{directory / MANIFEST} is not a valid manifest: it holds snapshots of node 2 but QDRANT_URL lists 1 nodes; "
        "a restore needs the nodes the backup was taken from, in the same order"
    )


async def test_nodes_in_another_order_are_listed_in_the_order_of_the_backup_in_the_refusal(tmp_path):
    nodes = NodesOfARestore(nodes=3)
    files = [{**entry, "url": f"http://node-{(entry['node'] % 3) + 1}/"} for entry in entries(nodes=3)]
    directory = backup_set(tmp_path, files)

    with pytest.raises(ValueError) as refused:
        await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    assert str(refused.value) == (
        f"{directory / MANIFEST} is not a valid manifest: QDRANT_URL lists the nodes in another order than the backup was "
        "taken from (http://node-2, http://node-3, http://node-1); a snapshot must go back to the node it came from"
    )


async def test_a_trailing_slash_does_not_make_an_address_another_one(tmp_path):
    nodes = NodesOfARestore(nodes=2)
    files = [{**entry, "url": f"http://node-{entry['node']}/"} for entry in entries(nodes=2)]
    directory = backup_set(tmp_path, files)

    await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    assert len(nodes.uploads) == 4


async def test_an_address_that_ends_in_a_letter_of_its_own_is_not_cut_back_to_another_one(tmp_path):
    """Nodes that moved to addresses that differ from the configured ones only by a trailing ``X`` are other addresses."""
    nodes = NodesOfARestore(nodes=2)
    nodes.urls = ["http://a", "http://b"]
    moved = {1: "http://bX/", 2: "http://aX/"}
    files = [{**entry, "url": moved[entry["node"]]} for entry in entries(nodes=2)]
    directory = backup_set(tmp_path, files)

    await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    assert len(nodes.uploads) == 4


# ---- how the snapshot travels ------------------------------------------------------------------------------------------


async def test_a_snapshot_is_streamed_with_get_in_chunks_of_the_size_set_for_it(backups, monkeypatch):
    monkeypatch.setattr(backup_module, "_CHUNK", 1000)
    sizes: list[int | None] = []
    aiter_bytes = httpx.Response.aiter_bytes

    def recording(self, chunk_size=None):
        sizes.append(chunk_size)
        return aiter_bytes(self, chunk_size)

    monkeypatch.setattr(httpx.Response, "aiter_bytes", recording)
    pool = ServingPool(nodes=1)

    await QdrantBackupAdapter(pool, schema=LiveSchema()).create_backup(TIMESTAMP)

    assert pool.streams == [
        ("GET", f"/collections/{MEMORIES}/snapshots/{MEMORIES}-0.snapshot"),
        ("GET", f"/collections/{NAMES.links}/snapshots/{NAMES.links}-0.snapshot"),
    ]
    assert sizes == [1000, 1000]


async def test_the_manifest_is_written_and_read_as_utf_8_and_a_set_that_was_made_can_be_restored(backups, monkeypatch):
    encodings: list[tuple[str, Any]] = []
    read_text, write_text = Path.read_text, Path.write_text

    def reading(self, *args, **kwargs):
        encodings.append(("read", kwargs.get("encoding")) if self.name == MANIFEST else ("other", None))
        return read_text(self, *args, **kwargs)

    def writing(self, *args, **kwargs):
        encodings.append(("write", kwargs.get("encoding")) if self.name == MANIFEST else ("other", None))
        return write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reading)
    monkeypatch.setattr(Path, "write_text", writing)
    nodes = ServingPool(nodes=1)
    made = await QdrantBackupAdapter(nodes, schema=LiveSchema()).create_backup(TIMESTAMP)

    await QdrantBackupAdapter(NodesOfARestore(), schema=Schema()).restore_backup(made.directory)

    assert [entry for entry in encodings if entry[0] != "other"] == [("write", "utf-8"), ("read", "utf-8")]


# ---- the words of every refusal of a manifest ------------------------------------------------------------------------


def damaged(directory, change: Callable[[dict[str, Any]], None]) -> None:
    """Write the manifest of ``directory`` again after ``change`` was applied to the document."""
    document = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    change(document)
    (directory / MANIFEST).write_text(json.dumps(document), encoding="utf-8")


def second_entry(document: dict[str, Any]) -> dict[str, Any]:
    return document["files"][1]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda document: document["files"].__setitem__(1, "node1-links.snapshot"),
            "{manifest} is not a valid manifest: an entry does not name its node",
        ),
        (
            lambda document: second_entry(document).pop("sha256"),
            "{manifest} is not a valid manifest: an entry lacks its role, collection, file or checksum",
        ),
        (
            lambda document: second_entry(document).update(file="../links.snapshot"),
            "The manifest names a file that is not a snapshot of this backup set: '../links.snapshot'",
        ),
        (
            lambda document: second_entry(document).update(collection="other_links"),
            (
                "The manifest names the 'links' collection 'other_links', which does not belong to this deployment "
                "(QDRANT_COLLECTION_PREFIX='axmem'): refusing to restore it"
            ),
        ),
        (
            lambda document: second_entry(document).update(file="node1-memories.snapshot"),
            "{manifest} is not a valid manifest: the file 'node1-memories.snapshot' is not the links snapshot of node 1",
        ),
        (
            lambda document: document["files"].pop(1),
            "{manifest} is not a valid manifest: node 1 needs exactly one memories and one links snapshot",
        ),
        (lambda document: document.update(files={}), "{manifest} is not a valid manifest: it has no list of files"),
    ],
    ids=[
        "entry-without-node",
        "entry-without-checksum",
        "file-outside-the-set",
        "foreign-collection",
        "wrong-file",
        "missing-links",
        "no-list",
    ],
)
async def test_a_manifest_that_cannot_be_trusted_is_refused_with_a_message_that_names_the_manifest_and_the_fault(tmp_path, change, message):
    directory = backup_set(tmp_path, entries())
    damaged(directory, change)

    with pytest.raises(ValueError) as refused:
        await QdrantBackupAdapter(NodesOfARestore(), schema=Schema()).restore_backup(directory)

    assert str(refused.value) == message.format(manifest=directory / MANIFEST)


async def test_a_manifest_that_is_not_json_is_refused_with_the_reason_the_parser_gave(tmp_path):
    directory = backup_set(tmp_path, entries())
    (directory / MANIFEST).write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError) as refused:
        await QdrantBackupAdapter(NodesOfARestore(), schema=Schema()).restore_backup(directory)

    assert str(refused.value) == (
        f"{directory / MANIFEST} is not a valid manifest: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"
    )


async def test_a_snapshot_that_does_not_match_its_checksum_is_named_in_the_refusal(tmp_path):
    directory = backup_set(tmp_path, entries())
    (directory / "node1-links.snapshot").write_bytes(b"damaged")

    with pytest.raises(ValueError) as refused:
        await QdrantBackupAdapter(NodesOfARestore(), schema=Schema()).restore_backup(directory)

    assert str(refused.value) == "node1-links.snapshot does not match the checksum in the manifest; the backup is damaged"


@pytest.mark.parametrize(
    ("extra_nodes", "complaint"),
    [(2, "node 3 needs exactly one memories and one links snapshot"), (1, "node 2 needs exactly one memories and one links snapshot")],
)
async def test_the_first_node_that_lacks_a_snapshot_is_the_one_named(tmp_path, extra_nodes, complaint):
    nodes = NodesOfARestore(nodes=3)
    directory = backup_set(tmp_path, entries(nodes=extra_nodes))

    with pytest.raises(ValueError) as refused:
        await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    assert str(refused.value) == f"{directory / MANIFEST} is not a valid manifest: {complaint}"


async def test_the_lowest_extra_node_is_the_one_named_when_several_are_extra(tmp_path):
    nodes = NodesOfARestore(nodes=1)
    files = [*entries(nodes=1), *[{**entry, "node": 5, "file": entry["file"].replace("node1", "node5")} for entry in entries(nodes=1)]]
    files += [{**entry, "node": 3, "file": entry["file"].replace("node1", "node3")} for entry in entries(nodes=1)]
    directory = backup_set(tmp_path, files)

    with pytest.raises(ValueError, match=r"it holds snapshots of node 3 but QDRANT_URL lists 1 nodes"):
        await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)


# ---- the order of the nodes -------------------------------------------------------------------------------------------


async def test_configured_addresses_are_compared_without_their_trailing_slash(tmp_path):
    nodes = NodesOfARestore(nodes=2)
    nodes.urls = ["http://node-1/", "http://node-2/"]
    files = [{**entry, "url": f"http://node-{3 - entry['node']}"} for entry in entries(nodes=2)]  # the nodes, the other way round
    directory = backup_set(tmp_path, files)

    with pytest.raises(ValueError, match="QDRANT_URL lists the nodes in another order"):
        await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    assert nodes.uploads == []


async def test_configured_addresses_that_end_in_a_letter_of_their_own_are_not_cut_back(tmp_path):
    nodes = NodesOfARestore(nodes=2)
    nodes.urls = ["http://aX", "http://bX"]
    files = [{**entry, "url": {1: "http://b", 2: "http://a"}[entry["node"]]} for entry in entries(nodes=2)]
    directory = backup_set(tmp_path, files)

    await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    assert len(nodes.uploads) == 4  # other addresses than the ones the backup names: nodes that moved, not nodes in another order


# ---- the replicas of a collection -------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 404, 500])
async def test_any_answer_from_400_up_stops_the_restore_with_the_status_and_the_node(tmp_path, monkeypatch, status):
    monkeypatch.setattr(backup_module, "REPLICA_POLL_SECONDS", 0)
    monkeypatch.setattr(backup_module, "REPLICA_RETRY_SECONDS", 0.05)  # an answer that is let through would wait out its deadline
    monkeypatch.setattr(backup_module, "REPLICA_SETTLE_SECONDS", 0.05)
    nodes = NodesOfARestore()
    nodes.poll_status = status
    directory = backup_set(tmp_path, entries())

    with pytest.raises(OperatorCommandError) as refused:
        await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    assert str(refused.value) == f"Qdrant at http://node-1 would not report the replicas of {MEMORIES}: HTTP {status}"


@pytest.mark.parametrize(
    "report",
    [
        {},
        {"result": {}},
        {"result": {"remote_shards": [{"state": "Active"}]}},
        {"result": {"local_shards": [{"state": "Active"}]}},
    ],
    ids=["empty-answer", "empty-result", "remote-shards-only", "local-shards-only"],
)
async def test_an_answer_without_a_list_of_shards_is_read_as_an_empty_list(tmp_path, monkeypatch, report):
    monkeypatch.setattr(backup_module, "REPLICA_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(backup_module, "REPLICA_SETTLE_SECONDS", 0.05)
    nodes = NodesOfARestore()
    nodes.reports = [report]
    directory = backup_set(tmp_path, entries())
    adapter = QdrantBackupAdapter(nodes, schema=Schema())

    if report.get("result", {}).keys() & {"remote_shards", "local_shards"}:
        await adapter.restore_backup(directory)  # one list is enough: its replicas are all Active
        assert len(nodes.uploads) == 2
    else:
        with pytest.raises(OperatorCommandError, match=r"\(states: none reported\)"):
            await adapter.restore_backup(directory)


async def test_a_replica_is_waited_for_up_to_the_moment_the_time_is_up_and_not_a_poll_longer(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_module, "REPLICA_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(backup_module, "REPLICA_SETTLE_SECONDS", 0.0)
    # The deadline and the first look at the clock agree, then time moves on: a wait that ends exactly at its deadline
    # polls once, one that needs time to pass first polls twice
    ticks = itertools.chain.from_iterable(itertools.repeat(moment, 2) for moment in itertools.count(100.0, 100.0))
    monkeypatch.setattr(backup_module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    nodes = NodesOfARestore()
    nodes.reports = [shards(["Dead"])]
    directory = backup_set(tmp_path, entries())

    with pytest.raises(OperatorCommandError, match=r"states: Dead"):
        await QdrantBackupAdapter(nodes, schema=Schema()).restore_backup(directory)

    assert len(nodes.polls) == 2  # once after each of the two uploads of the first file
