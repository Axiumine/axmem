"""PostgreSQL start-up: a failing Alembic upgrade is logged and stops initialisation.

Runs against the shared test database; the failure is injected into the upgrade command, so
the schema other tests use is never modified (the surrounding transaction is rolled back).
"""
import logging

import pytest

from alembic import command
from app.repositories.postgres.postgres_adapter import PostgresDatabaseAdapter
from tests.e2e.pg_repository_world_e2e_helpers import captured_logger

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

ADAPTER_LOGGER = "app.repositories.postgres.postgres_adapter"


async def test_init_db_logs_and_reraises_a_failed_migration(db_adapter, monkeypatch, caplog):
    failure = RuntimeError("migration exploded")

    def failing_upgrade(config, revision):
        raise failure

    monkeypatch.setattr(command, "upgrade", failing_upgrade)
    adapter = PostgresDatabaseAdapter()
    try:
        with captured_logger(caplog, ADAPTER_LOGGER, logging.ERROR) as records, \
                pytest.raises(RuntimeError) as raised:
            await adapter.init_db()
    finally:
        await adapter.dispose()

    assert raised.value is failure
    (record,) = records
    assert record.getMessage() == "Alembic migration failed: migration exploded"
    assert record.exc_info is not None
    assert record.exc_info[1] is failure
