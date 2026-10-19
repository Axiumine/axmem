"""PostgreSQL adapter: a failed schema migration is logged and aborts initialisation.

``init_db`` takes the migration advisory lock, makes sure the vector extension exists and
runs the Alembic upgrade on the same connection. When the upgrade fails the adapter logs
the reason and lets the original exception reach the caller, so the application does not
start on a half-migrated schema. The engine is replaced by a recording fake: no database.
"""
import logging

import pytest

from alembic import command
from app.repositories.postgres.postgres_adapter import (
    MIGRATION_LOCK_KEY,
    PostgresDatabaseAdapter,
)

MODULE = "app.repositories.postgres.postgres_adapter"


class RecordingConnection:
    def __init__(self):
        self.statements = []

    async def execute(self, statement, parameters=None):
        self.statements.append((str(statement), parameters))

    async def run_sync(self, function):
        return function(self)


class RecordingEngine:
    def __init__(self, connection):
        self._connection = connection

    def begin(self):
        return self

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


async def test_failed_alembic_upgrade_is_logged_and_reraised(monkeypatch, caplog):
    failure = RuntimeError("revision graph is broken")

    def failing_upgrade(config, revision):
        raise failure

    monkeypatch.setattr(command, "upgrade", failing_upgrade)
    monkeypatch.setattr(logging.getLogger(MODULE), "disabled", False)
    caplog.set_level(logging.ERROR)
    connection = RecordingConnection()
    adapter = PostgresDatabaseAdapter()
    monkeypatch.setattr(adapter, "_engine", RecordingEngine(connection))

    with pytest.raises(RuntimeError) as raised:
        await adapter.init_db()

    assert raised.value is failure
    (record,) = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert record.name == MODULE
    assert record.getMessage() == "Alembic migration failed: revision graph is broken"
    lock_sql, lock_parameters = connection.statements[0]
    assert "pg_advisory_xact_lock" in lock_sql
    assert lock_parameters == {"key": MIGRATION_LOCK_KEY}
    assert "CREATE EXTENSION IF NOT EXISTS vector" in connection.statements[1][0]
