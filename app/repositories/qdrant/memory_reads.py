"""Single-memory lookup and filtered, sorted listing for the Qdrant memory repository"""
from datetime import UTC, datetime
from uuid import UUID

from app.config.logging_config import logging
from app.exceptions import NotFoundError
from app.models.memory_models import VALID_SORT_BY, VALID_SORT_ORDER, Memory
from app.repositories.qdrant.memory_base import QdrantMemoryBase
from app.repositories.qdrant.store import (
    Point,
    Scope,
    Where,
    any_of,
    at_least,
    equals,
    since,
)

logger = logging.getLogger(__name__)

IMPORTANCE_LEVELS = range(1, 11)


def _listing_filter(
    project: str | None,
    include_obsolete: bool,
    importance_min: int | None,
    created_since: datetime | None,
    tags: list[str] | None,
) -> Where:
    """The conditions of a listing, besides the tenant."""
    where = Where()
    if not include_obsolete:
        where = where.also(equals("is_obsolete", False))
    if importance_min is not None:
        where = where.also(at_least("importance", importance_min))
    if created_since is not None:
        where = where.also(since("created_at", created_since.astimezone(UTC)))
    if project:
        where = where.also(equals("project", project))
    if tags:
        where = where.also(any_of("tags", tags))
    return where


class QdrantMemoryReadMixin(QdrantMemoryBase):
    """Memory reads by id and by listing filters"""

    async def get_memory_by_id(self, user_id: UUID, memory_id: UUID) -> Memory:
        """Retrieves memory by ID

        Raises:
            NotFoundError: If the memory does not exist for this tenant
        """
        scope = Scope.of(user_id)
        points = await self.store.get(scope, self.memories, [memory_id])
        if not points:
            raise NotFoundError(f"Memory with id {memory_id} not found")
        return (await self._memories(scope, points))[0]

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

        Returns:
            Tuple of (memories, total_count) where total_count is count before pagination
        """
        if sort_by not in VALID_SORT_BY:
            raise ValueError(f"sort_by must be one of: {', '.join(VALID_SORT_BY)}")
        if sort_order not in VALID_SORT_ORDER:
            raise ValueError(f"sort_order must be one of: {', '.join(VALID_SORT_ORDER)}")

        scope = Scope.of(user_id)
        where = _listing_filter(project, include_obsolete, importance_min, created_since, tags)
        total = await self.store.count(scope, self.memories, where)
        if offset >= total:
            # Nothing lies beyond the end; a scroll of offset + limit points would read the whole collection
            page: list[Point] = []
        elif sort_by == "importance":
            page = await self._by_importance(scope, where, sort_order, offset, limit)
        else:
            # Qdrant orders a scroll by a payload field but cannot skip: read the offset and drop it
            points = await self.store.scroll(
                scope, self.memories, where, limit=offset + limit, order_by=(sort_by, sort_order),
            )
            page = points[offset:]

        logger.info("Retrieved recent memories", extra={
            "user_id": str(user_id),
            "count": len(page),
            "total": total,
            "limit": limit,
            "offset": offset,
            "project_filtered": project is not None,
            "include_obsolete": include_obsolete,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "tags_filtered": bool(tags),
        })
        return await self._memories(scope, page), total

    async def _by_importance(
        self,
        scope: Scope,
        where: Where,
        sort_order: str,
        offset: int,
        limit: int,
    ) -> list[Point]:
        """A page ordered by importance, then newest first.

        Importance has only ten values, so ordering a scroll by it would leave the order inside each value
        undefined and make pages overlap. Each value is read on its own, newest first, and whole values
        are skipped by their count until the offset is used up.
        """
        levels = reversed(IMPORTANCE_LEVELS) if sort_order == "desc" else IMPORTANCE_LEVELS
        page: list[Point] = []
        skip = offset
        for level in levels:
            at_level = where.also(equals("importance", level))
            if skip:
                size = await self.store.count(scope, self.memories, at_level)
                if skip >= size:
                    skip -= size
                    continue
            points = await self.store.scroll(
                scope, self.memories, at_level, limit=skip + limit - len(page), order_by=("created_at", "desc"),
            )
            page.extend(points[skip:])
            skip = 0
            if len(page) >= limit:
                break
        return page
