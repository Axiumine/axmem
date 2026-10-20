"""Re-embedding and embedding-storage support of the Postgres memory repository."""
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, func, select, text, update

from app.config.logging_config import logging
from app.config.settings import settings
from app.models.memory_models import Memory
from app.repositories.postgres.memory_base import (
    MemoryRepositoryBase,
    affected_rows,
    memory_load_options,
    project_membership_filter,
)
from app.repositories.postgres.postgres_tables import MemoryTable, ProjectsTable
from app.repositories.vector_ddl import resize_pgvector_column_sql

logger = logging.getLogger(__name__)


class MemoryReembeddingMixin(MemoryRepositoryBase):
    """Bulk embedding rewrites, targeted rebuilds and embedding storage checks."""

    # ============ Re-embedding support methods ============

    async def count_all_memories(self) -> int:
        """Count all memories, including obsolete ones, across all users."""
        async with self.db_adapter.system_session() as session:
            result = await session.scalar(
                select(func.count()).select_from(MemoryTable),
            )
            return cast("int", result)

    async def get_memories_for_reembedding(self, limit: int, offset: int) -> list[Memory]:
        """Fetch all memories, including obsolete ones, in batches ordered by id."""
        async with self.db_adapter.system_session() as session:
            stmt = (
                select(MemoryTable)
                .options(*memory_load_options())
                .order_by(MemoryTable.id.asc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(stmt)
            memories_orm = result.scalars().all()
            return [Memory.model_validate(m) for m in memories_orm]

    async def reset_embedding_storage(self) -> None:
        """Alter embedding column to match new EMBEDDING_DIMENSIONS.

        pgvector cannot cast vectors across dimensions, so existing embeddings
        must be cleared before resizing the column.
        """
        dims = settings.EMBEDDING_DIMENSIONS
        # Built (and validated) first, so a bad width fails before anything is cleared
        resize_column = resize_pgvector_column_sql("memories", dims)
        async with self.db_adapter.system_session() as session:
            # Drop NOT NULL so we can clear embeddings
            await session.execute(text(
                "ALTER TABLE memories ALTER COLUMN embedding DROP NOT NULL",
            ))
            # Clear existing embeddings (can't cast across dimensions)
            await session.execute(text(
                "UPDATE memories SET embedding = NULL",
            ))
            # Resize the column
            await session.execute(text(resize_column))
            logger.info("Altered embedding column", extra={
                "dimensions": dims,
            })

    async def finalize_embedding_storage(self) -> None:
        """Restore NOT NULL on the embedding column that reset_embedding_storage dropped.

        Only valid once every memory has an embedding again, otherwise PostgreSQL rejects it.
        """
        async with self.db_adapter.system_session() as session:
            await session.execute(text(
                "ALTER TABLE memories ALTER COLUMN embedding SET NOT NULL",
            ))

    async def bulk_update_embeddings(self, updates: list[tuple[int, list[float]]]) -> None:
        """Write new embeddings for a batch of memory IDs"""
        async with self.db_adapter.system_session() as session:
            for memory_id, embedding in updates:
                await session.execute(
                    update(MemoryTable)
                    .where(MemoryTable.id == memory_id)
                    # Explicit value: suppresses the column `onupdate`, a re-embed is not an edit.
                    .values(embedding=embedding, updated_at=MemoryTable.updated_at),
                )

    # ---- Targeted (user-scoped) rebuild support, never resets vec storage ----

    def _build_targeted_rebuild_filter(
        self,
        user_id: UUID,
        memory_ids: list[int] | None,
        project: str | None,
    ) -> Select[tuple[MemoryTable]]:
        conditions = [
            MemoryTable.user_id == user_id,
            MemoryTable.is_obsolete.is_(False),
        ]
        if memory_ids:
            conditions.append(MemoryTable.id.in_(memory_ids))
        stmt = select(MemoryTable).where(*conditions)
        if project is not None:
            stmt = stmt.where(project_membership_filter(project))
        return stmt

    async def count_memories_for_targeted_rebuild(
        self,
        user_id: UUID,
        memory_ids: list[int] | None = None,
        project: str | None = None,
    ) -> int:
        async with self.db_adapter.system_session() as session:
            base = self._build_targeted_rebuild_filter(
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
                    ProjectsTable.user_id == user_id,
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
            stmt = self._build_targeted_rebuild_filter(
                user_id=user_id,
                memory_ids=memory_ids,
                project=project,
            )
            if after_id is not None:
                stmt = stmt.where(MemoryTable.id > after_id)
            stmt = (
                stmt
                .options(*memory_load_options())
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
        """Idempotently set embeddings for memories owned by `user_id`.

        Postgres stores embeddings inline on `memories.embedding`, so an UPDATE
        is naturally idempotent. We still verify ownership server-side instead
        of trusting the input list.

        `expected_updated_at` maps a memory id to the `updated_at` observed when
        its embedding input was read. The UPDATE only matches while the row still
        carries that value, so a memory edited (and re-embedded) in the meantime
        keeps its newer vector and its id is omitted from the result. `updated_at`
        itself is never changed by this method.
        """
        if not updates:
            return []
        expected_updated_at = expected_updated_at or {}
        ids = [mid for mid, _ in updates]
        async with self.db_adapter.system_session() as session:
            owned_rows = await session.execute(
                select(MemoryTable.id).where(
                    MemoryTable.user_id == user_id,
                    MemoryTable.is_obsolete.is_(False),
                    MemoryTable.id.in_(ids),
                ),
            )
            owned_ids = {row[0] for row in owned_rows}
            written: list[int] = []
            for memory_id, embedding in updates:
                if memory_id not in owned_ids:
                    continue
                stmt = update(MemoryTable).where(
                    MemoryTable.id == memory_id,
                    MemoryTable.user_id == user_id,
                )
                if memory_id in expected_updated_at:
                    stmt = stmt.where(MemoryTable.updated_at == expected_updated_at[memory_id])
                # Explicit `updated_at` value: suppresses the column `onupdate`.
                result = await session.execute(
                    stmt.values(embedding=embedding, updated_at=MemoryTable.updated_at),
                )
                if affected_rows(result):
                    written.append(memory_id)
            return written

    async def validate_embedding_count(self) -> bool:
        """Check embedding count matches all memories, including obsolete ones."""
        async with self.db_adapter.system_session() as session:
            memory_count = await session.scalar(
                select(func.count()).select_from(MemoryTable),
            )
            embedding_count = await session.scalar(
                select(func.count()).select_from(MemoryTable).where(
                    MemoryTable.embedding.isnot(None),
                ),
            )
            return memory_count == embedding_count

    async def validate_embedding_dimensions(self) -> bool:
        """Sample embeddings and verify correct dimensions"""
        async with self.db_adapter.system_session() as session:
            result = await session.execute(
                text("""
                    SELECT vector_dims(embedding) as dims
                    FROM memories
                    WHERE embedding IS NOT NULL
                    LIMIT 5
                """),
            )
            rows = result.fetchall()
            if not rows:
                return True

            for row in rows:
                if row[0] != settings.EMBEDDING_DIMENSIONS:
                    logger.error("Dimension mismatch", extra={
                        "expected": settings.EMBEDDING_DIMENSIONS,
                        "actual": row[0],
                    })
                    return False
            return True

    async def validate_search_works(self) -> bool:
        """Smoke-test vector storage, including obsolete memories."""
        async with self.db_adapter.system_session() as session:
            title_result = await session.execute(
                text("SELECT title FROM memories LIMIT 1"),
            )
            row = title_result.fetchone()
            if not row:
                return True

            query_text = row[0]
            embeddings = await self._generate_embeddings(query_text)

            search_result = await session.execute(
                select(MemoryTable.id)
                .where(MemoryTable.embedding.isnot(None))
                .order_by(MemoryTable.embedding.cosine_distance(embeddings))
                .limit(1),
            )
            return search_result.fetchone() is not None
