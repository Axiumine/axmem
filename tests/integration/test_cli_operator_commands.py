"""Operator commands (backup, restore, export, import, qdrant-check) from parsed arguments to the Qdrant stack.

They open the stack the settings describe, never a server, print a short report and return the exit code.
The stack here is a real Qdrant node with collections of the test's own; only the logging set-up is recorded
instead of installed.
"""
import atexit
import json
from pathlib import Path
from uuid import uuid4

import pytest

import app.config.logging_config as logging_config
from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.protocols.maintenance_protocol import ConsistencyReport, NodeReport
from app.routes.cli import operator_commands
from app.routes.cli.parser import build_parser, dispatch
from tests.backup_restore_cases import seed_memories


@pytest.fixture(autouse=True)
def logging_calls(monkeypatch):
    """Record what the commands ask of the logging set-up, so a test run installs no handlers and no exit hook."""
    configured, registered = [], []
    monkeypatch.setattr(logging_config, "configure_logging", lambda **kwargs: configured.append(kwargs))
    monkeypatch.setattr(atexit, "register", registered.append)
    return configured, registered


@pytest.fixture
async def seeded(isolated_stack, tensorrt_embedder, tmp_path, monkeypatch):
    """Collections of the test's own with three memories of one tenant (one link), and a backup directory."""
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    user_id, originals = await seed_memories(repo)
    return repo, user_id, originals


async def test_a_command_logs_quietly_and_flushes_at_exit(seeded, tmp_path, logging_calls):
    configured, registered = logging_calls

    await operator_commands.export_memories(tmp_path / "export.jsonl")

    assert configured == [{"log_level": "WARNING", "log_format": "console"}]
    assert registered == [logging_config.shutdown_logging]


async def test_backup_then_restore_with_confirmation(seeded, capsys):
    repo, user_id, originals = seeded

    assert await operator_commands.backup() == 0

    out = capsys.readouterr().out
    saved = Path(out.splitlines()[0].removeprefix("Backup saved: "))
    assert (saved / "manifest.json").is_file()
    assert "node1-memories.snapshot" in out
    assert "keep the set together" in out
    await repo.restore_obsolete(user_id, originals[2].id)

    assert await operator_commands.restore(saved, confirmed=True) == 0

    assert f"Restored: {saved}" in capsys.readouterr().out
    assert (await repo.get_memory_by_id(user_id, originals[2].id)).is_obsolete


async def test_restore_without_confirmation_changes_nothing_and_exits_2(seeded, tmp_path, capsys):
    repo, user_id, originals = seeded

    assert await operator_commands.restore(tmp_path / "anything", confirmed=False) == 2

    assert "--yes" in capsys.readouterr().err
    assert await repo.get_memory_by_id(user_id, originals[0].id) == originals[0]


async def test_restore_of_a_missing_backup_reports_it_and_exits_1(seeded, tmp_path, capsys):
    assert await operator_commands.restore(tmp_path / "nothing-here", confirmed=True) == 1

    assert "Error: Backup not found" in capsys.readouterr().err


async def test_export_then_import_report_what_they_moved(seeded, tmp_path, capsys):
    path = tmp_path / "export.jsonl"

    assert await operator_commands.export_memories(path) == 0

    assert "Exported 3 memories and 1 links of 1 tenants" in capsys.readouterr().out
    assert json.loads(path.read_text().splitlines()[0])["kind"] == "header"

    assert await operator_commands.import_memories(path) == 0

    assert capsys.readouterr().out.strip() == (
        f"Imported 0 memories and 0 links of 1 tenants from {path} (0 lines skipped)"
    )


async def test_the_export_holds_the_memories_of_every_tenant(seeded, tmp_path, capsys):
    repo, _user_id, _originals = seeded
    await repo.create_memory(uuid4(), MemoryCreate(
        title="Another tenant", content="Content", context="Context", keywords=["k"], tags=["t"], importance=5,
    ))

    assert await operator_commands.export_memories(tmp_path / "all.jsonl") == 0

    assert "Exported 4 memories and 1 links of 2 tenants" in capsys.readouterr().out


async def test_importing_a_file_that_is_not_an_export_exits_1(seeded, tmp_path, capsys):
    path = tmp_path / "other.jsonl"
    path.write_text('{"format": "something-else"}\n')

    assert await operator_commands.import_memories(path) == 1

    assert "not an Axmem export" in capsys.readouterr().err


async def test_check_of_one_node_reports_its_points_and_agreement(seeded, capsys):
    assert await operator_commands.check() == 0

    out = capsys.readouterr().out
    assert f"{settings.QDRANT_URL}: 4 points" in out  # three memories and one link
    assert "The nodes agree." in out


@pytest.mark.parametrize("divergent_count", [2, operator_commands.REPORT_LIMIT + 3])
async def test_check_lists_the_differing_points_up_to_a_limit_and_exits_1(monkeypatch, capsys, divergent_count):
    report = ConsistencyReport(
        nodes=[NodeReport(url="http://a", points=5), NodeReport(url="http://b", error="TimeoutError")],
        divergent=[f"collection:{index}" for index in range(divergent_count)],
    )

    class Service:
        async def check(self):
            return report

    async def with_service(run):
        return await run(Service())

    monkeypatch.setattr(operator_commands, "_with_service", with_service)

    assert await operator_commands.check() == 1

    out = capsys.readouterr().out
    hidden = divergent_count - operator_commands.REPORT_LIMIT
    assert "http://a: 5 points" in out
    assert "http://b: unreadable (TimeoutError)" in out
    assert out.count("  differs: ") == min(divergent_count, operator_commands.REPORT_LIMIT)
    assert (f"and {hidden} more" in out) == (hidden > 0)
    assert f"do not agree on {divergent_count} points" in out


@pytest.mark.parametrize(
    ("argv", "handler", "arguments", "keywords"),
    [
        (["backup"], "backup", (), {"force": False}),
        (["backup", "--force"], "backup", (), {"force": True}),
        (["restore", "dir"], "restore", (Path("dir"),), {"confirmed": False}),
        (["restore", "dir", "--yes"], "restore", (Path("dir"),), {"confirmed": True}),
        (["export", "file"], "export_memories", (Path("file"),), {}),
        (["import", "file"], "import_memories", (Path("file"),), {}),
        (["qdrant-check"], "check", (), {}),
    ],
)
async def test_each_command_name_routes_to_its_handler(monkeypatch, argv, handler, arguments, keywords):
    called = []

    async def record(*args, **kwargs):
        called.append((args, kwargs))
        return 7

    monkeypatch.setattr(operator_commands, handler, record)

    assert await operator_commands.run_operator_command(build_parser().parse_args(argv)) == 7

    assert called == [(arguments, keywords)]


def test_the_parser_hands_operator_commands_to_the_operator_runner(isolated_qdrant, capsys):
    """No collections exist yet: the command fails the way an operator sees it, with exit code 1 and the cause."""
    code = dispatch(["qdrant-check"], serve_runner=None, reembed_runner=None)

    assert code == 1
    assert "does not exist yet" in capsys.readouterr().err
