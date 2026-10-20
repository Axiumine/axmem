"""Backups of the Qdrant collections as snapshots, one set per node.

A snapshot holds the shards its node stores locally, and is consistent with that node's write-ahead log
but not with any other node or shard: nothing aligns snapshots in time. A set therefore comes from
writers that were stopped for the duration, and the files of one set are never mixed with another's.
Snapshots hold every tenant's data, so they are written readable by their owner only.
"""
import hashlib
import json
import logging
import shutil
from pathlib import Path

import httpx

from app.config.settings import settings
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
        make_private_dir(settings.backup_directory)
        directory.mkdir(mode=0o700)
        collections = {"memories": await self.schema.live_generation(), "links": self.schema.names.links}
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

        Raises:
            FileNotFoundError: If the directory has no manifest
            ValueError: If a snapshot file does not match the checksum in the manifest
        """
        manifest_path = backup_path / MANIFEST
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Backup not found: {backup_path}")
        files = [entry for entry in json.loads(manifest_path.read_text(encoding="utf-8"))["files"] if entry["node"] == 1]
        for entry in files:
            snapshot = backup_path / entry["file"]
            if hashlib.sha256(snapshot.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError(f"{snapshot.name} does not match the checksum in the manifest; the backup is damaged")
        for entry in files:
            await self._upload(backup_path / entry["file"], entry["collection"])
            if entry["role"] == "memories":
                await self.schema.make_live(entry["collection"])
        logger.info("Qdrant backup restored", extra={"backup_path": str(backup_path)})

    async def _upload(self, snapshot: Path, collection: str) -> None:
        with snapshot.open("rb") as handle:
            async with self.pool.http(self.pool.urls[0]) as http:
                response = await http.post(
                    f"/collections/{collection}/snapshots/upload",
                    params={"priority": "snapshot", "wait": "true"},
                    files={"snapshot": (snapshot.name, handle, "application/octet-stream")},
                )
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise RuntimeError(f"Qdrant refused the snapshot {snapshot.name}: HTTP {response.status_code}")
