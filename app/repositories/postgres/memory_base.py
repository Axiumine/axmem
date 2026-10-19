"""Shared state, id guards and query fragments of the Postgres memory repository mixins."""
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, Exists, Result, select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.base import ExecutableOption

from app.exceptions import NotFoundError
from app.models.memory_models import Memory
from app.protocols.embedding_protocol import EmbeddingsAdapter
from app.repositories.embeddings.reranker_adapter import RerankAdapter
from app.repositories.postgres.postgres_adapter import PostgresDatabaseAdapter
from app.repositories.postgres.postgres_tables import (
    MemoryTable,
    memory_project_association,
)

# memories.id is an INTEGER column. asyncpg refuses to encode a larger value instead of matching
# no row, which would surface as a DataError (HTTP 500) where SQLite simply reports "not found".
_INT4_MIN = -(2**31)
_INT4_MAX = 2**31 - 1


def fits_int4(*values: int) -> bool:
    return all(_INT4_MIN <= value <= _INT4_MAX for value in values)


def affected_rows(result: Result[Any]) -> int:
    """Rows matched by an UPDATE or DELETE (Session.execute types its result without rowcount)."""
    return cast(CursorResult[Any], result).rowcount


def memory_load_options() -> list[ExecutableOption]:
    """Eager loads for every relationship Memory.model_validate reads (no lazy loading in async)."""
    return [
        selectinload(MemoryTable.projects),
        selectinload(MemoryTable.linked_memories),
        selectinload(MemoryTable.linking_memories),
        selectinload(MemoryTable.code_artifacts),
        selectinload(MemoryTable.documents),
    ]


def project_membership_filter(project_ids: list[int]) -> Exists:
    """Condition: the memory belongs to at least one of project_ids."""
    return select(memory_project_association.c.memory_id).where(
        memory_project_association.c.memory_id == MemoryTable.id,
        memory_project_association.c.project_id.in_(project_ids),
    ).exists()


class MemoryRepositoryBase:
    """Adapters and single-memory reads that every Postgres memory repository mixin builds on."""

    def __init__(
            self,
            db_adapter: PostgresDatabaseAdapter,
            embedding_adapter: EmbeddingsAdapter,
            rerank_adapter: RerankAdapter | None = None,
    ):
        self.db_adapter = db_adapter
        self.embedding_adapter = embedding_adapter
        self.rerank_adapter = rerank_adapter

    async def get_memory_by_id(self, user_id: UUID, memory_id: int) -> Memory:
        """Retrieves memory by ID

        Args:
            user_id: User ID
            memory_id: Id of the memory to be returned

        Returns:
            Memory object

        Raises:
            NotFoundError: If the memory does not exist or is not owned by the user
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
        if not fits_int4(memory_id):
            raise NotFoundError(f"Memory with id {memory_id} not found")

        stmt = (
            select(MemoryTable)
            .where(MemoryTable.user_id==user_id, MemoryTable.id==memory_id)
            .options(*memory_load_options())
        )

        async with self.db_adapter.session(user_id) as session:
            result = await session.execute(stmt)
            memory_orm = result.scalar_one_or_none()

            if memory_orm:
                return memory_orm
            raise NotFoundError(f"Memory with id {memory_id} not found")

    async def _generate_embeddings(self, text: str) -> list[float]:
        return await self.embedding_adapter.generate_embedding(text=text)
