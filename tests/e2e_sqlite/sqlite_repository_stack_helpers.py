"""Shared building blocks for tests that drive the SQLite repositories directly.

``open_repository_stack`` wires the real in-memory SQLite adapter (sqlite-vec tables and
migrations included) to the entity, memory, project, code artifact and
document repositories, plus two users so ownership checks can be exercised.

``FaultyAdapter`` wraps that adapter and makes chosen session calls fail the way a locked
or constraint-violating database would, so error-handling paths can be driven without
touching the repositories' code.
"""
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError, OperationalError

from app.config.settings import settings
from app.models.user_models import UserCreate
from app.repositories.sqlite.code_artifact_repository import (
    SqliteCodeArtifactRepository,
)
from app.repositories.sqlite.document_repository import SqliteDocumentRepository
from app.repositories.sqlite.entity_repository import SqliteEntityRepository
from app.repositories.sqlite.memory_repository import SqliteMemoryRepository
from app.repositories.sqlite.project_repository import SqliteProjectRepository
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from app.repositories.sqlite.user_repository import SqliteUserRepository


class FakeEmbeddings:
    """Deterministic embedding adapter: every text maps to the same vector."""

    async def generate_embedding(self, text: str) -> list[float]:
        return [0.01] * settings.EMBEDDING_DIMENSIONS


@dataclass
class RepositoryStack:
    """Repositories sharing one in-memory SQLite database."""

    adapter: SqliteDatabaseAdapter
    user_id: UUID
    other_user_id: UUID
    entities: SqliteEntityRepository
    memories: SqliteMemoryRepository
    projects: SqliteProjectRepository
    code_artifacts: SqliteCodeArtifactRepository
    documents: SqliteDocumentRepository


@asynccontextmanager
async def open_repository_stack(monkeypatch: Any) -> AsyncIterator[RepositoryStack]:
    """Open a fresh in-memory SQLite database with all repositories and two users."""
    monkeypatch.setattr(settings, "DATABASE", "SQLite")
    monkeypatch.setattr(settings, "SQLITE_MEMORY", True)
    adapter = SqliteDatabaseAdapter()
    await adapter.init_db()
    try:
        users = SqliteUserRepository(db_adapter=adapter)
        owner = await users.create_user(
            UserCreate(external_id="repo-owner", name="Owner", email="owner@example.test"),
        )
        other = await users.create_user(
            UserCreate(external_id="repo-other", name="Other", email="other@example.test"),
        )
        embeddings = FakeEmbeddings()
        yield RepositoryStack(
            adapter=adapter,
            user_id=owner.id,
            other_user_id=other.id,
            entities=SqliteEntityRepository(db_adapter=adapter),
            memories=SqliteMemoryRepository(db_adapter=adapter, embedding_adapter=embeddings),
            projects=SqliteProjectRepository(db_adapter=adapter),
            code_artifacts=SqliteCodeArtifactRepository(db_adapter=adapter),
            documents=SqliteDocumentRepository(db_adapter=adapter),
        )
    finally:
        await adapter.dispose()


def locked_database_error() -> OperationalError:
    """The error SQLite raises when another connection holds the write lock."""
    return OperationalError("UPDATE x SET y = 1", {}, Exception("database is locked"))


def foreign_key_violation() -> IntegrityError:
    """An integrity failure that is not the duplicate-relationship unique violation."""
    return IntegrityError(
        "INSERT INTO entity_relationships", {}, Exception("FOREIGN KEY constraint failed"),
    )


class _FaultySession:
    """Forwards to a real session except for the named calls, which raise."""

    def __init__(self, session: Any, faults: Mapping[str, BaseException]) -> None:
        self._session = session
        self._faults = faults

    def __getattr__(self, name: str) -> Any:
        if name in self._faults:
            error = self._faults[name]

            async def fail(*_args: Any, **_kwargs: Any) -> None:
                raise error

            return fail
        return getattr(self._session, name)


class FaultyAdapter:
    """Database adapter whose sessions fail on the named session calls."""

    def __init__(self, real: SqliteDatabaseAdapter, faults: Mapping[str, BaseException]) -> None:
        self._real = real
        self._faults = faults

    def session(self, user_id: UUID) -> Any:
        return self._wrap(self._real.session(user_id))

    def system_session(self) -> Any:
        return self._wrap(self._real.system_session())

    @asynccontextmanager
    async def _wrap(self, real_session_context: Any) -> AsyncIterator[_FaultySession]:
        async with real_session_context as session:
            yield _FaultySession(session, self._faults)


def locked_adapter(real: SqliteDatabaseAdapter) -> FaultyAdapter:
    """Adapter on which every statement, flush and commit fails with 'database is locked'."""
    error = locked_database_error()
    return FaultyAdapter(real, {"execute": error, "flush": error, "commit": error})
