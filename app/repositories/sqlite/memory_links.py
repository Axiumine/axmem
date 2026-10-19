"""Memory-to-memory links for the SQLite memory repository"""
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.exc import IntegrityError, NoResultFound

from app.config.logging_config import logging
from app.exceptions import NotFoundError
from app.models.memory_models import Memory
from app.repositories.sqlite.memory_base import SqliteMemoryBase
from app.repositories.sqlite.memory_queries import affected_rows, with_relationships
from app.repositories.sqlite.sqlite_tables import (
    MemoryLinkTable,
    MemoryTable,
    ProjectsTable,
)

logger = logging.getLogger(__name__)


class SqliteMemoryLinkMixin(SqliteMemoryBase):
    """Reading, creating and removing the bidirectional links between memories"""

    async def get_linked_memories(
        self,
        user_id: UUID,
        memory_id: int,
        project_ids: list[int] | None,
        max_links: int = 5,
        exclude_ids: list[int] | None = None,
    ) -> list[Memory]:
        """Get memories linked to a specific memory (1-hop neighbors)

        Args:
            user_id: User ID,
            memory_id: Memory ID of the memory to retrieve linked memories for
            max_links: Maximum number of linked memories to return
            project_ids: Optional to filter linked memories for projects
            exclude_ids: Optional memory IDs to leave out, applied before max_links

        Returns:
            List of linked Memory objects
        """
        stmt = with_relationships(
            select(MemoryTable)
            .join(
                MemoryLinkTable,
                or_(
                    (MemoryLinkTable.source_id == memory_id) & (MemoryLinkTable.target_id == MemoryTable.id),
                    (MemoryLinkTable.target_id == memory_id) & (MemoryLinkTable.source_id == MemoryTable.id),
                ),
            ),
        ).where(
            MemoryTable.user_id == str(user_id),
            MemoryTable.id != memory_id,
            MemoryTable.is_obsolete.is_(False),
        )

        if exclude_ids:
            stmt = stmt.where(MemoryTable.id.not_in(exclude_ids))

        if project_ids:
            stmt = stmt.join(MemoryTable.projects).where(ProjectsTable.id.in_(project_ids)).distinct()

        stmt = stmt.order_by(MemoryTable.importance.desc()).limit(max_links)

        async with self.db_adapter.session(user_id=user_id) as session:
            try:

                result = await session.execute(stmt)
                memories_orm = result.scalars().all()
                return [Memory.model_validate(memory) for memory in memories_orm]

            except NoResultFound:
                raise NotFoundError(f"No linked memories retrieved for {memory_id}")

    async def create_link(
        self,
        user_id: UUID,
        source_id: int,
        target_id: int,
    ) -> MemoryLinkTable:
        """Creates a bidirectional link between two memories

        Args:
            user_id: User ID,
            source_id: Source memory ID
            target_id: Target memory ID

        Returns:
            Memory Link ORM

        Raises:
            NotFoundError: If source or target memory not found
            IntegrityError: If link already exists
        """
        async with self.db_adapter.session(user_id) as session:
            # Both endpoints must belong to this user. session.get() has no user
            # filter, so it would accept (and so reveal the existence of) another
            # user's memory ids and let a link be written across users.
            owned_result = await session.execute(
                select(MemoryTable.id).where(
                    MemoryTable.user_id == str(user_id),
                    MemoryTable.id.in_([source_id, target_id]),
                ),
            )
            owned_ids = set(owned_result.scalars().all())

            if source_id not in owned_ids:
                raise NotFoundError(f"Source memory with id {source_id} not found")

            if target_id not in owned_ids:
                raise NotFoundError(f"Target memory with id {target_id} not found")

            # Swap IDs if needed to ensure no duplicates
            link_source_id = source_id
            link_target_id = target_id
            if source_id > target_id:
                link_source_id, link_target_id = target_id, source_id

            logger.info(
                "Creating memory link",
                extra={
                    "user_id": str(user_id),
                    "source_id": link_source_id,
                    "target_id": link_target_id,
                },
            )

            link = MemoryLinkTable(source_id=link_source_id, target_id=link_target_id, user_id=str(user_id))

            session.add(link)
            try:
                await session.flush()
                await session.refresh(link)
                logger.info(
                    "Created link between memories",
                    extra={"user_id": str(user_id), "source_id": link_source_id, "target_id": link_target_id},
                )
                return link
            except IntegrityError:
                logger.warning(
                    "Memory link already existed",
                    extra={"user_id": str(user_id), "source_id": link_source_id, "target_id": link_target_id},
                )
                await session.rollback()
                raise

    async def create_links_batch(self, user_id: UUID, source_id: int, target_ids: list[int]) -> list[int]:
        """Create multiple links from one memory to many others

        Args:
            user_id: User ID
            source_id: Source memory ID
            target_ids: List of target memories IDs to link the source memory to

        Returns:
           List of Memory ID's that the memory has been linked with
        """
        if not target_ids:
            return []

        # SQLite integers are signed 64-bit and sqlite3 raises OverflowError for a larger value
        # instead of matching no row. Such ids cannot exist, so skip them like unknown ids; an error
        # mid-loop would abort the batch after earlier links were already committed.
        max_storable_id = 2**63 - 1
        if not 0 < source_id <= max_storable_id:
            return []
        target_ids = [target_id for target_id in target_ids if 0 < target_id <= max_storable_id]

        links_created = []

        for target_id in target_ids:
            if source_id == target_id:
                continue
            try:
                await self.create_link(user_id=user_id, source_id=source_id, target_id=target_id)
                links_created.append(target_id)
            except (IntegrityError, NotFoundError):
                # Skip duplicates and invalid target IDs
                continue

        logger.info("Memory links created", extra={"user_id": str(user_id), "source_id": source_id, "links_created": links_created})

        return links_created

    async def unlink_memories(
            self,
            user_id: UUID,
            source_id: int,
            target_id: int,
    ) -> bool:
        """Remove bidirectional link between two memories.

        Args:
            user_id: User ID for isolation
            source_id: Source memory ID
            target_id: Target memory ID to unlink

        Returns:
            True if link was removed, False if link didn't exist
        """
        async with self.db_adapter.session(user_id) as session:
            # Delete both directions (source→target and target→source)
            stmt = delete(MemoryLinkTable).where(
                MemoryLinkTable.user_id == str(user_id),
                or_(
                    and_(
                        MemoryLinkTable.source_id == source_id,
                        MemoryLinkTable.target_id == target_id,
                    ),
                    and_(
                        MemoryLinkTable.source_id == target_id,
                        MemoryLinkTable.target_id == source_id,
                    ),
                ),
            )
            result = await session.execute(stmt)
            await session.commit()

            deleted = affected_rows(result) > 0
            logger.info("Memory link removed", extra={
                "user_id": str(user_id),
                "source_id": source_id,
                "target_id": target_id,
                "deleted": deleted,
            })

            return deleted
