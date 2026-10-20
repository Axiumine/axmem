"""Operator operations on the stored data: snapshots, portable export and import, consistency check."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class BackupResult:
    """A snapshot set: the directory holding it and the snapshot files, one per node and collection."""
    directory: Path
    files: list[Path] = field(default_factory=list)


@dataclass
class ArchiveSummary:
    """What an export wrote or an import read: memories (with the tenants they belong to) and links."""
    memories: int = 0
    links: int = 0
    skipped: int = 0
    tenants: int = 0


@dataclass
class NodeReport:
    """What one Qdrant node holds locally, as read from that node alone."""
    url: str
    points: int | None = None
    error: str | None = None


@dataclass
class ConsistencyReport:
    """Comparison of the nodes of a cluster by local reads: the points the nodes do not agree on."""
    nodes: list[NodeReport] = field(default_factory=list)
    divergent: list[str] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        return not self.divergent and all(node.error is None for node in self.nodes)


class BackupAdapter(Protocol):
    """Collection snapshots, taken on every node and downloaded to private files."""
    async def create_backup(self, timestamp: str) -> BackupResult: ...
    async def restore_backup(self, backup_path: Path) -> None: ...


class ArchiveAdapter(Protocol):
    """Export of all memories and links as JSON lines without vectors, and import that embeds them again."""
    async def export_memories(self, path: Path) -> ArchiveSummary: ...
    async def import_memories(self, path: Path) -> ArchiveSummary: ...


class ClusterInspector(Protocol):
    """Reads every node on its own and reports the points the nodes disagree on."""
    async def check(self) -> ConsistencyReport: ...
