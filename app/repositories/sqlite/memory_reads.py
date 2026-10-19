"""Single-memory lookup and filtered, sorted listing for the SQLite memory repository"""
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Select, select

from app.config.logging_config import logging
from app.exceptions import NotFoundError
from app.models.memory_models import VALID_SORT_ORDER, Memory
from app.repositories.sqlite.memory_base import SqliteMemoryBase
from app.repositories.sqlite.memory_queries import with_relationships
from app.repositories.sqlite.sqlite_tables import (
    MemoryTable,
    memory_project_association,
)

logger = logging.getLogger(__name__)

_SORT_COLUMNS = {
    "created_at": MemoryTable.created_at,
    "updated_at": MemoryTable.updated_at,
    "importance": MemoryTable.importance,
}


def _in_projects(project_ids: list[int]) -> ColumnElement[bool]:
    """Condition: the memory belongs to one of the projects."""
    return select(memory_project_association.c.memory_id).where(
        memory_project_association.c.memory_id == MemoryTable.id,
        memory_project_association.c.project_id.in_(project_ids),
    ).exists()


def _listing_statement(
    user_id: UUID,
    project_ids: list[int] | None,
    include_obsolete: bool,
    importance_min: int | None,
    created_since: datetime | None,
) -> Select[tuple[MemoryTable]]:
    """Memories of the user that pass the listing filters, with relationships loaded."""
    stmt = with_relationships(select(MemoryTable)).where(MemoryTable.user_id == str(user_id))

    if not include_obsolete:
        stmt = stmt.where(MemoryTable.is_obsolete.is_(False))

    if importance_min is not None:
        stmt = stmt.where(MemoryTable.importance >= importance_min)

    if created_since is not None:
        stmt = stmt.where(MemoryTable.created_at >= created_since.astimezone(UTC))

    if project_ids:
        stmt = stmt.where(_in_projects(project_ids))
    return stmt


def _sorted(
    stmt: Select[tuple[MemoryTable]],
    sort_by: str,
    sort_order: str,
) -> Select[tuple[MemoryTable]]:
    """Order the listing, raising ValueError for an unknown sort field or direction."""
    if sort_by not in _SORT_COLUMNS:
        raise ValueError(f"sort_by must be one of: {', '.join(_SORT_COLUMNS)}")
    if sort_order not in VALID_SORT_ORDER:
        raise ValueError(f"sort_order must be one of: {', '.join(VALID_SORT_ORDER)}")
    sort_column = _SORT_COLUMNS[sort_by]
    order = sort_column.desc() if sort_order == "desc" else sort_column.asc()
    # Tie-break on id to keep ordering deterministic when timestamps are equal
    id_tiebreak = MemoryTable.id.desc() if sort_order == "desc" else MemoryTable.id.asc()
    if sort_by == "importance":
        return stmt.order_by(order, MemoryTable.created_at.desc(), MemoryTable.id.desc())
    return stmt.order_by(order, id_tiebreak)


def _with_any_tag(memories: list[MemoryTable], tags: list[str] | None) -> list[MemoryTable]:
    """Keep the memories carrying any of the tags (SQLite JSON has no efficient array overlap)."""
    if not tags:
        return memories
    tag_set = set(tags)
    return [m for m in memories if m.tags and tag_set.intersection(m.tags)]


class SqliteMemoryReadMixin(SqliteMemoryBase):
    """Memory reads by id and by listing filters"""

    async def get_memory_by_id(self, user_id: UUID, memory_id: int) -> Memory:
        """Retrieves memory by ID

        Args:
            user_id: User ID
            memory_id: Id of the memory to be returned

        Returns:
            Memory object

        Raises:
            NotFoundError: If the memory does not exist for this user
        """
        memory_orm = await self.get_memory_table_by_id(user_id=user_id, memory_id=memory_id)
        return Memory.model_validate(memory_orm)

    async def get_memory_table_by_id(self, user_id: UUID, memory_id: int) -> MemoryTable:
        """Retrieves memory by ID

        Args:
            user_id: User ID
            memory_id: Id of the memory to be returned

        Returns:
            Memory Table object or None if not found
        """
        stmt = with_relationships(
            select(MemoryTable).where(MemoryTable.user_id == str(user_id), MemoryTable.id == memory_id),
        )

        async with self.db_adapter.session(user_id) as session:
            result = await session.execute(stmt)
            memory_orm = result.scalar_one_or_none()

            if memory_orm:
                return memory_orm
            raise NotFoundError(f"Memory with id {memory_id} not found")

    async def list_memories(
            self,
            user_id: UUID,
            limit: int,
            offset: int = 0,
            project_ids: list[int] | None = None,
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
            project_ids: Optional filter to only retrieve memories from specific projects
            include_obsolete: Include soft-deleted memories (default False)
            sort_by: Sort field - created_at, updated_at, importance
            sort_order: Sort direction - asc, desc
            tags: Filter by ANY of these tags (OR logic)
            importance_min: Minimum importance score (optional)
            created_since: Inclusive creation timestamp floor (UTC-aware)

        Returns:
            Tuple of (memories, total_count) where total_count is count before pagination
        """
        stmt = _sorted(
            _listing_statement(user_id, project_ids, include_obsolete, importance_min, created_since),
            sort_by,
            sort_order,
        )

        async with self.db_adapter.session(user_id) as session:
            result = await session.execute(stmt)
            all_memories = _with_any_tag(list(result.scalars().all()), tags)

            # Get total count before pagination
            total = len(all_memories)

            paginated_memories = all_memories[offset:offset + limit]

            logger.info("Retrieved recent memories", extra={
                "user_id": str(user_id),
                "count": len(paginated_memories),
                "total": total,
                "limit": limit,
                "offset": offset,
                "project_filtered": project_ids is not None,
                "include_obsolete": include_obsolete,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "tags_filter": tags,
            })

            return [Memory.model_validate(m) for m in paginated_memories], total

    async def get_listed_memory_ids(
            self,
            user_id: UUID,
            memory_ids: list[int],
            project_ids: list[int] | None = None,
    ) -> set[int]:
        """Return the ids among memory_ids that list_memories would list.

        Applies the same visibility rules as list_memories (owned by the user, not obsolete,
        member of one of project_ids when given) without loading the memories.

        Args:
            user_id: User ID for ownership filtering
            memory_ids: Candidate memory ids
            project_ids: Project filter (optional)

        Returns:
            The subset of memory_ids that is listed
        """
        if not memory_ids:
            return set()

        stmt = select(MemoryTable.id).where(
            MemoryTable.user_id == str(user_id),
            MemoryTable.id.in_(memory_ids),
            MemoryTable.is_obsolete.is_(False),
        )
        if project_ids:
            stmt = stmt.where(_in_projects(project_ids))

        async with self.db_adapter.session(user_id) as session:
            result = await session.execute(stmt)
            return set(result.scalars().all())
