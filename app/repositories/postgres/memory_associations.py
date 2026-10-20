"""Association of a memory with its project scope."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.repositories.postgres.postgres_tables import (
    MemoryTable,
    ProjectsTable,
)

# MemoryCreate / MemoryUpdate fields that are stored as associations, not as memory columns
ASSOCIATION_ID_FIELDS = frozenset({"project"})


async def _scope_row(session: AsyncSession, user_id: UUID, name: str) -> ProjectsTable:
    """The tenant's project row of that name, created on first use.

    Rows are per tenant, so two tenants using the same scope name never share one. Two
    first uses racing each other can create the row twice; scopes are matched by name, so
    the duplicate is harmless.
    """
    result = await session.execute(
        select(ProjectsTable)
        .where(ProjectsTable.user_id == user_id, ProjectsTable.name == name)
        .order_by(ProjectsTable.id)
        .limit(1),
    )
    row = result.scalars().first()
    if row is None:
        row = ProjectsTable(user_id=user_id, name=name, description="", status="active")
        session.add(row)
        await session.flush()
    return row


async def _link(session: AsyncSession, memory: MemoryTable, user_id: UUID, name: str) -> None:
    row = await _scope_row(session, user_id, name)
    await session.run_sync(lambda _: memory.projects.append(row))


async def attach_associations(
        session: AsyncSession,
        memory: MemoryTable,
        data: MemoryCreate,
        user_id: UUID,
) -> None:
    """Put a new memory in the scope its creation data names."""
    if data.project:
        await _link(session, memory, user_id, data.project)


async def replace_associations(
        session: AsyncSession,
        memory: MemoryTable,
        data: MemoryUpdate,
        user_id: UUID,
) -> None:
    """Replace the scope when the update data names one (an empty name clears it)."""
    if data.project is None:
        return
    # Load the relationship for manipulation (must include 'id' for refresh to work)
    await session.refresh(memory, attribute_names=["id", "projects"])
    memory.projects.clear()
    if data.project:
        await _link(session, memory, user_id, data.project)
