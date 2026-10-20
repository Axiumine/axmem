"""Association of a memory with its project scope"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.repositories.sqlite.sqlite_tables import (
    MemoryTable,
    ProjectsTable,
)

# The scope is stored as an association with a project row, not as a memory column
ASSOCIATION_REQUEST_FIELD = "project"


def association_request_fields() -> set[str]:
    """Names of the request fields that are persisted as associations, not as memory columns."""
    return {ASSOCIATION_REQUEST_FIELD}


async def _scope_row(session: AsyncSession, user_id: UUID, name: str) -> ProjectsTable:
    """The tenant's project row of that name, created on first use.

    Rows are per tenant, so two tenants using the same scope name never share one. Two
    first uses racing each other can create the row twice; scopes are matched by name, so
    the duplicate is harmless.
    """
    result = await session.execute(
        select(ProjectsTable)
        .where(ProjectsTable.user_id == str(user_id), ProjectsTable.name == name)
        .order_by(ProjectsTable.id)
        .limit(1),
    )
    row = result.scalars().first()
    if row is None:
        row = ProjectsTable(user_id=str(user_id), name=name, description="", status="active")
        session.add(row)
        await session.flush()
    return row


async def _link(session: AsyncSession, memory: MemoryTable, user_id: UUID, name: str) -> None:
    row = await _scope_row(session, user_id, name)
    await session.run_sync(lambda _sync_session: memory.projects.append(row))


async def add_associations(
    session: AsyncSession,
    memory: MemoryTable,
    created: MemoryCreate,
    user_id: UUID,
) -> None:
    """Put a new memory in the scope its creation request names."""
    if created.project:
        await _link(session, memory, user_id, created.project)


async def replace_associations(
    session: AsyncSession,
    memory: MemoryTable,
    updated: MemoryUpdate,
    user_id: UUID,
) -> None:
    """Replace the scope when the update names one (an empty name clears it)."""
    if updated.project is None:
        return
    await session.refresh(memory, attribute_names=["id", "projects"])
    memory.projects.clear()
    if updated.project:
        await _link(session, memory, user_id, updated.project)
