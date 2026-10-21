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
from pydantic import SecretStr

import app.config.logging_config as logging_config
from app import bootstrap
from app.config.settings import settings
from app.exceptions import QdrantStartupError
from app.models.memory_models import MemoryCreate
from app.protocols.maintenance_protocol import ConsistencyReport, NodeReport
from app.routes.cli import operator_commands
from app.routes.cli.parser import build_parser, dispatch
from tests.backup_restore_cases import seed_memories
from tests.qdrant_support import drop_prefix, unique_prefix
from tests.relayout_support import stop_after_removing_the_old_links


@pytest.fixture(autouse=True)
def logging_calls(monkeypatch, tmp_path_factory):
    """Record what the commands ask of the logging set-up, so a test run installs no handlers and no exit hook."""
    tmp_path_factory.getbasetemp()  # also made for the session by ``base_temp_made_first`` of tests/conftest.py
    configured, registered = [], []

    def register(func, *args, **kwargs):
        """``atexit.register`` records the function; the arguments of someone else's registration cannot break it."""
        registered.append(func)
        return func

    monkeypatch.setattr(logging_config, "configure_logging", lambda **kwargs: configured.append(kwargs))
    monkeypatch.setattr(atexit, "register", register)
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


async def test_restore_removes_the_staging_collection_of_a_re_embed_that_did_not_finish(seeded, isolated_stack, capsys):
    names = isolated_stack.schema.names
    assert await operator_commands.backup() == 0
    saved = Path(capsys.readouterr().out.splitlines()[0].removeprefix("Backup saved: "))
    await isolated_stack.schema.create_links_collection(names.links_staging)  # links waiting for a move that never finished
    with pytest.raises(QdrantStartupError, match="_links_relayout holds the links"):
        await isolated_stack.schema.ensure()

    assert await operator_commands.restore(saved, confirmed=True) == 0

    assert not await isolated_stack.schema.exists(names.links_staging)  # the restored links are the ones to serve
    await isolated_stack.schema.ensure()


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


async def test_an_export_goes_to_the_file_it_is_given_and_never_into_the_backup_directory(seeded, tmp_path, capsys):
    """BACKUP_DIR is where `backup` writes; `export` writes the one file named on its command line (the docs say so)."""
    target = tmp_path / "somewhere-else" / "export.jsonl"
    target.parent.mkdir()

    assert await operator_commands.export_memories(target) == 0

    assert json.loads(target.read_text().splitlines()[0])["kind"] == "header"
    assert f"to {target}" in capsys.readouterr().out
    assert not (tmp_path / "backups").exists()  # nothing was created in the backup directory, not even the directory


async def test_import_creates_the_collections_of_a_qdrant_that_has_none(seeded, tmp_path, capsys, monkeypatch):
    """The documented recovery after the loss of a node: a new Qdrant, no server started yet."""
    path = tmp_path / "export.jsonl"
    assert await operator_commands.export_memories(path) == 0
    capsys.readouterr()
    monkeypatch.setattr(settings, "QDRANT_COLLECTION_PREFIX", unique_prefix())
    try:
        assert await operator_commands.import_memories(path) == 0
        assert capsys.readouterr().out.startswith("Imported 3 memories and 1 links of 1 tenants")
    finally:
        drop_prefix(settings.QDRANT_URL, settings.QDRANT_API_KEY.get_secret_value(), settings.QDRANT_COLLECTION_PREFIX)


@pytest.mark.parametrize("command", ["export", "backup"])
async def test_a_qdrant_without_the_collections_is_reported_not_crashed_on(seeded, tmp_path, capsys, monkeypatch, command):
    monkeypatch.setattr(settings, "QDRANT_COLLECTION_PREFIX", unique_prefix())

    code = await (
        operator_commands.export_memories(tmp_path / "export.jsonl") if command == "export" else operator_commands.backup()
    )

    assert code == 1
    assert "does not exist yet; start the server once to create it" in capsys.readouterr().err
    assert not (tmp_path / "export.jsonl").exists()


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


def report_of(monkeypatch, report: ConsistencyReport) -> None:
    class Service:
        async def check(self):
            return report

    async def with_service(run):
        return await run(Service())

    monkeypatch.setattr(operator_commands, "_with_service", with_service)


@pytest.mark.parametrize("divergent_count", [2, operator_commands.REPORT_LIMIT + 3])
async def test_check_lists_the_differing_points_up_to_a_limit_and_exits_1(monkeypatch, capsys, divergent_count):
    report_of(monkeypatch, ConsistencyReport(
        nodes=[NodeReport(url="http://a", points=5), NodeReport(url="http://b", error="TimeoutError")],
        divergent=[f"collection:{index}" for index in range(divergent_count)],
    ))

    assert await operator_commands.check() == 1

    out = capsys.readouterr().out
    hidden = divergent_count - operator_commands.REPORT_LIMIT
    assert "http://a: 5 points" in out
    assert "http://b: unreadable (TimeoutError)" in out
    assert out.count("  differs: ") == min(divergent_count, operator_commands.REPORT_LIMIT)
    assert (f"and {hidden} more" in out) == (hidden > 0)
    assert f"The nodes that answered do not agree on {divergent_count} points." in out
    assert "1 of 2 nodes could not be read" in out
    assert out.rstrip().endswith("Nothing was changed.")


async def test_check_of_nodes_that_all_answer_but_differ_does_not_blame_a_node(monkeypatch, capsys):
    report_of(monkeypatch, ConsistencyReport(
        nodes=[NodeReport(url="http://a", points=5), NodeReport(url="http://b", points=4)],
        divergent=["collection:one"],
    ))

    assert await operator_commands.check() == 1

    out = capsys.readouterr().out
    assert "  differs: collection:one" in out
    assert "The nodes that answered do not agree on 1 points." in out
    assert "could not be read" not in out


async def test_check_with_an_unreadable_node_and_nothing_to_compare_says_so_and_does_not_report_a_disagreement(monkeypatch, capsys):
    report_of(monkeypatch, ConsistencyReport(
        nodes=[NodeReport(url="http://a", points=5), NodeReport(url="http://b", error="ConnectError")],
    ))

    assert await operator_commands.check() == 1

    out = capsys.readouterr().out
    assert "http://b: unreadable (ConnectError)" in out
    assert "1 of 2 nodes could not be read, so the nodes were not compared in full: start them and run the check again." in out
    assert "do not agree" not in out
    assert "differs" not in out
    assert out.rstrip().endswith("Nothing was changed.")


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


# ---- failures an operator can fix are one line and exit code 1, bugs keep their traceback ----------------


async def test_an_unwritable_backup_directory_is_reported_not_crashed_on(seeded, tmp_path, capsys, monkeypatch):
    (tmp_path / "a-file").write_text("not a directory")
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "a-file" / "backups"))

    assert await operator_commands.backup() == 1

    err = capsys.readouterr().err
    assert err.startswith("Error: Not a directory: ")
    assert len(err.splitlines()) == 1


async def test_an_export_to_a_directory_that_does_not_exist_is_reported_not_crashed_on(seeded, tmp_path, capsys):
    assert await operator_commands.export_memories(tmp_path / "missing" / "export.jsonl") == 1

    err = capsys.readouterr().err
    assert err.startswith("Error: No such file or directory: ")
    assert not list(tmp_path.glob("missing*"))


@pytest.mark.parametrize("command", ["export", "backup"])
async def test_an_api_key_that_qdrant_refuses_is_reported_with_the_setting_to_check(
    seeded, tmp_path, capsys, monkeypatch, command,
):
    """Qdrant answers its version without a key, so the version check passes and the first real request is refused."""
    monkeypatch.setattr(settings, "QDRANT_API_KEY", SecretStr("not-the-key"))

    code = await (
        operator_commands.export_memories(tmp_path / "export.jsonl") if command == "export" else operator_commands.backup()
    )

    assert code == 1
    assert capsys.readouterr().err == "Error: Qdrant refused the request (HTTP 401): check QDRANT_API_KEY\n"


async def test_a_failure_that_is_not_the_operators_to_fix_keeps_its_traceback_and_closes_the_stack(isolated_qdrant):
    closed = []

    async def bug(_service):
        raise KeyError("a bug")

    real_create = bootstrap.create_qdrant_stack

    def create_and_watch():
        stack = real_create()
        real_close = stack.close

        async def close():
            closed.append(True)
            await real_close()

        stack.close = close
        return stack

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(bootstrap, "create_qdrant_stack", create_and_watch)
        with pytest.raises(KeyError, match="a bug"):
            await operator_commands._with_service(bug)

    assert closed == [True]


# ---- links that wait for a re-embed to finish moving them -------------------------------------------------------------


@pytest.fixture
async def links_waiting_in_staging(seeded, isolated_stack, monkeypatch):
    """What a re-embed leaves that stopped after it removed the old links collection: the links wait in staging."""
    repo, *_ = seeded
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    await stop_after_removing_the_old_links(isolated_stack, repo)
    return isolated_stack.schema.names


@pytest.mark.parametrize("command", ["backup", "export"])
async def test_a_command_that_copies_the_links_away_refuses_while_they_wait_in_staging(
    links_waiting_in_staging, command, tmp_path, capsys,
):
    target = tmp_path / "export.jsonl"

    exit_code = await (operator_commands.backup() if command == "backup" else operator_commands.export_memories(target))

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error: " in captured.err and "_links_relayout holds the links" in captured.err
    assert "Run `axmem re-embed` again" in captured.err
    assert not target.exists()  # no set that leaves the links out
    assert not (tmp_path / "backups").exists()


async def test_import_refuses_while_the_links_wait_in_staging_and_does_not_make_an_empty_links_collection(
    seeded, isolated_stack, tmp_path, capsys, monkeypatch,
):
    repo, *_ = seeded
    path = tmp_path / "export.jsonl"
    assert await operator_commands.export_memories(path) == 0
    capsys.readouterr()
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    await stop_after_removing_the_old_links(isolated_stack, repo)

    assert await operator_commands.import_memories(path) == 1

    assert "_links_relayout holds the links" in capsys.readouterr().err
    assert not await isolated_stack.schema.exists(isolated_stack.schema.names.links)
