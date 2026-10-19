"""Access tracking of the Postgres memory repository."""
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import update

from app.repositories.postgres.memory_base import MemoryRepositoryBase, affected_rows
from app.repositories.postgres.postgres_tables import MemoryTable


class MemoryUsageMixin(MemoryRepositoryBase):
    """Counting how often memories are retrieved (forgetful-hulkito fork)."""

    async def record_memory_access(
        self,
        user_id: UUID,
        memory_ids: list[int],
        accessed_at: datetime | None = None,
    ) -> int:
        """Increment `access_count` and update `last_accessed_at` for the given
        memory ids, scoped to `user_id`. Does NOT mutate `updated_at`.

        Returns the number of rows actually updated (owned + non-obsolete).
        """
        if not memory_ids:
            return 0
        ts = accessed_at or datetime.now(UTC)
        async with self.db_adapter.system_session() as session:
            stmt = (
                update(MemoryTable)
                .where(
                    MemoryTable.user_id == user_id,
                    MemoryTable.is_obsolete.is_(False),
                    MemoryTable.id.in_(memory_ids),
                )
                .values(
                    access_count=MemoryTable.access_count + 1,
                    last_accessed_at=ts,
                    # Explicit value: suppresses the column `onupdate` so reads do not look like edits.
                    updated_at=MemoryTable.updated_at,
                )
            )
            result = await session.execute(stmt)
            return affected_rows(result) or 0
