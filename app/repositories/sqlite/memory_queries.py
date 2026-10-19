"""Query helpers shared by the SQLite memory repository mixins"""
from typing import Any, cast
from uuid import UUID

import sqlite_vec
from sqlalchemy import Result, Select, select, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.repositories.sqlite.sqlite_tables import MemoryTable


def with_relationships(stmt: Select[tuple[MemoryTable]]) -> Select[tuple[MemoryTable]]:
    """Eager-load every relationship that the Memory model reads."""
    return stmt.options(
        selectinload(MemoryTable.projects),
        selectinload(MemoryTable.linked_memories),
        selectinload(MemoryTable.linking_memories),
    )


async def reload_memory(session: AsyncSession, memory_id: int) -> MemoryTable:
    """Re-query a memory so that all its relationships are loaded after a write."""
    result = await session.execute(
        with_relationships(select(MemoryTable).where(MemoryTable.id == memory_id)),
    )
    return result.scalar_one()


async def load_memories_in_order(session: AsyncSession, memory_ids: list[int]) -> list[MemoryTable]:
    """Load the memories with their relationships, in the order of memory_ids.

    Ids without a row are left out.
    """
    result = await session.execute(
        with_relationships(select(MemoryTable).where(MemoryTable.id.in_(memory_ids))),
    )
    memory_by_id = {memory.id: memory for memory in result.scalars().all()}
    return [memory_by_id[memory_id] for memory_id in memory_ids if memory_id in memory_by_id]


async def fetch_owned_embedding(
    session: AsyncSession,
    user_id: UUID,
    memory_id: int,
) -> bytes | None:
    """Stored embedding of a memory the user owns, or None when there is none.

    vec_memories has no user_id, so ownership is checked against memories:
    another user's embedding must never be used as the query vector.
    """
    result = await session.execute(
        text(
            "SELECT embedding FROM vec_memories WHERE memory_id = :memory_id "
            "AND EXISTS (SELECT 1 FROM memories WHERE id = :memory_pk AND user_id = :user_id)",
        ),
        {"memory_id": str(memory_id), "memory_pk": memory_id, "user_id": str(user_id)},
    )
    row = result.fetchone()
    return row[0] if row else None


async def insert_embedding(session: AsyncSession, memory_id: int, embedding: list[float]) -> None:
    """Store the embedding of a memory that has none in vec_memories."""
    await session.execute(
        text("INSERT INTO vec_memories (memory_id, embedding) VALUES (:memory_id, :embedding)"),
        {"memory_id": str(memory_id), "embedding": sqlite_vec.serialize_float32(embedding)},
    )


def affected_rows(result: Result[Any]) -> int:
    """Rows matched by an UPDATE or DELETE (the async session types its result without rowcount)."""
    return cast(CursorResult[Any], result).rowcount
