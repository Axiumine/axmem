"""The re-embed run on a real SQLite file: vector storage, dry run and recovery.

Only the embedding provider is replaced. The service, repositories, backup tools and the
CLI entry point (in this process, or in a child process where a signal or a console
codec is involved) are the real ones.
"""

import argparse
import asyncio
import hashlib
import os
import signal
import sqlite3
import sys
from pathlib import Path

import pytest
from alembic.config import Config

import main
from alembic import command
from app.bootstrap import create_db_adapter, create_repositories
from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.models.user_models import UserCreate
from app.repositories.sqlite.memory_repository import SqliteMemoryRepository
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from app.services.re_embedding_service import ReEmbeddingService
from tests.backup_restore_cases import (
    BackupTestEmbeddings,
    assert_originals_restored,
    seed_memories,
)

ROOT = Path(__file__).resolve().parents[2]
OLD_DIMS = 4
NEW_DIMS = 6


class FakeEmbedder:
    """Deterministic embedder whose output width follows the (changeable) configuration."""

    async def generate_embedding(self, text):
        digest = hashlib.sha256(text.encode()).digest()
        return [(digest[i] / 255.0) + 0.01 for i in range(settings.EMBEDDING_DIMENSIONS)]


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    """A file-backed SQLite configuration, first used with OLD_DIMS-wide vectors."""
    path = tmp_path / "data" / "forgetful.db"
    monkeypatch.setattr(settings, "DATABASE", "SQLite")
    monkeypatch.setattr(settings, "SQLITE_MEMORY", False)
    monkeypatch.setattr(settings, "SQLITE_PATH", str(path))
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", OLD_DIMS)
    return path


@pytest.fixture
def quiet_cli(monkeypatch):
    """Run main._run_reembed in this process without reconfiguring the test's logging."""
    import app.bootstrap
    import app.config.logging_config

    monkeypatch.setattr(app.config.logging_config, "configure_logging", lambda **_: None)
    monkeypatch.setattr(main.atexit, "register", lambda *_: None)
    monkeypatch.setattr(app.bootstrap, "get_embedding_adapter", FakeEmbedder)


async def open_stack():
    """Open the database the way a freshly started process would, then wire repositories."""
    adapter = SqliteDatabaseAdapter()
    await adapter.init_db()
    embedder = FakeEmbedder()
    memories = SqliteMemoryRepository(
        db_adapter=adapter, embedding_adapter=embedder, rerank_adapter=None,
    )
    return adapter, memories


async def create_user(adapter):
    repos = create_repositories(adapter, FakeEmbedder(), None)
    user = await repos["user"].create_user(UserCreate(
        external_id="reembed-run-flow", name="Run Flow", email="run-flow@example.test",
    ))
    return user.id


def new_memory(title):
    return MemoryCreate(
        title=title, content=f"{title} content", context="run flow",
        keywords=["run"], tags=["flow"], importance=7,
    )


def vec_dimensions(path, table):
    """Width of a vec0 table, read from its definition without opening the application."""
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?", (table,),
        ).fetchone()
    finally:
        connection.close()
    return int(row[0].split("FLOAT[")[1].split("]")[0])


def use_new_dimensions(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", NEW_DIMS)


class TestEmptyDatabase:
    async def test_re_embed_all_resizes_vector_storage(self, db_file, monkeypatch):
        # Arrange: a database that never stored anything, then a wider embedding model.
        adapter, _ = await open_stack()
        await adapter.dispose()
        use_new_dimensions(monkeypatch)
        adapter, memories = await open_stack()
        try:
            assert vec_dimensions(db_file, "vec_memories") == OLD_DIMS  # init_db keeps it

            # Act
            result = await ReEmbeddingService(memories, FakeEmbedder()).re_embed_all()

            # Assert: storage matches the configuration, so the next insert succeeds.
            assert result.validation.all_passed
            assert vec_dimensions(db_file, "vec_memories") == NEW_DIMS
            user_id = await create_user(adapter)
            assert (await memories.create_memory(user_id, new_memory("after"))).id is not None
        finally:
            await adapter.dispose()

    async def test_cli_resizes_vector_storage_without_a_backup(
        self, db_file, monkeypatch, quiet_cli, capsys,
    ):
        adapter, _ = await open_stack()
        await adapter.dispose()
        use_new_dimensions(monkeypatch)

        await main._run_reembed(argparse.Namespace(batch_size=5, dry_run=False))

        assert "No memories to re-embed." in capsys.readouterr().out
        assert vec_dimensions(db_file, "vec_memories") == NEW_DIMS
        assert not list(db_file.parent.glob("*.bak-*"))  # nothing to protect


class TestDryRun:
    @pytest.fixture
    def older_schema(self, db_file):
        """A database one release behind, built the way that release's server built it."""
        async def build(revision):
            db_file.parent.mkdir(parents=True, exist_ok=True)
            config = Config(str(ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(ROOT / "alembic"))
            # env.py reads settings.SQLITE_PATH and starts its own event loop.
            await asyncio.to_thread(command.upgrade, config, revision)
        return build

    @staticmethod
    def schema(path):
        connection = sqlite3.connect(path)
        try:
            version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            )}
        finally:
            connection.close()
        return version, tables

    async def test_dry_run_does_not_migrate_the_schema(
        self, db_file, older_schema, quiet_cli, capsys,
    ):
        await older_schema("20260408_provenance_all")
        before = self.schema(db_file)

        await main._run_reembed(argparse.Namespace(batch_size=20, dry_run=True))

        out = capsys.readouterr().out
        assert "Memories to process: 0" in out
        assert "No changes were made." in out
        assert self.schema(db_file) == before
        assert "vec_memories" not in before[1]  # still no vector tables either

    async def test_dry_run_counts_a_database_from_an_older_release(
        self, db_file, older_schema, quiet_cli, capsys,
    ):
        await older_schema("20260315_files")  # several releases behind
        before = self.schema(db_file)

        await main._run_reembed(argparse.Namespace(batch_size=20, dry_run=True))

        assert "Would re-embed 0 memories" in capsys.readouterr().out
        assert self.schema(db_file) == before

    async def test_dry_run_does_not_create_a_missing_database(self, db_file, quiet_cli, capsys):
        await main._run_reembed(argparse.Namespace(batch_size=20, dry_run=True))

        assert "Memories to process: 0" in capsys.readouterr().out
        assert not db_file.exists()

    async def test_dry_run_reports_counts_and_leaves_vector_storage_alone(
        self, db_file, monkeypatch, quiet_cli, capsys,
    ):
        adapter, memories = await open_stack()
        user_id = await create_user(adapter)
        await memories.create_memory(user_id, new_memory("one"))
        await memories.create_memory(user_id, new_memory("two"))
        await adapter.dispose()
        use_new_dimensions(monkeypatch)

        await main._run_reembed(argparse.Namespace(batch_size=20, dry_run=True))

        out = capsys.readouterr().out
        assert "Memories to process: 2" in out
        assert "Would re-embed 2 memories with" in out
        assert vec_dimensions(db_file, "vec_memories") == OLD_DIMS
        assert not list(db_file.parent.glob("*.bak-*"))


# A child process runs the real CLI. Its embedding provider embeds the first memory, then
# either stops the run with an error whose text no legacy console can print, or parks the
# event loop so the test can deliver a signal in the middle of the migration.
DRIVER = r"""
import asyncio
import sys

import app.bootstrap
from app.config.settings import settings
from main import cli

mode = sys.argv[1]


class Provider:
    calls = 0

    async def generate_embedding(self, text):
        self.calls += 1
        vector = [0.0] * settings.EMBEDDING_DIMENSIONS
        vector[1] = 1.0  # differs from the seed vector, so a missing restore is visible
        if self.calls == 2:
            if mode == "unencodable":
                raise RuntimeError("速率限制 → retry later")
            print("READY_FOR_SIGNAL", flush=True)
            await asyncio.sleep(60)
        return vector


provider = Provider()
app.bootstrap.get_embedding_adapter = lambda: provider
sys.argv = ["forgetful", "re-embed", "--batch-size", "1"]
sys.exit(cli())
"""


@pytest.fixture
async def backup_database(db_file, monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 384)
    adapter = create_db_adapter()
    try:
        await adapter.init_db()
        yield adapter, create_repositories(adapter, BackupTestEmbeddings(), None)
    finally:
        await adapter.dispose()


def child_environment(encoding="utf-8"):
    env = os.environ.copy()
    for name in ("DATABASE", "SQLITE_PATH", "SQLITE_MEMORY", "EMBEDDING_DIMENSIONS"):
        env[name] = str(getattr(settings, name))
    env.update({
        "RERANKING_ENABLED": "false",
        "PYTHONIOENCODING": encoding,
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": str(ROOT),
    })
    return env


async def start_child(mode, encoding="utf-8"):
    return await asyncio.create_subprocess_exec(
        sys.executable, "-c", DRIVER, mode, cwd=ROOT, env=child_environment(encoding),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )


async def finish_child(process, encoding="utf-8", timeout=60):
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return stdout.decode(encoding, "replace"), stderr.decode(encoding, "replace")


class TestRecovery:
    @pytest.mark.parametrize("sent", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"])
    async def test_signal_during_the_run_restores_the_backup(self, backup_database, sent):
        # Arrange: three memories with vectors, then a child stuck on its second embedding.
        adapter, repos = backup_database
        user_id, originals = await seed_memories(repos)
        await adapter.dispose()
        process = await start_child("signal")
        started = []

        async def wait_until_migrating():
            while line := await process.stdout.readline():
                started.append(line.decode())
                if b"READY_FOR_SIGNAL" in line:
                    return

        await asyncio.wait_for(wait_until_migrating(), 90)
        assert any("[3/5] Updating vector schema" in line for line in started), started

        # Act: interrupt the migration after the vector table was already rebuilt.
        process.send_signal(sent)
        stdout, stderr = await finish_child(process)

        # Assert: the interrupt is reported, the backup restored, and the original data back.
        report = "".join(started) + stdout + stderr
        assert process.returncode == 1, report
        assert "Interrupted!" in stdout, report
        assert "Database returned to pre-migration state." in stdout, report
        assert "RuntimeError" not in stderr, report
        await assert_originals_restored(repos["memory"], user_id, originals)

    async def test_unencodable_error_text_does_not_prevent_the_restore(self, backup_database):
        # Arrange: a console that cannot print the provider's error message (Windows pipe).
        adapter, repos = backup_database
        user_id, originals = await seed_memories(repos)
        await adapter.dispose()
        process = await start_child("unencodable", encoding="cp1252")

        # Act
        stdout, stderr = await finish_child(process, encoding="cp1252")

        # Assert: recovery ran, the real error is reported, and the data is back.
        report = stdout + stderr
        assert process.returncode not in (0, None), report
        assert "UnicodeEncodeError" not in stderr, report
        assert "ERROR: \\u901f\\u7387\\u9650\\u5236 \\u2192 retry later" in stdout, report
        assert "Database returned to pre-migration state." in stdout, report
        await assert_originals_restored(repos["memory"], user_id, originals)


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="needs POSIX signals")
class TestStopHandlers:
    @pytest.fixture(params=["loop", "windows-fallback"])
    async def loop_kind(self, request, monkeypatch):
        if request.param == "windows-fallback":
            loop = asyncio.get_running_loop()

            def unsupported(*_):
                raise NotImplementedError

            monkeypatch.setattr(loop, "add_signal_handler", unsupported)
            monkeypatch.setattr(loop, "remove_signal_handler", unsupported)
        return request.param

    async def test_signal_reaches_the_loop_and_previous_handlers_return(self, loop_kind):
        original = signal.getsignal(signal.SIGUSR1)
        received = asyncio.Event()
        restore = main._install_stop_handlers(
            asyncio.get_running_loop(), received.set, signals=(signal.SIGUSR1,),
        )
        try:
            signal.raise_signal(signal.SIGUSR1)
            await asyncio.wait_for(received.wait(), 5)
        finally:
            restore()

        assert signal.getsignal(signal.SIGUSR1) == original
