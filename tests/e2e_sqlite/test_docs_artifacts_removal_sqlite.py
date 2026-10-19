"""The migration that dropped documents and code artifacts, on SQLite."""

import pytest

from app.config.settings import settings
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from tests.docs_artifacts_removal_cases import (
    TestDocumentsAndCodeArtifactsRemoval as TestDocumentsAndCodeArtifactsRemoval,
)


@pytest.fixture
async def docs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE", "SQLite")
    monkeypatch.setattr(settings, "SQLITE_MEMORY", False)
    monkeypatch.setattr(settings, "SQLITE_PATH", str(tmp_path / "docs-artifacts-removal.db"))
    adapter = SqliteDatabaseAdapter()
    await adapter.init_db()
    try:
        yield adapter
    finally:
        await adapter.dispose()
