"""Backups of the Qdrant collections as snapshots, one set per node.

A snapshot holds the shards its node stores locally, and is consistent with that node's write-ahead log
but not with any other node or shard: nothing aligns snapshots in time. A set therefore comes from
writers that were stopped for the duration, and the files of one set are never mixed with another's.
Snapshots hold every tenant's data, so they are written readable by their owner only.
"""
import hashlib
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

import httpx

from app.config.settings import settings
from app.exceptions import OperatorCommandError
from app.protocols.maintenance_protocol import BackupResult
from app.repositories.qdrant.client import NodePool
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
        snapshot = await self.pool.create_snapshot(index, collection)
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
        await self.pool.delete_snapshot(index, collection, snapshot)
        return {
            "node": index + 1,
            "role": role,
            "collection": collection,
            "file": target.name,
            "sha256": digest.hexdigest(),
            "bytes": target.stat().st_size,
        }

    async def restore_backup(self, backup_path: Path) -> None:
        """Recover the collections of a backup set from the snapshots of its first node.

        The snapshot wins over whatever the collection holds now (``priority=snapshot``). The restored
        memory generation becomes the live one. In a cluster the other replicas catch up from the node
        that was restored; check the replica states afterwards (docs/configuration.md).

        The manifest is read as untrusted input and checked before anything is uploaded: every file must be
        a snapshot of this set that the checksum confirms, and every collection one of this deployment's.

        Raises:
            FileNotFoundError: If the directory has no manifest
            ValueError: If the manifest is not a valid one for this deployment, or a snapshot file does not
                match the checksum in the manifest
        """
        manifest_path = backup_path / MANIFEST
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Backup not found: {backup_path}")
        files = self._first_node_entries(manifest_path)
        for entry in files:
            snapshot = backup_path / entry["file"]
            if _sha256_of(snapshot) != entry["sha256"]:
                raise ValueError(f"{snapshot.name} does not match the checksum in the manifest; the backup is damaged")
        for entry in files:
            await self._upload(backup_path / entry["file"], entry["collection"])
            if entry["role"] == "memories":
                await self.schema.make_live(entry["collection"])
        logger.info("Qdrant backup restored", extra={"backup_path": str(backup_path)})

    def _first_node_entries(self, manifest_path: Path) -> list[dict[str, str]]:
        """The entries of node 1 in the manifest, or ``ValueError`` naming what is wrong with the manifest."""
        problem = f"{manifest_path} is not a valid manifest"
        entries: list[dict[str, str]] = []
        for entry in _manifest_files(manifest_path):
            if not isinstance(entry, dict) or not isinstance(entry.get("node"), int):
                raise ValueError(f"{problem}: an entry does not name its node")
            if entry["node"] == 1:
                self._check_entry(entry, problem)
                entries.append(entry)
        if sorted(entry["role"] for entry in entries) != ["links", "memories"]:
            raise ValueError(f"{problem}: node 1 needs exactly one memories and one links snapshot")
        return entries

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

    def _is_ours(self, role: str, collection: str) -> bool:
        """Whether ``collection`` is one that this deployment's settings name for ``role``."""
        names = self.schema.names
        if role == "memories":
            return re.fullmatch(re.escape(names.live) + _GENERATION_SUFFIX, collection) is not None
        return role == "links" and collection == names.links

    async def _upload(self, snapshot: Path, collection: str) -> None:
        with snapshot.open("rb") as handle:
            async with self.pool.http(self.pool.urls[0]) as http:
                response = await http.post(
                    f"/collections/{collection}/snapshots/upload",
                    params={"priority": "snapshot", "wait": "true"},
                    files={"snapshot": (snapshot.name, handle, "application/octet-stream")},
                )
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise OperatorCommandError(f"Qdrant refused the snapshot {snapshot.name}: HTTP {response.status_code}")


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
