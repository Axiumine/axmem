"""The migration that dropped the files tables, on SQLite."""

import pytest

from app.config.settings import settings
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from tests.files_removal_cases import (
    TestFilesRemoval as TestFilesRemoval,
)


@pytest.fixture
async def files_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE", "SQLite")
    monkeypatch.setattr(settings, "SQLITE_MEMORY", False)
    monkeypatch.setattr(settings, "SQLITE_PATH", str(tmp_path / "files-removal.db"))
    adapter = SqliteDatabaseAdapter()
    await adapter.init_db()
    try:
        yield adapter
    finally:
        await adapter.dispose()
