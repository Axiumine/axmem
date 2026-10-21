"""Backups of the Qdrant collections as snapshots, one set per node.

A snapshot holds the shards its node stores locally, and is consistent with that node's write-ahead log
but not with any other node or shard: nothing aligns snapshots in time. A set therefore comes from
writers that were stopped for the duration, and the files of one set are never mixed with another's.
Which shards a node stores depends on the layout (a replication factor below the number of nodes spreads
them), so a restore puts every node's snapshot back on that node and never relies on one node holding it all.
Snapshots hold every tenant's data, so they are written readable by their owner only.
"""
import asyncio
import hashlib
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

import httpx

from app.config.settings import settings
from app.exceptions import OperatorCommandError
from app.protocols.maintenance_protocol import BackupResult
from app.repositories.qdrant.client import SNAPSHOT_TIMEOUT_SECONDS, NodePool
from app.repositories.qdrant.schema import QdrantSchema
from app.utils.private_files import (
    create_private_file,
    make_private_dir,
    restrict_to_owner,
)

logger = logging.getLogger(__name__)

MANIFEST = "manifest.json"
_CHUNK = 1024 * 1024
# What ``create_backup`` names a snapshot file: nothing else in a manifest is a file of this backup set
_SNAPSHOT_FILE = re.compile(r"node\d+-(memories|links)\.snapshot")
_GENERATION_SUFFIX = r"_\d{8}t\d{6}[0-9a-f]{4}"
# How long a restore waits, after each file, for the replicas Qdrant marked for recovery to become Active again, and
# how often it looks. The next file is uploaded only then: a node that restores its shards while a replica on another
# node is still being recovered from the first can leave every replica of a shard Dead.
REPLICA_SETTLE_SECONDS = 600.0
REPLICA_POLL_SECONDS = 1.0
# A replica that is still not Active after this long is not on its way: Qdrant can leave the replica of a shard it just
# restored in ManualRecovery for good when its own re-sync of the other replicas claims the shard at that moment. The
# file is then uploaded once more, which activates it.
REPLICA_RETRY_SECONDS = 90.0


class QdrantBackupAdapter:
    """Snapshot, download and restore of the memory and link collections."""

    def __init__(self, pool: NodePool, schema: QdrantSchema):
        self.pool = pool
        self.schema = schema

    async def create_backup(self, timestamp: str) -> BackupResult:
        """Snapshot the live memory generation and the links on every node and download the files.

        The set is written to a directory named after the timestamp, with a manifest that names each file's
        node, collection and checksum. A failed backup leaves no directory behind.
        """
        directory = settings.backup_directory / f"axmem-backup-{timestamp}"
        # Asked before the directory exists: a node that refuses the question must leave nothing behind
        collections = {"memories": await self.schema.live_generation(), "links": self.schema.names.links}
        make_private_dir(settings.backup_directory)
        directory.mkdir(mode=0o700)
        entries: list[dict[str, str | int]] = []
        try:
            for index, url in enumerate(self.pool.urls):
                for role, collection in collections.items():
                    entries.append(await self._download(directory, index, url, role, collection))
            manifest = directory / MANIFEST
            create_private_file(manifest)
            manifest.write_text(json.dumps({"created": timestamp, "files": entries}, indent=2), encoding="utf-8")
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        logger.info("Qdrant backup created", extra={"backup_path": str(directory), "files": len(entries)})
        return BackupResult(directory=directory, files=[directory / str(entry["file"]) for entry in entries])

    async def _download(
        self, directory: Path, index: int, url: str, role: str, collection: str,
    ) -> dict[str, str | int]:
        """Snapshot ``collection`` on one node and download it; the snapshot does not stay on the node, whatever happens."""
        snapshot = await self.pool.create_snapshot(index, collection)
        try:
            entry = await self._fetch(directory, index, url, role, collection, snapshot)
        except BaseException:
            await self._drop_snapshot(index, collection, snapshot)
            raise
        await self._drop_snapshot(index, collection, snapshot)
        return entry

    async def _fetch(
        self, directory: Path, index: int, url: str, role: str, collection: str, snapshot: str,
    ) -> dict[str, str | int]:
        target = directory / f"node{index + 1}-{role}.snapshot"
        create_private_file(target)
        digest = hashlib.sha256()
        async with self.pool.http(url) as http, http.stream(
            "GET", f"/collections/{collection}/snapshots/{snapshot}",
        ) as response:
            response.raise_for_status()
            with target.open("wb") as out:
                async for chunk in response.aiter_bytes(_CHUNK):
                    out.write(chunk)
                    digest.update(chunk)
        restrict_to_owner(target)
        return {
            "node": index + 1,
            "role": role,
            "collection": collection,
            "file": target.name,
            "sha256": digest.hexdigest(),
            "bytes": target.stat().st_size,
            "url": url,
        }

    async def _drop_snapshot(self, index: int, collection: str, snapshot: str) -> None:
        """Delete a snapshot from its node. A snapshot is as large as the node's shards, so one that stays is a leak:
        when it cannot be deleted the operator is told which one (the backup itself is not failed by that)."""
        try:
            await self.pool.delete_snapshot(index, collection, snapshot)
        except Exception:
            logger.exception(
                "A snapshot could not be deleted from its node; delete it with the Qdrant API",
                extra={"node": self.pool.urls[index], "collection": collection, "snapshot": snapshot},
            )

    async def restore_backup(self, backup_path: Path) -> None:
        """Recover the collections of a backup set: every node's snapshots go back to that node.

        A node's snapshot holds the shards that node stored, which with a replication factor below the number of nodes
        is only some of them, so restoring one node's snapshot alone would bring back part of the data. Each file is
        uploaded to the node it was taken from (node N of the backup is the N-th entry of ``QDRANT_URL``), and the
        snapshot wins over whatever the collection holds now (``priority=snapshot``). The restored memory generation
        becomes the live one once every file is in, and the staging collection of an unfinished ``re-embed`` is removed.
        In a cluster the replicas then settle; check their states afterwards (docs/configuration.md).

        The manifest is read as untrusted input and checked before anything is uploaded: every file must be
        a snapshot of this set that the checksum confirms, every collection one of this deployment's, and the nodes
        must be the ones this deployment is configured with.

        Raises:
            FileNotFoundError: If the directory has no manifest
            ValueError: If the manifest is not a valid one for this deployment, or a snapshot file does not
                match the checksum in the manifest
        """
        manifest_path = backup_path / MANIFEST
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Backup not found: {backup_path}")
        files = self._node_entries(manifest_path)
        for entry in files:
            snapshot = backup_path / entry["file"]
            if _sha256_of(snapshot) != entry["sha256"]:
                raise ValueError(f"{snapshot.name} does not match the checksum in the manifest; the backup is damaged")
        for done, entry in enumerate(files):
            try:
                await self._restore_file(backup_path / entry["file"], entry["collection"], entry["node"] - 1)
            except OperatorCommandError as exc:
                if done:  # a restore is repeatable: the files already in are put in again and win again
                    raise OperatorCommandError(
                        f"{exc}. {done} of {len(files)} files were already restored: fix the cause and run the restore again",
                    ) from exc
                raise
        await self.schema.make_live(next(entry["collection"] for entry in files if entry["role"] == "memories"))
        # The snapshot is the source of truth: the links that an unfinished `re-embed` left in its staging collection
        # are not the restored ones, and copying them back later would add links the restored data never had
        await self.schema.discard(self.schema.names.links_staging)
        logger.info("Qdrant backup restored", extra={"backup_path": str(backup_path), "nodes": len(self.pool.urls)})

    def _node_entries(self, manifest_path: Path) -> list[dict[str, Any]]:
        """The entries of the manifest, memories before links, node by node, or ``ValueError`` naming what is wrong with it."""
        problem = f"{manifest_path} is not a valid manifest"
        entries: list[dict[str, Any]] = []
        for entry in _manifest_files(manifest_path):
            if not isinstance(entry, dict) or type(entry.get("node")) is not int:
                raise ValueError(f"{problem}: an entry does not name its node")
            self._check_entry(entry, problem)
            entries.append(entry)
        self._check_nodes(entries, problem)
        self._check_order(entries, problem)
        # The links collection has one name; the memory generation is the one every node's snapshot must be of
        if len({entry["collection"] for entry in entries if entry["role"] == "memories"}) != 1:
            raise ValueError(f"{problem}: the memories snapshots of the nodes are of different collections")
        return sorted(entries, key=lambda entry: (entry["node"], entry["role"] != "memories"))

    def _check_nodes(self, entries: list[dict[str, Any]], problem: str) -> None:
        """Refuse a set that is not exactly one memories and one links snapshot for each node of ``QDRANT_URL``."""
        nodes = range(1, len(self.pool.urls) + 1)
        for node in nodes:
            if sorted(entry["role"] for entry in entries if entry["node"] == node) != ["links", "memories"]:
                raise ValueError(f"{problem}: node {node} needs exactly one memories and one links snapshot")
        if extra := sorted({entry["node"] for entry in entries} - set(nodes)):
            raise ValueError(
                f"{problem}: it holds snapshots of node {extra[0]} but QDRANT_URL lists {len(self.pool.urls)} nodes; "
                "a restore needs the nodes the backup was taken from, in the same order",
            )

    def _check_order(self, entries: list[dict[str, Any]], problem: str) -> None:
        """Refuse a backup whose nodes are the configured ones in another order: node N's snapshot on another node would mix shards up.

        A node that moved to another address is not an error (the manifest records where each file was taken from, and the
        address may have changed since); only the same set of addresses in a different order is.
        """
        recorded = {entry["node"]: str(entry["url"]).rstrip("/") for entry in entries if isinstance(entry.get("url"), str)}
        configured = [url.rstrip("/") for url in self.pool.urls]
        if len(recorded) == len(configured) and sorted(recorded.values()) == sorted(configured) and any(
            recorded[number] != url for number, url in enumerate(configured, start=1)
        ):
            raise ValueError(
                f"{problem}: QDRANT_URL lists the nodes in another order than the backup was taken from "
                f"({', '.join(recorded[number] for number in sorted(recorded))}); a snapshot must go back to the node it came from",
            )

    def _check_entry(self, entry: dict[str, Any], problem: str) -> None:
        """Refuse an entry whose fields are not text, whose file is not a snapshot of the set or whose collection is foreign."""
        if not all(isinstance(entry.get(key), str) for key in ("role", "collection", "file", "sha256")):
            raise ValueError(f"{problem}: an entry lacks its role, collection, file or checksum")
        if not _SNAPSHOT_FILE.fullmatch(entry["file"]):
            raise ValueError(f"The manifest names a file that is not a snapshot of this backup set: {entry['file']!r}")
        if not self._is_ours(entry["role"], entry["collection"]):
            raise ValueError(
                f"The manifest names the {entry['role']!r} collection {entry['collection']!r}, which does not belong to "
                f"this deployment (QDRANT_COLLECTION_PREFIX={self.schema.names.prefix!r}): refusing to restore it",
            )
        if entry["file"] != f"node{entry['node']}-{entry['role']}.snapshot":
            raise ValueError(f"{problem}: the file {entry['file']!r} is not the {entry['role']} snapshot of node {entry['node']}")

    def _is_ours(self, role: str, collection: str) -> bool:
        """Whether ``collection`` is one that this deployment's settings name for ``role``."""
        names = self.schema.names
        if role == "memories":
            return re.fullmatch(re.escape(names.live) + _GENERATION_SUFFIX, collection) is not None
        return role == "links" and collection == names.links

    async def _upload(self, snapshot: Path, collection: str, index: int) -> None:
        with snapshot.open("rb") as handle:
            async with self.pool.http(self.pool.urls[index], timeout=SNAPSHOT_TIMEOUT_SECONDS) as http:
                response = await http.post(
                    f"/collections/{collection}/snapshots/upload",
                    params={"priority": "snapshot", "wait": "true"},
                    files={"snapshot": (snapshot.name, handle, "application/octet-stream")},
                )
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise OperatorCommandError(f"Qdrant refused the snapshot {snapshot.name}: HTTP {response.status_code}")


    async def _restore_file(self, snapshot: Path, collection: str, index: int) -> None:
        """Upload one node's snapshot and wait for the replicas; upload it a second time when they do not settle.

        Raises:
            OperatorCommandError: If Qdrant refuses the file, or the replicas are not all Active
                ``REPLICA_SETTLE_SECONDS`` after the second upload
        """
        await self._upload(snapshot, collection, index)
        unsettled = await self._wait_until_active(collection, index, REPLICA_RETRY_SECONDS)
        if unsettled is None:
            return
        logger.warning(
            "The replicas did not all become Active after the restore of a snapshot; uploading it again",
            extra={"node": self.pool.urls[index], "collection": collection, "snapshot": snapshot.name, "states": unsettled},
        )
        await self._upload(snapshot, collection, index)
        unsettled = await self._wait_until_active(collection, index, REPLICA_SETTLE_SECONDS)
        if unsettled is not None:
            raise OperatorCommandError(
                f"The replicas of {collection} did not all become Active within {REPLICA_SETTLE_SECONDS:.0f} s of the "
                f"restore of {snapshot.name} (states: {unsettled}). Look at the cluster (docs/configuration.md), "
                "then run the restore again",
            )

    async def _wait_until_active(self, collection: str, index: int, within: float) -> str | None:
        """Wait up to ``within`` seconds until every replica of ``collection`` is Active, as node ``index`` reports it.

        Uploading a snapshot makes Qdrant mark the other replicas of the shards it restored for recovery, and they
        recover from the restored one by shard transfers; that takes seconds on an idle cluster and more on a busy one.

        Returns:
            None when all replicas are Active, otherwise the states they were in when ``within`` seconds had passed

        Raises:
            OperatorCommandError: If Qdrant would not report the replicas
        """
        deadline = time.monotonic() + within
        async with self.pool.http(self.pool.urls[index]) as http:
            while True:
                response = await http.get(f"/collections/{collection}/cluster")
                if response.status_code >= httpx.codes.BAD_REQUEST:
                    raise OperatorCommandError(
                        f"Qdrant at {self.pool.urls[index]} would not report the replicas of {collection}: HTTP {response.status_code}",
                    )
                result = response.json().get("result", {})
                states = sorted(str(shard.get("state")) for shard in [*result.get("local_shards", []), *result.get("remote_shards", [])])
                if states and set(states) == {"Active"}:
                    return None
                if time.monotonic() >= deadline:
                    return ", ".join(states) or "none reported"
                await asyncio.sleep(REPLICA_POLL_SECONDS)


def _manifest_files(manifest_path: Path) -> list[Any]:
    """The list of file entries in the manifest, or ``ValueError`` when the manifest is not JSON of that shape."""
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, RecursionError) as exc:  # invalid JSON, invalid UTF-8 or nested deeper than the parser goes
        raise ValueError(f"{manifest_path} is not a valid manifest: {exc}") from exc
    listed = document.get("files") if isinstance(document, dict) else None
    if not isinstance(listed, list):
        raise ValueError(f"{manifest_path} is not a valid manifest: it has no list of files")
    return listed


def _sha256_of(path: Path) -> str:
    """The checksum of a file, read in chunks (a snapshot can be many gigabytes)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
