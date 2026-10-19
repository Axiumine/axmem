"""Memory repository for postgres data access operations

Creating, updating and obsoleting memories lives here; the search, link, listing, usage
and re-embedding queries are mixins from the sibling memory_* modules.
"""
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logging_config import logging
from app.exceptions import (
    AssociationNotFoundError,
    InvalidStateTransitionError,
    NotFoundError,
)
from app.models.memory_models import Memory, MemoryCreate, MemoryUpdate
from app.repositories.postgres.memory_associations import (
    ASSOCIATION_ID_FIELDS,
    attach_associations,
    replace_associations,
)
from app.repositories.postgres.memory_base import fits_int4, memory_load_options
from app.repositories.postgres.memory_links import MemoryLinksMixin
from app.repositories.postgres.memory_listing import MemoryListingMixin
from app.repositories.postgres.memory_reembedding import MemoryReembeddingMixin
from app.repositories.postgres.memory_search import MemorySearchMixin
from app.repositories.postgres.memory_usage import MemoryUsageMixin
from app.repositories.postgres.postgres_tables import MemoryTable
from app.utils.embedding_helpers import build_embedding_text

logger = logging.getLogger(__name__)

# Embedding attempts update_memory makes while other writers keep changing the same memory
MAX_EMBEDDING_ATTEMPTS = 5


async def _reload_memory(session: AsyncSession, memory_id: int) -> Memory:
    """Re-query a memory with selectinload to ensure all relationships are properly loaded

    This is the recommended async pattern per SQLAlchemy docs.
    """
    stmt = (
        select(MemoryTable)
        .where(MemoryTable.id == memory_id)
        .options(*memory_load_options())
    )
    result = await session.execute(stmt)
    return Memory.model_validate(result.scalar_one())


class PostgresMemoryRepository(
    MemorySearchMixin,
    MemoryLinksMixin,
    MemoryListingMixin,
    MemoryUsageMixin,
    MemoryReembeddingMixin,
):
    """Repository for Memory entity operations in Postgres
    """

    async def create_memory(self, user_id: UUID, memory: MemoryCreate) -> Memory:
        """Create a new memory in postgres

        Args:
            user_id: User ID,
            memory: MemoryCreate object containing the data for the memory that is
                    to be created

        Returns:
            Created Memory Object
        """
        embeddings_text = build_embedding_text(memory_data=memory)
        embeddings = await self._generate_embeddings(text=embeddings_text)

        async with self.db_adapter.session(user_id) as session:
            memory_data = memory.model_dump(exclude=set(ASSOCIATION_ID_FIELDS))
            new_memory = MemoryTable(**memory_data, user_id=user_id, embedding=embeddings)
            session.add(new_memory)
            await session.flush()

            await attach_associations(session, new_memory, memory, user_id)

            return await _reload_memory(session, new_memory.id)

    async def update_memory(
            self,
            user_id: UUID,
            memory_id: int,
            updated_memory: MemoryUpdate,
            existing_memory: Memory,
            search_fields_changed: bool,
    ) -> Memory:
        """Update a memory

        The embedding is generated before a database session is opened, so no pooled connection
        is held (idle in transaction) while the provider call is in flight. Another writer may
        change a search field in the meantime, so the write verifies that the row still produces
        the text that was embedded and, if it does not, embeds again from the current row. After
        MAX_EMBEDDING_ATTEMPTS attempts the last embedding is written as is rather than failing
        the update.

        Args:
            user_id: User ID
            memory_id: Memory ID
            updated_memory: MemoryUpdate object containing the changes to be applied
            existing_memory: Existing Memory object
            search_fields_changed: Whether search-relevant fields changed (requires embedding update)

        Returns:
            Updated Memory object

        Raises:
            NotFoundError: If memory not found
        """
        update_data = updated_memory.model_dump(
            exclude_unset=True,
            exclude=set(ASSOCIATION_ID_FIELDS),
        )

        base_memory = existing_memory
        attempt = 1
        while True:
            update_data["updated_at"] = datetime.now(UTC)

            embedding_text = None
            embeddings = None
            if search_fields_changed:
                merged_memory = base_memory.model_copy(update=update_data)
                embedding_text = build_embedding_text(memory_data=merged_memory)
                embeddings = await self._generate_embeddings(embedding_text)

            is_last_attempt = attempt >= MAX_EMBEDDING_ATTEMPTS
            if embedding_text is not None and is_last_attempt:
                logger.warning("memory kept changing while its embedding was generated, writing without verification", extra={
                    "memory_id": memory_id,
                    "user_id": user_id,
                    "attempts": attempt,
                })

            memory = await self._write_memory_update(
                user_id=user_id,
                memory_id=memory_id,
                updated_memory=updated_memory,
                update_data=update_data,
                embeddings=embeddings,
                verify_embedding_text=None if is_last_attempt else embedding_text,
            )
            if memory is not None:
                return memory

            logger.info("memory changed while its embedding was generated, embedding again", extra={
                "memory_id": memory_id,
                "user_id": user_id,
                "attempt": attempt,
            })
            attempt += 1
            base_memory = await self.get_memory_by_id(user_id=user_id, memory_id=memory_id)

    async def _write_memory_update(
            self,
            user_id: UUID,
            memory_id: int,
            updated_memory: MemoryUpdate,
            update_data: dict[str, Any],
            embeddings: list[float] | None,
            verify_embedding_text: str | None,
    ) -> Memory | None:
        """Apply one update attempt in a single transaction.

        Returns None, with nothing written, when verify_embedding_text is given and the stored row
        no longer produces that text.
        """
        column_values = update_data if embeddings is None else {**update_data, "embedding": embeddings}

        async with self.db_adapter.session(user_id) as session:
            stmt = (
                update(MemoryTable)
                .where(MemoryTable.user_id==user_id, MemoryTable.id==memory_id)
                .values(**column_values)
                .returning(MemoryTable)
            )

            try:
                result = await session.execute(stmt)
                memory_orm = result.scalar_one()

                if verify_embedding_text is not None:
                    stored_text = build_embedding_text(memory_data=Memory.model_validate(memory_orm))
                    if stored_text != verify_embedding_text:
                        await session.rollback()
                        return None

                await replace_associations(session, memory_orm, updated_memory, user_id)

                return await _reload_memory(session, memory_id)

            except NoResultFound:
                raise NotFoundError(f"Memory with id {memory_id} not found")

    async def mark_obsolete(
            self,
            user_id: UUID,
            memory_id: int,
            reason: str,
            superseded_by: int | None = None,
    ) -> bool:
        """Mark a memory as obsolete (soft delete)

        Args:
            user_id: User ID
            memory_id: Memory ID to mark as obsolete
            reason: Why the memory is being made obsolete
            superseded_by: ID of the new memory that supersedes this one (optional)

        Returns:
            True if successfully marked obsolete

        Raises:
            NotFoundError: If memory not found or doesn't belong to user
            AssociationNotFoundError: If superseded_by memory not found or doesn't belong to user
            InvalidStateTransitionError: If the memory is already obsolete
        """
        if not fits_int4(memory_id):
            raise NotFoundError(f"Memory {memory_id} not found")
        if superseded_by and not fits_int4(superseded_by):
            raise NotFoundError(f"Superseding memory {superseded_by} not found")

        async with self.db_adapter.session(user_id) as session:
            if superseded_by is not None:
                superseding_stmt = select(MemoryTable).where(
                    MemoryTable.user_id == user_id,
                    MemoryTable.id == superseded_by,
                )
                superseding_result = await session.execute(superseding_stmt)
                if not superseding_result.scalar_one_or_none():
                    raise AssociationNotFoundError(f"Superseding memory {superseded_by} not found")

            stmt = (
                update(MemoryTable)
                .where(
                    MemoryTable.user_id == user_id,
                    MemoryTable.id == memory_id,
                    MemoryTable.is_obsolete.is_(False),
                )
                .values(
                    is_obsolete=True,
                    obsolete_reason=reason,
                    superseded_by=superseded_by,
                    obsoleted_at=datetime.now(UTC),
                )
                .returning(MemoryTable)
            )

            result = await session.execute(stmt)
            obsoleted_memory = result.scalar_one_or_none()

            if not obsoleted_memory:
                existing = await session.execute(
                    select(MemoryTable.id).where(
                        MemoryTable.user_id == user_id,
                        MemoryTable.id == memory_id,
                    ),
                )
                if existing.scalar_one_or_none() is not None:
                    raise InvalidStateTransitionError(f"Memory {memory_id} is already obsolete")
                raise NotFoundError(f"Memory {memory_id} not found")

            return True

    async def restore_obsolete(self, user_id: UUID, memory_id: int) -> bool:
        """Clear obsolescence and return the memory to active search.

        Returns True only when exactly one obsolete row was updated.
        """
        if not fits_int4(memory_id):
            return False

        async with self.db_adapter.session(user_id) as session:
            stmt = (
                update(MemoryTable)
                .where(
                    MemoryTable.user_id == user_id,
                    MemoryTable.id == memory_id,
                    MemoryTable.is_obsolete.is_(True),
                )
                .values(
                    is_obsolete=False,
                    obsolete_reason=None,
                    superseded_by=None,
                    obsoleted_at=None,
                )
                .returning(MemoryTable)
            )
            result = await session.execute(stmt)
            restored = result.scalar_one_or_none()
            return restored is not None
