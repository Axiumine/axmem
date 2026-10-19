"""Helpers for driving the SQLite repositories directly on a real in-memory database.

The repository tests in this suite skip the HTTP/MCP layers on purpose: services check
ownership and existence before they call a repository, so the repository's own behaviour for
unknown ids, empty patches and storage failures is only visible when it is called directly.
"""
import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config.settings import settings
from app.models.user_models import UserCreate
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from app.repositories.sqlite.user_repository import SqliteUserRepository

UNKNOWN_ID = 424_242


@asynccontextmanager
async def open_adapter(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SqliteDatabaseAdapter]:
    """A migrated in-memory SQLite adapter, disposed on exit."""
    monkeypatch.setattr(settings, "DATABASE", "SQLite")
    monkeypatch.setattr(settings, "SQLITE_MEMORY", True)
    adapter = SqliteDatabaseAdapter()
    try:
        await adapter.init_db()
        yield adapter
    finally:
        await adapter.dispose()


async def create_user(adapter: SqliteDatabaseAdapter, name: str = "Repository Test") -> UUID:
    """Persist a user through the user repository and return its internal id."""
    suffix = uuid4().hex
    user = await SqliteUserRepository(adapter).create_user(
        UserCreate(external_id=f"ext-{suffix}", name=name, email=f"{suffix}@example.test"),
    )
    return user.id


class _BrokenSession:
    """Async context manager whose entry fails, like a session that cannot reach the database."""

    def __init__(self, error: BaseException):
        self._error = error

    async def __aenter__(self) -> None:
        raise self._error

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def break_database(
    monkeypatch: pytest.MonkeyPatch,
    adapter: SqliteDatabaseAdapter,
    message: str = "disk I/O error",
) -> None:
    """Make every session of the adapter fail with a SQLite operational error."""
    error = sqlite3.OperationalError(message)

    def broken_session(user_id: UUID | None = None) -> _BrokenSession:
        return _BrokenSession(error)

    monkeypatch.setattr(adapter, "session", broken_session)
    monkeypatch.setattr(adapter, "system_session", lambda: _BrokenSession(error))


def capture_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    logger: logging.Logger,
) -> None:
    """Collect ERROR records of one repository logger.

    Alembic's fileConfig() (run by any test that migrates a database) disables every logger that
    already exists, so the logger is re-enabled explicitly.
    """
    monkeypatch.setattr(logger, "disabled", False)
    caplog.set_level(logging.ERROR, logger=logger.name)


def error_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.levelno >= logging.ERROR]


def record_extra(record: logging.LogRecord, key: str) -> Any:
    """A field passed through ``extra=`` on a log call."""
    return record.__dict__[key]
