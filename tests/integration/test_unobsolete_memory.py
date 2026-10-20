"""Integration tests for unobsolete_memory restore path."""
import asyncio
import hashlib
import random
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.config.settings import settings
from app.exceptions import InvalidStateTransitionError, NotFoundError
from app.models.memory_models import MemoryCreate, MemoryQueryRequest
from app.repositories.sqlite.memory_repository import SqliteMemoryRepository
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from app.services.memory_service import MemoryService


@pytest.mark.asyncio
async def test_unobsolete_restores_query_visibility_and_clears_fields(test_memory_service):
    user_id = uuid4()
    mem_data = MemoryCreate(
        title="Restore me unique-title-ff86",
        content="Memory to obsolete then restore",
        context="unobsolete test",
        keywords=["restore", "ff86"],
        tags=["stub"],
        importance=5,
    )
    mem, _ = await test_memory_service.create_memory(user_id=user_id, memory_data=mem_data)
    await test_memory_service.mark_memory_obsolete(
        user_id=user_id,
        memory_id=mem.id,
        reason="test obsolete",
        superseded_by=None,
    )

    before = await test_memory_service.query_memory(
        user_id=user_id,
        memory_query=MemoryQueryRequest(
            query="unique-title-ff86",
            query_context="unobsolete visibility check",
            k=5,
        ),
    )
    assert all(m.id != mem.id for m in before.primary_memories)

    ok = await test_memory_service.unobsolete_memory(user_id=user_id, memory_id=mem.id)
    assert ok is True

    refreshed = await test_memory_service.get_memory(user_id=user_id, memory_id=mem.id)
    assert refreshed is not None
    assert refreshed.is_obsolete is False
    assert refreshed.obsolete_reason is None
    assert refreshed.superseded_by is None
    assert refreshed.obsoleted_at is None

    after = await test_memory_service.query_memory(
        user_id=user_id,
        memory_query=MemoryQueryRequest(
            query="unique-title-ff86",
            query_context="unobsolete visibility check",
            k=5,
        ),
    )
    assert any(m.id == mem.id for m in after.primary_memories)


@pytest.mark.asyncio
async def test_unobsolete_unknown_id_raises_not_found(test_memory_service):
    user_id = uuid4()
    with pytest.raises(NotFoundError):
        await test_memory_service.unobsolete_memory(user_id=user_id, memory_id=999_999)


@pytest.mark.asyncio
async def test_unobsolete_active_memory_raises_invalid_state_transition(test_memory_service):
    user_id = uuid4()
    mem_data = MemoryCreate(
        title="Still active",
        content="Never obsolete",
        context="unobsolete negative test",
        keywords=["active"],
        tags=["stub"],
        importance=5,
    )
    mem, _ = await test_memory_service.create_memory(user_id=user_id, memory_data=mem_data)

    with pytest.raises(InvalidStateTransitionError, match="is not obsolete"):
        await test_memory_service.unobsolete_memory(user_id=user_id, memory_id=mem.id)


async def _restore_twice_concurrently(service, user_id, memory_id):
    """Fire two overlapping unobsolete_memory calls and return both outcomes."""
    return await asyncio.gather(
        service.unobsolete_memory(user_id=user_id, memory_id=memory_id),
        service.unobsolete_memory(user_id=user_id, memory_id=memory_id),
        return_exceptions=True,
    )


def _split_restore_outcomes(results):
    """Split concurrent restore outcomes into (successes, invalid-state errors)."""
    successes = [r for r in results if r is True]
    invalid = [r for r in results if isinstance(r, InvalidStateTransitionError)]
    return successes, invalid


@pytest.mark.asyncio
async def test_unobsolete_concurrent_restore_one_success_one_invalid_state(test_memory_service):
    """Two overlapping restores: one success, one invalid-state."""
    service = test_memory_service
    user_id = uuid4()
    mem_data = MemoryCreate(
        title="Concurrent restore race",
        content="Obsolete memory restored concurrently",
        context="unobsolete concurrency test",
        keywords=["concurrent", "restore"],
        tags=["stub"],
        importance=5,
    )
    mem, _ = await service.create_memory(user_id=user_id, memory_data=mem_data)
    await service.mark_memory_obsolete(
        user_id=user_id,
        memory_id=mem.id,
        reason="concurrency obsolete",
        superseded_by=None,
    )

    results = await _restore_twice_concurrently(service, user_id, mem.id)

    successes, invalid = _split_restore_outcomes(results)
    assert len(successes) == 1
    assert len(invalid) == 1


class _StaticEmbeddingAdapter:
    """Deterministic offline embeddings for SQLite integration checks."""

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    async def generate_embedding(self, text: str) -> list[float]:
        seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        vec = [rng.random() for _ in range(self.dimensions)]
        magnitude = sum(x * x for x in vec) ** 0.5 or 1.0
        return [x / magnitude for x in vec]


async def _sqlite_user(db_adapter, user_id):
    now = datetime.now(UTC).isoformat()
    async with db_adapter.system_session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, external_id, name, email, created_at, updated_at) "
                "VALUES (:id, :external_id, :name, :email, :created_at, :updated_at)",
            ),
            {
                "id": str(user_id),
                "external_id": f"ext-{user_id}",
                "name": "Test User",
                "email": "test@example.com",
                "created_at": now,
                "updated_at": now,
            },
        )


@asynccontextmanager
async def _sqlite_memory_stack():
    """Yield (db_adapter, repo, service) on in-memory SQLite, restoring settings on exit."""
    original_sqlite_memory = settings.SQLITE_MEMORY
    original_database = settings.DATABASE
    original_dims = settings.EMBEDDING_DIMENSIONS

    settings.DATABASE = "SQLite"
    settings.SQLITE_MEMORY = True
    settings.EMBEDDING_DIMENSIONS = 384

    db_adapter = SqliteDatabaseAdapter()
    await db_adapter.init_db()
    embedding_adapter = _StaticEmbeddingAdapter(dimensions=384)
    repo = SqliteMemoryRepository(
        db_adapter=db_adapter,
        embedding_adapter=embedding_adapter,
        rerank_adapter=None,
    )
    service = MemoryService(repo)

    try:
        yield db_adapter, repo, service
    finally:
        try:
            await db_adapter.dispose()
        except Exception:  # noqa: BLE001
            pass
        settings.DATABASE = original_database
        settings.SQLITE_MEMORY = original_sqlite_memory
        settings.EMBEDDING_DIMENSIONS = original_dims


@pytest.mark.asyncio
async def test_unobsolete_concurrent_restore_sqlite_atomic():
    """SQLite conditional UPDATE: concurrent restores yield one winner."""
    async with _sqlite_memory_stack() as (db_adapter, repo, service):
        user_id = uuid4()
        await _sqlite_user(db_adapter, user_id)
        mem = await repo.create_memory(
            user_id=user_id,
            memory=MemoryCreate(
                title="SQLite concurrent restore",
                content="Obsolete then concurrent unobsolete",
                context="sqlite concurrency",
                keywords=["sqlite", "restore"],
                tags=["stub"],
                importance=5,
            ),
        )
        await repo.mark_obsolete(user_id, mem.id, "test", None)

        results = await _restore_twice_concurrently(service, user_id, mem.id)

        successes, invalid = _split_restore_outcomes(results)
        assert len(successes) == 1
        assert len(invalid) == 1
