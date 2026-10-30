# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""Coordinate snapshots, portable exports and the consistency check through the storage adapters."""

from datetime import datetime
from pathlib import Path

from app.config.settings import settings
from app.exceptions import ClusterInconsistentError
from app.protocols.maintenance_protocol import (
    ArchiveAdapter,
    ArchiveSummary,
    BackupAdapter,
    BackupResult,
    ClusterInspector,
    ConsistencyReport,
)


class BackupService:
    """Create and restore backups without knowing the storage implementation."""

    def __init__(self, backups: BackupAdapter, archive: ArchiveAdapter, inspector: ClusterInspector):
        self.backups = backups
        self.archive = archive
        self.inspector = inspector

    async def create_backup(self, *, force: bool = False) -> BackupResult:
        """Snapshot every node.

        With replicated collections the nodes must agree first: snapshots of nodes that disagree would
        mix states of different instants, and nothing ties a snapshot of one node to another's.
        ``force`` skips that check.

        Raises:
            ClusterInconsistentError: If the nodes of a replicated deployment disagree
        """
        if settings.QDRANT_REPLICATION_FACTOR > 1 and not force:
            report = await self.inspector.check()
            if not report.consistent:
                raise ClusterInconsistentError(
                    f"The Qdrant nodes do not agree ({len(report.divergent)} differing points, "
                    f"{sum(1 for node in report.nodes if node.error)} unreadable nodes); "
                    "run qdrant-check, repair the cluster, and stop the writers before a backup",
                )
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")  # local time
        return await self.backups.create_backup(timestamp)

    async def restore_backup(self, backup_path: Path) -> None:
        await self.backups.restore_backup(backup_path)

    async def export_memories(self, path: Path) -> ArchiveSummary:
        return await self.archive.export_memories(path)

    async def import_memories(self, path: Path) -> ArchiveSummary:
        return await self.archive.import_memories(path)

    async def check(self) -> ConsistencyReport:
        return await self.inspector.check()
