"""Real PostgreSQL backup and automatic CLI recovery, without model downloads."""

import pytest

from app.bootstrap import create_repositories
from app.config.settings import settings
from tests.backup_restore_cases import BackupRestoreCases, BackupTestEmbeddings

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
async def backup_database(db_adapter, clean_database, postgres_backup_tools, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SQLITE_PATH", str(tmp_path / "backup-test.db"))
    try:
        yield db_adapter, create_repositories(db_adapter, BackupTestEmbeddings(), None)
    finally:
        await db_adapter.dispose()


class TestPostgresBackupRestore(BackupRestoreCases):
    pass
