"""The migration that dropped the files tables, on PostgreSQL."""

import pytest

from tests.files_removal_cases import (
    TestFilesRemoval as TestFilesRemoval,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def files_db(db_adapter):
    return db_adapter
