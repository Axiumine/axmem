"""Integration tests for the words an operator reads from backup, restore, export, import and qdrant-check.

The commands print a short report on stdout, the warnings and refusals on stderr, and return the exit code a script
tests. The reports are checked to the character against the results of the backup service; the open and the closing of
the Qdrant stack, which ``test_cli_operator_commands.py`` runs against a real node, is replaced here by a recording
seam, so what the stack was asked for (the options of ``_with_service``) is checked too.
"""
from pathlib import Path

import pytest

from app.protocols.maintenance_protocol import (
    ArchiveSummary,
    BackupResult,
    ConsistencyReport,
    NodeReport,
)
from app.routes.cli import operator_commands

LINKS_ADVICE = (
    "Each node's snapshot holds only that node's shards; keep the set together. A restore puts every file back on the "
    "node it came from, so QDRANT_URL must list the same nodes in the same order then."
)


class RecordingService:
    """Stands in for the backup service: records its calls and answers with the results it is given."""

    def __init__(self, backup=None, summary=None, report=None):
        self.calls = []
        self._backup = backup
        self._summary = summary
        self._report = report

    async def create_backup(self, *, force):
        self.calls.append(("create_backup", force))
        return self._backup

    async def restore_backup(self, path):
        self.calls.append(("restore_backup", path))

    async def export_memories(self, path):
        self.calls.append(("export_memories", path))
        return self._summary

    async def import_memories(self, path):
        self.calls.append(("import_memories", path))
        return self._summary

    async def check(self):
        self.calls.append(("check",))
        return self._report


@pytest.fixture
def stack(monkeypatch):
    """Run the command bodies on a ``RecordingService``; ``stack(service)`` returns the options the stack was opened with."""
    opened = []

    def install(service):
        async def with_service(run, **options):
            opened.append(options)
            return await run(service)

        monkeypatch.setattr(operator_commands, "_with_service", with_service)
        return opened

    return install


async def test_a_backup_names_the_set_and_every_file_and_reminds_to_keep_them_together(stack, capsys):
    service = RecordingService(backup=BackupResult(Path("/backups/set-1"), [Path("n1/memories.snapshot"), Path("n2/links.snapshot")]))
    opened = stack(service)

    assert await operator_commands.backup() == 0

    captured = capsys.readouterr()
    assert captured.out == f"Backup saved: /backups/set-1\n  memories.snapshot\n  links.snapshot\n{LINKS_ADVICE}\n"
    assert captured.err == ""
    assert service.calls == [("create_backup", False)]
    assert opened == [{"links_whole": True}]


async def test_a_backup_is_forced_only_when_asked(stack):
    service = RecordingService(backup=BackupResult(Path("/backups/set-1")))
    stack(service)

    assert await operator_commands.backup(force=True) == 0

    assert service.calls == [("create_backup", True)]


async def test_a_confirmed_restore_names_the_set_and_the_next_step_for_a_cluster(stack, capsys):
    service = RecordingService()
    opened = stack(service)

    assert await operator_commands.restore(Path("/backups/set-1"), confirmed=True) == 0

    captured = capsys.readouterr()
    assert captured.out == (
        "Restored: /backups/set-1\n"
        "In a cluster, wait until every replica is Active, then run qdrant-check before starting the writers.\n"
    )
    assert captured.err == ""
    assert service.calls == [("restore_backup", Path("/backups/set-1"))]
    assert opened == [{}]


async def test_a_restore_that_is_not_confirmed_refuses_in_one_line_without_opening_the_stack(stack, capsys):
    service = RecordingService()
    opened = stack(service)

    assert await operator_commands.restore(Path("/backups/set-1"), confirmed=False) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Error: restoring replaces the data in Qdrant with the snapshot's; stop the server first "
        "and repeat the command with --yes\n"
    )
    assert service.calls == []
    assert opened == []


async def test_an_export_reports_what_it_wrote(stack, capsys):
    service = RecordingService(summary=ArchiveSummary(memories=3, links=1, tenants=2, memories_at_end=3, links_at_end=1))
    opened = stack(service)

    assert await operator_commands.export_memories(Path("/out/all.jsonl")) == 0

    captured = capsys.readouterr()
    assert captured.out == "Exported 3 memories and 1 links of 2 tenants to /out/all.jsonl\n"
    assert captured.err == ""
    assert service.calls == [("export_memories", Path("/out/all.jsonl"))]
    assert opened == [{"links_whole": True}]


async def test_an_export_that_writers_changed_meanwhile_warns_on_stderr_and_still_succeeds(stack, capsys):
    stack(RecordingService(summary=ArchiveSummary(memories=3, links=1, tenants=2, memories_at_end=5, links_at_end=2)))

    assert await operator_commands.export_memories(Path("/out/all.jsonl")) == 0

    captured = capsys.readouterr()
    assert captured.out == "Exported 3 memories and 1 links of 2 tenants to /out/all.jsonl\n"
    assert captured.err == (
        "Warning: writers changed the data while the export ran. Qdrant holds 5 memories and 2 links now, the file holds 3 and 1: "
        "memories created meanwhile may be missing from it. Stop the writers and export again for an exact copy.\n"
    )


async def test_an_import_reports_what_it_read_and_makes_the_collections_first(stack, capsys):
    service = RecordingService(summary=ArchiveSummary(memories=3, links=1, tenants=1, skipped=2))
    opened = stack(service)

    assert await operator_commands.import_memories(Path("/in/all.jsonl")) == 0

    captured = capsys.readouterr()
    assert captured.out == "Imported 3 memories and 1 links of 1 tenants from /in/all.jsonl (2 lines skipped)\n"
    assert captured.err == ""
    assert service.calls == [("import_memories", Path("/in/all.jsonl"))]
    assert opened == [{"create_collections": True}]


async def test_nodes_that_agree_are_listed_with_their_points(stack, capsys):
    service = RecordingService(report=ConsistencyReport(nodes=[NodeReport("http://a:6333", points=5), NodeReport("http://b:6333", points=5)]))
    opened = stack(service)

    assert await operator_commands.check() == 0

    captured = capsys.readouterr()
    assert captured.out == "  http://a:6333: 5 points\n  http://b:6333: 5 points\nThe nodes agree.\n"
    assert captured.err == ""
    assert service.calls == [("check",)]
    assert opened == [{}]


@pytest.mark.parametrize("divergent_count", [1, operator_commands.REPORT_LIMIT - 1, operator_commands.REPORT_LIMIT, operator_commands.REPORT_LIMIT + 1])
async def test_the_differing_points_are_listed_up_to_the_limit_and_the_rest_is_counted(stack, capsys, divergent_count):
    divergent = [f"collection:{index}" for index in range(divergent_count)]
    stack(RecordingService(report=ConsistencyReport(nodes=[NodeReport("http://a", points=5), NodeReport("http://b", points=4)], divergent=divergent)))

    assert await operator_commands.check() == 1

    shown = divergent[: operator_commands.REPORT_LIMIT]
    hidden = divergent_count - len(shown)
    assert capsys.readouterr().out == (
        "  http://a: 5 points\n  http://b: 4 points\n"
        + "".join(f"  differs: {point}\n" for point in shown)
        + (f"  ... and {hidden} more\n" if hidden else "")
        + f"The nodes that answered do not agree on {divergent_count} points.\n"
        + "Nothing was changed.\n"
    )
