"""Backup and failed CLI rebuild recovery against a real SQLite file."""

import pytest

from app.bootstrap import create_db_adapter, create_repositories
from app.config.settings import settings
from tests.backup_restore_cases import BackupRestoreCases


@pytest.fixture
async def backup_database(tmp_path, monkeypatch, tensorrt_embedder):
    monkeypatch.setattr(settings, "DATABASE", "SQLite")
    monkeypatch.setattr(settings, "SQLITE_MEMORY", False)
    monkeypatch.setattr(settings, "SQLITE_PATH", str(tmp_path / "backup-test.db"))
    adapter = create_db_adapter()
    try:
        await adapter.init_db()
        yield adapter, create_repositories(adapter, tensorrt_embedder, None)
    finally:
        await adapter.dispose()


class TestSqliteBackupRestore(BackupRestoreCases):
    pass
