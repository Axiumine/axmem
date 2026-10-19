"""E2E tests (real PostgreSQL) for the document summary listing and tag filters.

PostgreSQL counterpart of
``tests/e2e_sqlite/test_sqlite_tag_filters_and_list_loading_sqlite.py``:

- the summary listing must not fetch ``documents.content``;
- the exact, case-sensitive ``tags`` contract that the SQLite backend now
  matches (``&&`` array overlap: Unicode, ``%``, ``_`` and quotes are literal).
"""
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event

from app.models.document_models import DocumentCreate
from app.models.user_models import UserCreate
from app.repositories.postgres.document_repository import PostgresDocumentRepository
from app.repositories.postgres.user_repository import PostgresUserRepository

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest_asyncio.fixture(loop_scope="session")
async def user_id(db_adapter):
    user = await PostgresUserRepository(db_adapter=db_adapter).create_user(
        UserCreate(
            external_id=f"ext-{uuid4()}",
            name="Summary Listing Tester",
            email="listing@example.com",
        ),
    )
    return user.id


class _StatementCapture:
    """Records SQL sent through the shared session-scoped engine."""

    def __init__(self, db_adapter):
        self._engine = db_adapter._engine.sync_engine
        self.statements: list[str] = []

    def _grab(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def __enter__(self):
        event.listen(self._engine, "before_cursor_execute", self._grab)
        return self

    def __exit__(self, *exc_info):
        event.remove(self._engine, "before_cursor_execute", self._grab)

    def selects_from(self, table: str) -> list[str]:
        return [
            s
            for s in self.statements
            if s.lstrip().upper().startswith("SELECT") and f"FROM {table}" in s
        ]


async def test_list_documents_does_not_select_content_column(db_adapter, user_id):
    repo = PostgresDocumentRepository(db_adapter=db_adapter)
    created = await repo.create_document(
        user_id,
        DocumentCreate(
            title="big",
            description="long document",
            content="x" * 5000,
            tags=["long"],
        ),
    )

    with _StatementCapture(db_adapter) as capture:
        summaries = await repo.list_documents(user_id)

    list_selects = capture.selects_from("documents")
    assert list_selects
    assert not any("documents.content" in s for s in list_selects), list_selects[0]
    assert [s.id for s in summaries] == [created.id]
    assert summaries[0].title == "big"
    assert summaries[0].size_bytes == 5000
    assert summaries[0].tags == ["long"]

    # The full record (with its content) is still retrievable
    full = await repo.get_document_by_id(user_id, created.id)
    assert full.content == "x" * 5000


async def test_document_tag_filter_is_exact_and_case_sensitive(db_adapter, user_id):
    """Reference behaviour that the SQLite backend must match."""
    repo = PostgresDocumentRepository(db_adapter=db_adapter)
    tag_sets = {
        "cafe": ["café"],
        "hyphen": ["bug-fix"],
        "underscore": ["bug_fix"],
        "pair": ["alpha", "beta"],
        "pipe": ['5" pipe'],
        "upper": ["Python"],
    }
    for title, tags in tag_sets.items():
        await repo.create_document(
            user_id,
            DocumentCreate(title=title, description="d", content="c", tags=tags),
        )

    async def titles(tags: list[str]) -> set[str]:
        return {d.title for d in await repo.list_documents(user_id, tags=tags)}

    assert await titles(["café"]) == {"cafe"}
    assert await titles(["bug_fix"]) == {"underscore"}
    assert await titles(["%"]) == set()
    assert await titles(['alpha","beta']) == set()
    assert await titles(['5" pipe']) == {"pipe"}
    assert await titles(["python"]) == set()
    assert await titles(["Python"]) == {"upper"}
