"""E2E tests for the re-embed run against real PostgreSQL (pgvector).

Tests ReEmbeddingService -> PostgresMemoryRepository: the schema must be left exactly as the
application models declare it once a re-embed has completed.
"""
import argparse
import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

import main
from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.repositories.postgres.memory_repository import PostgresMemoryRepository
from app.services.re_embedding_service import ReEmbeddingService
from tests.embedding_stubs import BatchViaSingle

pytestmark = pytest.mark.asyncio(loop_scope="session")


class StubEmbeddings(BatchViaSingle):
    """Deterministic offline embeddings at the configured width."""

    async def generate_embedding(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [(digest[i % len(digest)] + 1) / 256.0 for i in range(settings.EMBEDDING_DIMENSIONS)]


async def embedding_is_nullable(db_adapter, table: str = "memories") -> bool:
    async with db_adapter.system_session() as session:
        value = await session.scalar(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = 'embedding'",
            ),
            {"table": table},
        )
    return value == "YES"


async def create_user(db_adapter):
    user_id = uuid4()
    now = datetime.now(UTC)
    async with db_adapter.system_session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, external_id, name, email, created_at, updated_at) "
                "VALUES (:id, :external_id, 'Run Flow', 'run-flow@example.com', :now, :now)",
            ),
            {"id": str(user_id), "external_id": f"ext-{user_id}", "now": now},
        )
    return user_id


async def restore_schema(db_adapter, table: str) -> None:
    """Leave the shared database as the migrations created it for the next test module."""
    async with db_adapter.system_session() as session:
        await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608 - fixed table names
        await session.execute(text(
            f"ALTER TABLE {table} ALTER COLUMN embedding "
            f"TYPE vector({settings.EMBEDDING_DIMENSIONS})",
        ))
        await session.execute(text(f"ALTER TABLE {table} ALTER COLUMN embedding SET NOT NULL"))


@pytest_asyncio.fixture(loop_scope="session")
async def memory_repo(db_adapter, clean_database):
    """Repository on a schema that starts exactly as the migrations create it."""
    await restore_schema(db_adapter, "memories")
    repo = PostgresMemoryRepository(
        db_adapter=db_adapter, embedding_adapter=StubEmbeddings(), rerank_adapter=None,
    )
    try:
        yield repo
    finally:
        await restore_schema(db_adapter, "memories")


@pytest.mark.e2e
@pytest.mark.parametrize("count", [0, 3], ids=["empty", "three-memories"])
async def test_not_null_embedding_constraint_is_restored_after_re_embed(
    memory_repo, db_adapter, count,
):
    # Arrange
    user_id = await create_user(db_adapter)
    for index in range(count):
        await memory_repo.create_memory(user_id, MemoryCreate(
            title=f"Memory {index}", content=f"content number {index}", context="run flow",
            keywords=["run"], tags=["flow"], importance=7,
        ))
    assert not await embedding_is_nullable(db_adapter)

    # Act
    result = await ReEmbeddingService(memory_repo, StubEmbeddings(), batch_size=2).re_embed_all()

    # Assert: the reset relaxes the column while vectors are rebuilt; a finished run restores it.
    assert result.validation.all_passed
    assert not await embedding_is_nullable(db_adapter)


async def _seed_memories(memory_repo, db_adapter):
    user_id = await create_user(db_adapter)
    for title in ("pdf processing", "docx processing"):
        await memory_repo.create_memory(user_id, MemoryCreate(
            title=title, content=f"Process documents: {title}", context="run flow",
            keywords=["run"], tags=["flow"], importance=7,
        ))
    return user_id


async def _store_vectors_from_another_model(db_adapter):
    """Give every memory an 8-wide vector, as if another model produced it."""
    async with db_adapter.system_session() as session:
        await session.execute(text("ALTER TABLE memories ALTER COLUMN embedding DROP NOT NULL"))
        await session.execute(text("ALTER TABLE memories ALTER COLUMN embedding TYPE vector(8) USING NULL"))
        await session.execute(text("UPDATE memories SET embedding = '[1,0,0,0,0,0,0,0]'::vector"))


async def _memory_vector_widths(db_adapter):
    async with db_adapter.system_session() as session:
        widths = (await session.execute(text("SELECT DISTINCT vector_dims(embedding) FROM memories"))).all()
    return [row[0] for row in widths]


@pytest.mark.e2e
async def test_memories_are_migrated_from_another_model_and_keep_their_constraint(
    memory_repo, db_adapter,
):
    # Arrange: stored memories whose vectors came from another (8-wide) model, while the
    # application already runs with the new configuration, as after a restart.
    await _seed_memories(memory_repo, db_adapter)
    before = {m.id: m.updated_at for m in await memory_repo.get_memories_for_reembedding(10, 0)}
    await _store_vectors_from_another_model(db_adapter)

    # Act
    result = await ReEmbeddingService(memory_repo, StubEmbeddings(), batch_size=1).re_embed_all()

    # Assert: memories have vectors of the configured width again.
    assert (result.total_memories, result.total_processed) == (2, 2)
    assert result.validation.all_passed
    assert not await embedding_is_nullable(db_adapter)
    assert await _memory_vector_widths(db_adapter) == [settings.EMBEDDING_DIMENSIONS]
    # Rebuilding a vector is not an edit of the memory.
    after = {m.id: m.updated_at for m in await memory_repo.get_memories_for_reembedding(10, 0)}
    assert after == before


@pytest.mark.e2e
async def test_dry_run_counts_without_touching_the_schema(
    memory_repo, db_adapter, monkeypatch, capsys,
):
    # Arrange: one memory, and the migration version the database is at.
    import app.bootstrap
    import app.config.logging_config

    monkeypatch.setattr(app.config.logging_config, "configure_logging", lambda **_: None)
    monkeypatch.setattr(main.atexit, "register", lambda *_: None)
    monkeypatch.setattr(app.bootstrap, "get_embedding_adapter", StubEmbeddings)
    user_id = await create_user(db_adapter)
    await memory_repo.create_memory(user_id, MemoryCreate(
        title="Memory", content="content", context="run flow",
        keywords=["run"], tags=["flow"], importance=7,
    ))

    async def version():
        async with db_adapter.system_session() as session:
            return await session.scalar(text("SELECT version_num FROM alembic_version"))

    before = await version()

    # Act
    await main._run_reembed(argparse.Namespace(batch_size=5, dry_run=True))

    # Assert
    out = capsys.readouterr().out
    assert "Memories to process: 1" in out
    assert "No changes were made." in out
    assert await version() == before
    assert not await embedding_is_nullable(db_adapter)
