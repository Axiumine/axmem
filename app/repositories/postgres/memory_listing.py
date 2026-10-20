"""Paginated listing queries of the Postgres memory repository."""
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import ColumnElement, func, select

from app.config.logging_config import logging
from app.models.memory_models import VALID_SORT_ORDER, Memory
from app.repositories.postgres.memory_base import (
    MemoryRepositoryBase,
    memory_load_options,
    project_membership_filter,
)
from app.repositories.postgres.postgres_tables import MemoryTable

logger = logging.getLogger(__name__)


def _list_conditions(
        user_id: UUID,
        project: str | None,
        include_obsolete: bool,
        tags: list[str] | None,
        importance_min: int | None,
        created_since: datetime | None,
) -> list[ColumnElement[bool]]:
    """WHERE conditions shared by the page query and the count query of list_memories."""
    conditions = [MemoryTable.user_id == user_id]

    # Conditional obsolete filter
    if not include_obsolete:
        conditions.append(MemoryTable.is_obsolete.is_(False))

    if importance_min is not None:
        conditions.append(MemoryTable.importance >= importance_min)

    if created_since is not None:
        conditions.append(MemoryTable.created_at >= created_since)

    # Tag filter using Postgres ARRAY overlap
    if tags:
        conditions.append(MemoryTable.tags.overlap(tags))

    # Project filter
    if project:
        conditions.append(project_membership_filter(project))

    return conditions


def _list_ordering(sort_by: str, sort_order: str) -> tuple[ColumnElement[Any], ...]:
    """ORDER BY terms of list_memories; ValueError for an unknown sort field or direction."""
    sort_column_map = {
        "created_at": MemoryTable.created_at,
        "updated_at": MemoryTable.updated_at,
        "importance": MemoryTable.importance,
    }
    if sort_by not in sort_column_map:
        raise ValueError(f"sort_by must be one of: {', '.join(sort_column_map)}")
    if sort_order not in VALID_SORT_ORDER:
        raise ValueError(f"sort_order must be one of: {', '.join(VALID_SORT_ORDER)}")
    sort_column = sort_column_map[sort_by]
    order = sort_column.desc() if sort_order == "desc" else sort_column.asc()
    # Add deterministic tie-breaker so identical timestamps still return newest IDs first
    id_tiebreak = MemoryTable.id.desc() if sort_order == "desc" else MemoryTable.id.asc()
    if sort_by == "importance":
        return order, MemoryTable.created_at.desc(), MemoryTable.id.desc()
    return order, id_tiebreak


class MemoryListingMixin(MemoryRepositoryBase):
    """Listing and visibility queries over a user's memories."""

    async def list_memories(
            self,
            user_id: UUID,
            limit: int,
            offset: int = 0,
            project: str | None = None,
            include_obsolete: bool = False,
            sort_by: str = "created_at",
            sort_order: str = "desc",
            tags: list[str] | None = None,
            importance_min: int | None = None,
            created_since: datetime | None = None,
    ) -> tuple[list[Memory], int]:
        """Get memories with pagination, sorting, and filtering.

        Args:
            user_id: User ID for ownership filtering
            limit: Maximum number of memories to return
            offset: Skip N results for pagination
            project: Optional filter to only retrieve memories in this project scope
            include_obsolete: Include soft-deleted memories (default False)
            sort_by: Sort field - created_at, updated_at, importance
            sort_order: Sort direction - asc, desc
            tags: Filter by ANY of these tags (OR logic)
            importance_min: Minimum importance score (optional)
            created_since: Inclusive creation timestamp floor (UTC-aware)

        Returns:
            Tuple of (memories, total_count) where total_count is count before pagination
        """
        conditions = _list_conditions(
            user_id=user_id,
            project=project,
            include_obsolete=include_obsolete,
            tags=tags,
            importance_min=importance_min,
            created_since=created_since,
        )

        # Build main query with eager loading, dynamic sorting and pagination
        stmt = (
            select(MemoryTable)
            .options(*memory_load_options())
            .where(*conditions)
            .order_by(*_list_ordering(sort_by, sort_order))
            .offset(offset)
            .limit(limit)
        )

        # Build count query (same conditions, no limit/offset)
        count_stmt = select(func.count()).select_from(MemoryTable).where(*conditions)

        async with self.db_adapter.session(user_id) as session:
            # Execute count query
            total = cast("int", await session.scalar(count_stmt))

            # Execute main query
            result = await session.execute(stmt)
            memories = result.scalars().all()

            logger.info("Retrieved recent memories", extra={
                "user_id": user_id,
                "count": len(memories),
                "total": total,
                "limit": limit,
                "offset": offset,
                "project_filtered": project is not None,
                "include_obsolete": include_obsolete,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "tags_filter": tags,
            })

            return [Memory.model_validate(m) for m in memories], total
