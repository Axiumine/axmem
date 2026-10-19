"""The migration that dropped documents and code artifacts, on PostgreSQL."""

import pytest

from tests.docs_artifacts_removal_cases import (
    TestDocumentsAndCodeArtifactsRemoval as TestDocumentsAndCodeArtifactsRemoval,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def docs_db(db_adapter):
    return db_adapter
