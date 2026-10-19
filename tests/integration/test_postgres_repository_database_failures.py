"""PostgreSQL repositories: database failures are logged and re-raised unchanged.

Code artifacts and documents share one failure contract. Whatever operation fails
because the database cannot be used, the repository writes an error record naming the
operation (and the user and record involved) and then lets the original exception reach the
caller, so the service and route layers can turn it into a server error. No database is
needed: the repositories run against an adapter whose sessions fail on entry.
"""
import logging
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError

from app.models.code_artifact_models import CodeArtifactCreate, CodeArtifactUpdate
from app.models.document_models import DocumentCreate, DocumentUpdate
from app.repositories.postgres.code_artifact_repository import (
    PostgresCodeArtifactRepository,
)
from app.repositories.postgres.document_repository import PostgresDocumentRepository
from tests.integration.postgres_failure_injection import (
    UnreachableDatabaseAdapter,
    connection_lost_error,
)

USER_ID = UUID("00000000-0000-4000-8000-0000000000aa")
RECORD_ID = 41

ARTIFACT = CodeArtifactCreate(title="t", description="d", code="x = 1", language="python")
DOCUMENT = DocumentCreate(title="t", description="d", content="body")

# (repository class, method, keyword arguments besides user_id, expected error message)
FAILING_OPERATIONS = [
    pytest.param(
        PostgresCodeArtifactRepository, "create_code_artifact",
        {"artifact_data": ARTIFACT}, "Failed to create code artifact",
        id="code-artifact-create",
    ),
    pytest.param(
        PostgresCodeArtifactRepository, "get_code_artifact_by_id",
        {"artifact_id": RECORD_ID}, f"Failed to get code artifact {RECORD_ID}",
        id="code-artifact-get",
    ),
    pytest.param(
        PostgresCodeArtifactRepository, "list_code_artifacts",
        {}, "Failed to list code artifacts",
        id="code-artifact-list",
    ),
    pytest.param(
        PostgresCodeArtifactRepository, "update_code_artifact",
        {"artifact_id": RECORD_ID, "artifact_data": CodeArtifactUpdate(title="n")},
        f"Failed to update code artifact {RECORD_ID}",
        id="code-artifact-update",
    ),
    pytest.param(
        PostgresCodeArtifactRepository, "delete_code_artifact",
        {"artifact_id": RECORD_ID}, f"Failed to delete code artifact {RECORD_ID}",
        id="code-artifact-delete",
    ),
    pytest.param(
        PostgresDocumentRepository, "create_document",
        {"document_data": DOCUMENT}, "Failed to create document",
        id="document-create",
    ),
    pytest.param(
        PostgresDocumentRepository, "get_document_by_id",
        {"document_id": RECORD_ID}, f"Failed to get document {RECORD_ID}",
        id="document-get",
    ),
    pytest.param(
        PostgresDocumentRepository, "list_documents",
        {}, "Failed to list documents",
        id="document-list",
    ),
    pytest.param(
        PostgresDocumentRepository, "update_document",
        {"document_id": RECORD_ID, "document_data": DocumentUpdate(title="n")},
        f"Failed to update document {RECORD_ID}",
        id="document-update",
    ),
    pytest.param(
        PostgresDocumentRepository, "delete_document",
        {"document_id": RECORD_ID}, f"Failed to delete document {RECORD_ID}",
        id="document-delete",
    ),
]


@pytest.fixture
def error_records(caplog, monkeypatch):
    """Capture error records even when an earlier migration run disabled the app loggers."""
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("app.repositories.postgres."):
            monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    caplog.set_level(logging.ERROR)
    return caplog


@pytest.mark.parametrize(("repository_class", "method", "arguments", "message"), FAILING_OPERATIONS)
async def test_unreachable_database_is_logged_with_the_operation_and_reraised(
    error_records, repository_class, method, arguments, message,
):
    injected = connection_lost_error()
    repository = repository_class(UnreachableDatabaseAdapter(injected))

    with pytest.raises(OperationalError) as raised:
        await getattr(repository, method)(user_id=USER_ID, **arguments)

    assert raised.value is injected
    (record,) = [r for r in error_records.records if r.levelno >= logging.ERROR]
    assert record.name == repository_class.__module__
    assert record.getMessage() == message
    assert record.user_id == str(USER_ID)

