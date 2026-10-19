"""Code artifact, document and file repositories on a real SQLite database.

The three repositories share one contract: a storage failure is logged with the caller's
context and re-raised unchanged, an update of an unknown row is a NotFoundError that is not
logged as a failure, and a rejected row never leaves partial state behind.
"""
import base64
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest

from app.exceptions import NotFoundError
from app.models.code_artifact_models import CodeArtifactCreate, CodeArtifactUpdate
from app.models.document_models import DocumentCreate, DocumentUpdate
from app.models.file_models import FileCreate, FileUpdate
from app.repositories.sqlite import (
    code_artifact_repository,
    document_repository,
    file_repository,
)
from tests.e2e_sqlite.sqlite_repository_helpers import (
    UNKNOWN_ID,
    break_database,
    capture_errors,
    create_user,
    error_records,
    open_adapter,
    record_extra,
)


@dataclass(frozen=True)
class Kind:
    """One attachment repository and the names of its five operations."""

    noun: str
    module: ModuleType
    repository: type
    new: Callable[[], Any]
    patch: Callable[[], Any]
    create: str
    get: str
    list: str
    update: str
    delete: str

    @property
    def plural(self) -> str:
        return f"{self.noun}s"


KINDS = {
    "code_artifact": Kind(
        noun="code artifact",
        module=code_artifact_repository,
        repository=code_artifact_repository.SqliteCodeArtifactRepository,
        new=lambda: CodeArtifactCreate(
            title="Retry helper", description="Retries a call", code="retry()", language="Python",
        ),
        patch=lambda: CodeArtifactUpdate(title="Retry helper v2"),
        create="create_code_artifact",
        get="get_code_artifact_by_id",
        list="list_code_artifacts",
        update="update_code_artifact",
        delete="delete_code_artifact",
    ),
    "document": Kind(
        noun="document",
        module=document_repository,
        repository=document_repository.SqliteDocumentRepository,
        new=lambda: DocumentCreate(
            title="Runbook", description="How to deploy", content="1. Build\n2. Ship",
        ),
        patch=lambda: DocumentUpdate(title="Runbook v2"),
        create="create_document",
        get="get_document_by_id",
        list="list_documents",
        update="update_document",
        delete="delete_document",
    ),
    "file": Kind(
        noun="file",
        module=file_repository,
        repository=file_repository.SqliteFileRepository,
        new=lambda: FileCreate(
            filename="notes.txt",
            description="Meeting notes",
            data=base64.b64encode(b"hello").decode(),
            mime_type="text/plain",
        ),
        patch=lambda: FileUpdate(description="Meeting notes, revised"),
        create="create_file",
        get="get_file_by_id",
        list="list_files",
        update="update_file",
        delete="delete_file",
    ),
}
KIND_IDS = list(KINDS)

# operation -> (arguments after user_id, failure message for the log)
OPERATIONS = {
    "create": lambda kind: ((kind.new(),), f"Failed to create {kind.noun}"),
    "get": lambda kind: ((UNKNOWN_ID,), f"Failed to get {kind.noun} {UNKNOWN_ID}"),
    "list": lambda kind: ((), f"Failed to list {kind.plural}"),
    "update": lambda kind: (
        (UNKNOWN_ID, kind.patch()), f"Failed to update {kind.noun} {UNKNOWN_ID}",
    ),
    "delete": lambda kind: ((UNKNOWN_ID,), f"Failed to delete {kind.noun} {UNKNOWN_ID}"),
}


@pytest.fixture
async def adapter(monkeypatch):
    async with open_adapter(monkeypatch) as opened:
        yield opened


@pytest.mark.parametrize("operation", list(OPERATIONS))
@pytest.mark.parametrize("kind_id", KIND_IDS)
async def test_storage_failure_is_logged_with_the_callers_context_and_reraised(
    adapter, monkeypatch, caplog, kind_id, operation,
):
    kind = KINDS[kind_id]
    repo = kind.repository(adapter)
    user_id = await create_user(adapter)
    arguments, expected_message = OPERATIONS[operation](kind)
    capture_errors(monkeypatch, caplog, kind.module.logger)
    break_database(monkeypatch, adapter, "disk I/O error")

    with pytest.raises(sqlite3.OperationalError, match="^disk I/O error$"):
        await getattr(repo, getattr(kind, operation))(user_id, *arguments)

    [record] = error_records(caplog)
    assert record.getMessage() == expected_message
    assert record.exc_info is not None
    assert record_extra(record, "user_id") == str(user_id)
    assert record_extra(record, "error") == "disk I/O error"


@pytest.mark.parametrize("kind_id", KIND_IDS)
async def test_update_of_an_unknown_row_raises_not_found_and_is_not_logged_as_a_failure(
    adapter, monkeypatch, caplog, kind_id,
):
    kind = KINDS[kind_id]
    repo = kind.repository(adapter)
    user_id = await create_user(adapter)
    capture_errors(monkeypatch, caplog, kind.module.logger)

    expected = f"{kind.noun.capitalize()} {UNKNOWN_ID} not found"
    with pytest.raises(NotFoundError, match=f"^{expected}$"):
        await getattr(repo, kind.update)(user_id, UNKNOWN_ID, kind.patch())

    assert error_records(caplog) == []


async def test_update_stores_the_language_in_lowercase(adapter):
    repo = code_artifact_repository.SqliteCodeArtifactRepository(adapter)
    user_id = await create_user(adapter)
    created = await repo.create_code_artifact(user_id, KINDS["code_artifact"].new())

    updated = await repo.update_code_artifact(
        user_id, created.id, CodeArtifactUpdate(language="TypeScript"),
    )

    assert updated.language == "typescript"
    persisted = await repo.get_code_artifact_by_id(user_id, created.id)
    assert persisted is not None
    assert persisted.language == "typescript"
    assert persisted.title == created.title
