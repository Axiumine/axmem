# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""Integration tests for BackupService.

The service only coordinates the storage adapters: the consistency gate of replicated deployments, the
timestamped backup name and the hand-over to the snapshot, archive and inspector adapters. The adapters
themselves run against a real Qdrant node in ``tests/e2e/test_backup_restore_e2e.py``.
"""
from datetime import datetime
from pathlib import Path

import pytest

from app.bootstrap import create_backup_service
from app.config.settings import settings
from app.exceptions import ClusterInconsistentError
from app.protocols.maintenance_protocol import (
    ArchiveSummary,
    BackupResult,
    ConsistencyReport,
    NodeReport,
)
from app.services.backup_service import BackupService


class RecordingBackups:
    def __init__(self):
        self.created: list[str] = []
        self.restored: list[Path] = []

    async def create_backup(self, timestamp: str) -> BackupResult:
        self.created.append(timestamp)
        return BackupResult(directory=Path(f"axmem-backup-{timestamp}"))

    async def restore_backup(self, backup_path: Path) -> None:
        self.restored.append(backup_path)


class RecordingArchive:
    def __init__(self):
        self.calls: list[tuple[str, Path]] = []

    async def export_memories(self, path: Path) -> ArchiveSummary:
        self.calls.append(("export", path))
        return ArchiveSummary(memories=2, links=1, tenants=1)

    async def import_memories(self, path: Path) -> ArchiveSummary:
        self.calls.append(("import", path))
        return ArchiveSummary(memories=2, links=1, tenants=1, skipped=1)


class FixedInspector:
    def __init__(self, report: ConsistencyReport):
        self.report = report
        self.checks = 0

    async def check(self) -> ConsistencyReport:
        self.checks += 1
        return self.report


AGREEING = ConsistencyReport(nodes=[NodeReport(url="http://a", points=3), NodeReport(url="http://b", points=3)])
DIVERGENT = ConsistencyReport(
    nodes=[NodeReport(url="http://a", points=3), NodeReport(url="http://b", points=2)], divergent=["p1"],
)
UNREADABLE = ConsistencyReport(nodes=[NodeReport(url="http://a", points=3), NodeReport(url="http://b", error="down")])


def service(report: ConsistencyReport = AGREEING):
    backups, archive, inspector = RecordingBackups(), RecordingArchive(), FixedInspector(report)
    return BackupService(backups, archive, inspector), backups, archive, inspector


async def test_backup_name_carries_a_sortable_timestamp():
    backup_service, backups, _archive, _inspector = service()

    result = await backup_service.create_backup()

    (stamp,) = backups.created
    datetime.strptime(stamp, "%Y%m%d-%H%M%S")  # noqa: DTZ007 - only the format of the stamp is checked
    assert result.directory.name == f"axmem-backup-{stamp}"


async def test_single_replica_backup_does_not_compare_nodes(monkeypatch):
    monkeypatch.setattr(settings, "QDRANT_REPLICATION_FACTOR", 1)
    backup_service, backups, _archive, inspector = service(DIVERGENT)

    await backup_service.create_backup()

    assert inspector.checks == 0
    assert len(backups.created) == 1


async def test_replicated_backup_proceeds_when_the_nodes_agree(monkeypatch):
    monkeypatch.setattr(settings, "QDRANT_REPLICATION_FACTOR", 3)
    backup_service, backups, _archive, inspector = service(AGREEING)

    await backup_service.create_backup()

    assert inspector.checks == 1
    assert len(backups.created) == 1


@pytest.mark.parametrize("report", [DIVERGENT, UNREADABLE], ids=["divergent", "unreadable"])
async def test_replicated_backup_is_refused_when_the_nodes_disagree(monkeypatch, report):
    monkeypatch.setattr(settings, "QDRANT_REPLICATION_FACTOR", 3)
    backup_service, backups, _archive, _inspector = service(report)

    with pytest.raises(ClusterInconsistentError, match="qdrant-check"):
        await backup_service.create_backup()

    assert backups.created == []


async def test_force_backs_up_a_cluster_that_disagrees(monkeypatch):
    monkeypatch.setattr(settings, "QDRANT_REPLICATION_FACTOR", 3)
    backup_service, backups, _archive, inspector = service(DIVERGENT)

    await backup_service.create_backup(force=True)

    assert inspector.checks == 0
    assert len(backups.created) == 1


async def test_restore_export_import_and_check_are_handed_to_the_adapters():
    backup_service, backups, archive, inspector = service()

    await backup_service.restore_backup(Path("some-backup"))
    exported = await backup_service.export_memories(Path("out.jsonl"))
    imported = await backup_service.import_memories(Path("in.jsonl"))
    report = await backup_service.check()

    assert backups.restored == [Path("some-backup")]
    assert archive.calls == [("export", Path("out.jsonl")), ("import", Path("in.jsonl"))]
    assert (exported.memories, imported.skipped) == (2, 1)
    assert report is AGREEING
    assert inspector.checks == 1


async def test_restore_of_a_missing_backup_raises(isolated_stack, tmp_path):
    real = create_backup_service(isolated_stack)

    with pytest.raises(FileNotFoundError):
        await real.restore_backup(tmp_path / "nonexistent")
