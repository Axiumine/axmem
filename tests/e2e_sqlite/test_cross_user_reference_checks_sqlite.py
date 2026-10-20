"""SQLite E2E tests: the embeddings used for similarity searches must belong to the caller.

The SQLite memory repository read the source embedding for the similarity searches
without checking that the memory belongs to the caller. These tests drive the memory
repository with the real TensorRT embedder, including the owner control.
"""
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.memory_models import MemoryCreate
from app.repositories.sqlite.memory_repository import SqliteMemoryRepository
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from tests.embedding_doubles import similarity

# ============================================================================
# Memory repository: similarity searches must not use another user's embedding
# ============================================================================

DIMS = 384


@pytest.fixture
async def memory_repo_and_db(tensorrt_embedder):
    original = (settings.DATABASE, settings.SQLITE_MEMORY, settings.EMBEDDING_DIMENSIONS)
    settings.DATABASE = "SQLite"
    settings.SQLITE_MEMORY = True
    settings.EMBEDDING_DIMENSIONS = DIMS
    db_adapter = SqliteDatabaseAdapter()
    await db_adapter.init_db()
    repo = SqliteMemoryRepository(
        db_adapter=db_adapter,
        embedding_adapter=tensorrt_embedder,
        rerank_adapter=None,
    )
    try:
        yield repo, db_adapter
    finally:
        await db_adapter.dispose()
        settings.DATABASE, settings.SQLITE_MEMORY, settings.EMBEDDING_DIMENSIONS = original


async def _create_user(db_adapter, user_id) -> None:
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
                "name": "User",
                "email": f"{user_id}@example.test",
                "created_at": now,
                "updated_at": now,
            },
        )


def _memory(title: str) -> MemoryCreate:
    return MemoryCreate(
        title=title,
        content=f"{title} content about topic-alpha",
        context="isolation test",
        keywords=["topic-alpha"],
        tags=["t"],
        importance=7,
    )


@pytest.mark.asyncio
async def test_similar_memories_still_found_for_owner(memory_repo_and_db):
    """Control: the owner finds their own similar memory, so the setup is valid."""
    repo, db = memory_repo_and_db
    user = uuid4()
    await _create_user(db, user)
    first = await repo.create_memory(user, _memory("Alice one"))
    second = await repo.create_memory(user, _memory("Alice two"))

    scored = await repo.find_similar_memories_scored(user, first.id, max_links=5)

    assert [m.id for m, _ in scored] == [second.id]
    assert scored[0][1] >= settings.MEMORY_SIMILARITY_THRESHOLD


@pytest.mark.asyncio
async def test_find_similar_memories_scored_rejects_other_users_memory(memory_repo_and_db):
    """A foreign memory id raises NotFoundError instead of ranking against its embedding."""
    repo, db = memory_repo_and_db
    alice, bob = uuid4(), uuid4()
    await _create_user(db, alice)
    await _create_user(db, bob)
    alice_memory = await repo.create_memory(alice, _memory("Alice private secret"))
    await repo.create_memory(bob, _memory("Bob own note"))

    with pytest.raises(NotFoundError):
        await repo.find_similar_memories_scored(bob, alice_memory.id, max_links=5)


@pytest.mark.asyncio
async def test_find_similar_memories_rejects_other_users_memory(memory_repo_and_db):
    """Same through the unscored wrapper."""
    repo, db = memory_repo_and_db
    alice, bob = uuid4(), uuid4()
    await _create_user(db, alice)
    await _create_user(db, bob)
    alice_memory = await repo.create_memory(alice, _memory("Alice private secret"))
    await repo.create_memory(bob, _memory("Bob own note"))

    with pytest.raises(NotFoundError):
        await repo.find_similar_memories(bob, alice_memory.id, max_links=5)


@pytest.mark.asyncio
async def test_find_obsolete_matches_ignores_other_users_memory(memory_repo_and_db, tensorrt_embedder):
    """A foreign memory id yields no obsolete matches, as on PostgreSQL."""
    repo, db = memory_repo_and_db
    # The two texts are close (cosine about 0.8, above the 0.5 cutoff below), so a repository that
    # ranked against the foreign embedding would return Bob's memory.
    closeness = await similarity(
        tensorrt_embedder, _memory("Alice private secret"), _memory("Bob obsolete note"),
    )
    assert closeness > 0.7
    alice, bob = uuid4(), uuid4()
    await _create_user(db, alice)
    await _create_user(db, bob)
    alice_memory = await repo.create_memory(alice, _memory("Alice private secret"))
    bob_memory = await repo.create_memory(bob, _memory("Bob obsolete note"))
    assert await repo.mark_obsolete(
        user_id=bob, memory_id=bob_memory.id, reason="old", superseded_by=None,
    )

    matches = await repo.find_obsolete_matches(
        bob, alice_memory.id, limit=5, min_similarity=0.5,
    )

    assert matches == []


@pytest.mark.asyncio
async def test_find_obsolete_matches_still_found_for_owner(memory_repo_and_db):
    """Control: the owner still gets obsolete matches for their own memory."""
    repo, db = memory_repo_and_db
    user = uuid4()
    await _create_user(db, user)
    current = await repo.create_memory(user, _memory("Alice current"))
    old = await repo.create_memory(user, _memory("Alice old"))
    assert await repo.mark_obsolete(
        user_id=user, memory_id=old.id, reason="old", superseded_by=None,
    )

    matches = await repo.find_obsolete_matches(user, current.id, limit=5, min_similarity=0.5)

    assert [m.id for m, _ in matches] == [old.id]
