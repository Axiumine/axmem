"""Shared building blocks for tests that drive the SQLite repositories directly.

``open_repository_stack`` wires the real in-memory SQLite adapter (sqlite-vec tables and
migrations included) to the memory repository, plus two users so ownership
checks can be exercised. The embedder is the caller's (the real TensorRT one in the tests).
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.config.settings import settings
from app.models.user_models import UserCreate
from app.repositories.sqlite.memory_repository import SqliteMemoryRepository
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from app.repositories.sqlite.user_repository import SqliteUserRepository


@dataclass
class RepositoryStack:
    """Repositories sharing one in-memory SQLite database."""

    adapter: SqliteDatabaseAdapter
    user_id: UUID
    other_user_id: UUID
    memories: SqliteMemoryRepository


@asynccontextmanager
async def open_repository_stack(monkeypatch: Any, embeddings: Any) -> AsyncIterator[RepositoryStack]:
    """Open a fresh in-memory SQLite database with the repositories and two users."""
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
        yield RepositoryStack(
            adapter=adapter,
            user_id=owner.id,
            other_user_id=other.id,
            memories=SqliteMemoryRepository(db_adapter=adapter, embedding_adapter=embeddings),
        )
    finally:
        await adapter.dispose()

