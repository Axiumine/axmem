# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""What an operator reads when BackupService refuses a backup, and where the refusal starts.

``test_backup_service.py`` covers the flow; this file pins the exact refusal text (the counts it reports and the
commands it names) and the smallest replication factor that makes the nodes compare before they are snapshotted.
"""
from pathlib import Path

import pytest

from app.config.settings import settings
from app.exceptions import ClusterInconsistentError
from app.protocols.maintenance_protocol import (
    BackupResult,
    ConsistencyReport,
    NodeReport,
)
from app.services.backup_service import BackupService

ADVICE = "run qdrant-check, repair the cluster, and stop the writers before a backup"

ONE_DIVERGENT_POINT = ConsistencyReport(
    nodes=[NodeReport(url="http://a", points=3), NodeReport(url="http://b", points=2)], divergent=["p1"],
)
ONE_UNREADABLE_NODE = ConsistencyReport(
    nodes=[NodeReport(url="http://a", points=3), NodeReport(url="http://b", error="down")],
)
BOTH = ConsistencyReport(
    nodes=[
        NodeReport(url="http://a", error="down"),
        NodeReport(url="http://b", error="timeout"),
        NodeReport(url="http://c", points=1),
    ],
    divergent=["p1", "p2", "p3"],
)


class SnapshotCounter:
    def __init__(self):
        self.created: list[str] = []

    async def create_backup(self, timestamp: str) -> BackupResult:
        self.created.append(timestamp)
        return BackupResult(directory=Path(f"axmem-backup-{timestamp}"))


class FixedInspector:
    def __init__(self, report: ConsistencyReport):
        self.report = report
        self.checks = 0

    async def check(self) -> ConsistencyReport:
        self.checks += 1
        return self.report


def service(report: ConsistencyReport):
    backups, inspector = SnapshotCounter(), FixedInspector(report)
    return BackupService(backups, None, inspector), backups, inspector


@pytest.mark.parametrize(
    ("report", "counts"),
    [
        (ONE_DIVERGENT_POINT, "1 differing points, 0 unreadable nodes"),
        (ONE_UNREADABLE_NODE, "0 differing points, 1 unreadable nodes"),
        (BOTH, "3 differing points, 2 unreadable nodes"),
    ],
    ids=["divergent", "unreadable", "both"],
)
async def test_the_refusal_reports_how_many_points_differ_and_how_many_nodes_cannot_be_read(monkeypatch, report, counts):
    monkeypatch.setattr(settings, "QDRANT_REPLICATION_FACTOR", 3)
    backup_service, backups, _inspector = service(report)

    with pytest.raises(ClusterInconsistentError) as refused:
        await backup_service.create_backup()

    assert str(refused.value) == f"The Qdrant nodes do not agree ({counts}); {ADVICE}"
    assert backups.created == []


async def test_two_replicas_are_already_compared_before_the_snapshot(monkeypatch):
    monkeypatch.setattr(settings, "QDRANT_REPLICATION_FACTOR", 2)
    backup_service, backups, inspector = service(ONE_DIVERGENT_POINT)

    with pytest.raises(ClusterInconsistentError):
        await backup_service.create_backup()

    assert inspector.checks == 1
    assert backups.created == []
