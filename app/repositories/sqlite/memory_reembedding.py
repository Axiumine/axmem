"""Re-embedding, targeted rebuild and embedding validation for the SQLite memory repository"""
from datetime import datetime
from uuid import UUID

import sqlite_vec
from sqlalchemy import Select, func, select, text, update

from app.config.logging_config import logging
from app.config.settings import settings
from app.models.memory_models import Memory
from app.repositories.sqlite.memory_base import SqliteMemoryBase
from app.repositories.sqlite.memory_queries import (
    affected_rows,
    in_project,
    insert_embedding,
    with_relationships,
)
from app.repositories.sqlite.sqlite_tables import MemoryTable, ProjectsTable
from app.repositories.vector_ddl import create_vec0_table_sql

logger = logging.getLogger(__name__)


def _targeted_rebuild_statement(
    user_id: UUID,
    memory_ids: list[int] | None,
    project: str | None,
) -> Select[tuple[MemoryTable]]:
    """Shared SQLAlchemy filter for targeted rebuild count/select queries."""
    conditions = [
        MemoryTable.user_id == str(user_id),
        MemoryTable.is_obsolete.is_(False),
    ]
    if memory_ids:
        conditions.append(MemoryTable.id.in_(memory_ids))
    stmt = select(MemoryTable).where(*conditions)
    if project is not None:
        stmt = stmt.where(in_project(project))
    return stmt


class SqliteMemoryReembeddingMixin(SqliteMemoryBase):
    """Support for re-embedding every memory, rebuilding a user's embeddings and validating them"""

    # ============ Re-embedding support methods ============

    async def count_all_memories(self) -> int:
        """Count all memories, including obsolete ones, across all users."""
        async with self.db_adapter.system_session() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM memories"),
            )
            return result.scalar_one()

    async def get_memories_for_reembedding(self, limit: int, offset: int) -> list[Memory]:
        """Fetch all memories, including obsolete ones, in batches ordered by id."""
        async with self.db_adapter.system_session() as session:
            stmt = (
                with_relationships(select(MemoryTable))
                .order_by(MemoryTable.id.asc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(stmt)
            memories_orm = result.scalars().all()
            return [Memory.model_validate(m) for m in memories_orm]

    async def reset_embedding_storage(self) -> None:
        """Drop and recreate vec_memories table with current EMBEDDING_DIMENSIONS"""
        # Built (and validated) first, so a bad width fails before the old table is dropped
        create_table = create_vec0_table_sql(
            "vec_memories", "memory_id", settings.EMBEDDING_DIMENSIONS,
        )
        async with self.db_adapter.system_session() as session:
            await session.execute(text("DROP TABLE IF EXISTS vec_memories"))
            await session.execute(text(create_table))
            logger.info("Recreated vec_memories table", extra={
                "dimensions": settings.EMBEDDING_DIMENSIONS,
            })

    async def finalize_embedding_storage(self) -> None:
        """Nothing to restore: resetting recreates vec_memories without relaxing any constraint."""

    async def bulk_update_embeddings(self, updates: list[tuple[int, list[float]]]) -> None:
        """Write new embeddings for a batch of memory IDs into vec_memories"""
        async with self.db_adapter.system_session() as session:
            for memory_id, embedding in updates:
                await insert_embedding(session, memory_id, embedding)

    # ---- Targeted (user-scoped) rebuild support, never resets vec storage ----

    async def count_memories_for_targeted_rebuild(
        self,
        user_id: UUID,
        memory_ids: list[int] | None = None,
        project: str | None = None,
    ) -> int:
        async with self.db_adapter.system_session() as session:
            base = _targeted_rebuild_statement(
                user_id=user_id,
                memory_ids=memory_ids,
                project=project,
            )
            count_stmt = select(func.count()).select_from(base.subquery())
            return (await session.execute(count_stmt)).scalar() or 0

    async def project_exists_for_user(self, user_id: UUID, project: str) -> bool:
        async with self.db_adapter.system_session() as session:
            found = await session.execute(
                select(ProjectsTable.id).where(
                    ProjectsTable.name == project,
                    ProjectsTable.user_id == str(user_id),
                ),
            )
            return found.first() is not None

    async def get_memories_for_targeted_rebuild(
        self,
        user_id: UUID,
        limit: int,
        after_id: int | None = None,
        memory_ids: list[int] | None = None,
        project: str | None = None,
    ) -> list[Memory]:
        async with self.db_adapter.system_session() as session:
            stmt = _targeted_rebuild_statement(
                user_id=user_id,
                memory_ids=memory_ids,
                project=project,
            )
            if after_id is not None:
                stmt = stmt.where(MemoryTable.id > after_id)
            stmt = (
                with_relationships(stmt)
                .order_by(MemoryTable.id.asc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            memories_orm = result.scalars().unique().all()
            return [Memory.model_validate(m) for m in memories_orm]

    async def upsert_targeted_embeddings(
        self,
        user_id: UUID,
        updates: list[tuple[int, list[float]]],
        expected_updated_at: dict[int, datetime] | None = None,
    ) -> list[int]:
        """Idempotently write embeddings for memories owned by `user_id`.

        Implementation note: SQLite-vec's `vec_memories` virtual table is
        keyed by `memory_id` and rejects duplicate inserts. We delete the
        existing row (if any) before inserting the new embedding.

        Every row is guarded by a no-op UPDATE of its `memories` row that also
        checks ownership, `is_obsolete` and, when given, `expected_updated_at`.
        The UPDATE is the first write of the transaction, so it takes SQLite's
        write lock and holds it until commit: a concurrent `update_memory` has
        to wait for this batch instead of slipping between check and write, and
        a row it already committed fails the check. A plain SELECT is not
        enough here, because pysqlite opens its transaction only at the first
        DML statement, leaving a SELECT outside it.

        `expected_updated_at` maps a memory id to the `updated_at` observed when
        its embedding input was read. A memory whose row no longer carries that
        value was edited (and re-embedded) in the meantime, so its vector is left
        alone and its id is omitted from the result.
        """
        if not updates:
            return []
        expected_updated_at = expected_updated_at or {}
        async with self.db_adapter.system_session() as session:
            written: list[int] = []
            for memory_id, embedding in updates:
                guard = (
                    update(MemoryTable)
                    .where(
                        MemoryTable.id == memory_id,
                        MemoryTable.user_id == str(user_id),
                        MemoryTable.is_obsolete.is_(False),
                    )
                    # Explicit value keeps the column's `onupdate` from bumping updated_at.
                    .values(updated_at=MemoryTable.updated_at)
                )
                if memory_id in expected_updated_at:
                    guard = guard.where(MemoryTable.updated_at == expected_updated_at[memory_id])
                guarded = await session.execute(guard)
                if affected_rows(guarded) == 0:
                    continue
                await session.execute(
                    text("DELETE FROM vec_memories WHERE memory_id = :memory_id"),
                    {"memory_id": str(memory_id)},
                )
                await insert_embedding(session, memory_id, embedding)
                written.append(memory_id)
            return written

    async def validate_embedding_count(self) -> bool:
        """Check embedding count matches all memories, including obsolete ones."""
        async with self.db_adapter.system_session() as session:
            mem_result = await session.execute(
                text("SELECT COUNT(*) FROM memories"),
            )
            memory_count = mem_result.scalar()

            vec_result = await session.execute(
                text("SELECT COUNT(*) FROM vec_memories"),
            )
            vec_count = vec_result.scalar()

            return memory_count == vec_count

    async def validate_embedding_dimensions(self) -> bool:
        """Sample embeddings and verify correct dimensions"""
        async with self.db_adapter.system_session() as session:
            result = await session.execute(
                text("SELECT embedding FROM vec_memories LIMIT 5"),
            )
            rows = result.fetchall()
            if not rows:
                return True  # no embeddings to validate

            for row in rows:
                embedding_bytes = row[0]
                # sqlite-vec stores as raw bytes, 4 bytes per float32
                num_dims = len(embedding_bytes) // 4
                if num_dims != settings.EMBEDDING_DIMENSIONS:
                    logger.error("Dimension mismatch", extra={
                        "expected": settings.EMBEDDING_DIMENSIONS,
                        "actual": num_dims,
                    })
                    return False
            return True

    async def validate_search_works(self) -> bool:
        """Smoke-test vector storage, including obsolete memories."""
        async with self.db_adapter.system_session() as session:
            # Pick a random memory title
            title_result = await session.execute(
                text("SELECT title FROM memories LIMIT 1"),
            )
            row = title_result.fetchone()
            if not row:
                return True  # no memories to test

            query_text = row[0]
            embeddings = await self._generate_embeddings(query_text)
            embedding_bytes = sqlite_vec.serialize_float32(embeddings)

            search_result = await session.execute(
                text("""
                    SELECT vm.memory_id
                    FROM vec_memories vm
                    ORDER BY vec_distance_cosine(vm.embedding, :query_embedding)
                    LIMIT 1
                """),
                {"query_embedding": embedding_bytes},
            )
            return search_result.fetchone() is not None
