"""SQLite adapter start-up: riding out a concurrent WAL switch and reporting a failed migration."""
import logging
import sqlite3
from types import SimpleNamespace

import pytest
from alembic.util.exc import CommandError

from app.config.settings import settings
from app.repositories.sqlite import sqlite_adapter
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from tests.e2e_sqlite.sqlite_repository_helpers import capture_errors, error_records

WAL_PRAGMA = "PRAGMA journal_mode=WAL"


class ScriptedConnection:
    """Raw connection whose first calls fail with the scripted errors, then succeed."""

    def __init__(self, *errors: str):
        self._errors = list(errors)
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)
        if self._errors:
            raise sqlite3.OperationalError(self._errors.pop(0))


@pytest.fixture
def clock(monkeypatch):
    """Replace the adapter module's clock: sleeping advances time instead of waiting."""
    state = SimpleNamespace(now=0.0, sleeps=[])

    def sleep(seconds: float) -> None:
        state.sleeps.append(seconds)
        state.now += seconds

    monkeypatch.setattr(
        sqlite_adapter, "time", SimpleNamespace(monotonic=lambda: state.now, sleep=sleep),
    )
    return state


def test_wal_switch_retries_while_another_connection_is_converting_the_file(clock):
    connection = ScriptedConnection("database is locked", "database is locked")

    sqlite_adapter._enable_wal(connection)

    assert connection.statements == [WAL_PRAGMA] * 3
    assert len(clock.sleeps) == 2


def test_wal_switch_gives_up_once_the_retry_window_has_passed(clock):
    connection = ScriptedConnection(*["database is locked"] * 1_000)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        sqlite_adapter._enable_wal(connection)

    assert clock.now >= sqlite_adapter._WAL_SWITCH_RETRY_SECONDS
    assert len(connection.statements) == len(clock.sleeps) + 1


def test_wal_switch_does_not_retry_errors_other_than_a_lock(clock):
    connection = ScriptedConnection("disk I/O error")

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        sqlite_adapter._enable_wal(connection)

    assert connection.statements == [WAL_PRAGMA]
    assert clock.sleeps == []


async def test_database_stamped_with_an_unknown_revision_fails_start_up_and_is_left_untouched(
    tmp_path, monkeypatch, caplog,
):
    """A database written by a newer build names a revision this build cannot find."""
    path = tmp_path / "forgetful.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        connection.execute("INSERT INTO alembic_version VALUES ('from_a_newer_build')")
    monkeypatch.setattr(settings, "DATABASE", "SQLite")
    monkeypatch.setattr(settings, "SQLITE_MEMORY", False)
    monkeypatch.setattr(settings, "SQLITE_PATH", str(path))
    capture_errors(monkeypatch, caplog, sqlite_adapter.logger)
    adapter = SqliteDatabaseAdapter()

    try:
        with pytest.raises(CommandError, match="from_a_newer_build"):
            await adapter.init_db()
    finally:
        await adapter.dispose()

    [record] = [r for r in error_records(caplog) if r.name == sqlite_adapter.logger.name]
    assert record.levelno == logging.ERROR
    assert record.getMessage().startswith("Alembic migration failed: ")
    assert "from_a_newer_build" in record.getMessage()
    assert record.exc_info is not None
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        stamped = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    assert tables == {"alembic_version"}
    assert stamped == [("from_a_newer_build",)]
