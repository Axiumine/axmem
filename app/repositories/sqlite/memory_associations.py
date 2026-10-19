"""Association of a memory with projects, code artifacts, documents and files"""
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AssociationNotFoundError
from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.repositories.sqlite.sqlite_tables import (
    CodeArtifactsTable,
    DocumentsTable,
    FilesTable,
    MemoryTable,
    ProjectsTable,
)


@dataclass(frozen=True)
class MemoryAssociation:
    """One kind of object a memory can be associated with."""

    request_field: str  # id list on MemoryCreate and MemoryUpdate
    attribute: str  # relationship on MemoryTable
    table: Any  # table of the associated objects
    label: str  # name used in the not-found error


MEMORY_ASSOCIATIONS = (
    MemoryAssociation("project_ids", "projects", ProjectsTable, "Projects"),
    MemoryAssociation("code_artifact_ids", "code_artifacts", CodeArtifactsTable, "Code artifacts"),
    MemoryAssociation("document_ids", "documents", DocumentsTable, "Documents"),
    MemoryAssociation("file_ids", "files", FilesTable, "Files"),
)


def association_request_fields() -> set[str]:
    """Names of the id lists that are persisted as associations, not as memory columns."""
    return {association.request_field for association in MEMORY_ASSOCIATIONS}


async def _link(
    session: AsyncSession,
    memory: MemoryTable,
    association: MemoryAssociation,
    ids: list[int],
    user_id: UUID,
) -> None:
    """Link the memory to the user's objects with these ids, all of which must exist."""
    table = association.table
    result = await session.execute(
        select(table).where(table.id.in_(ids), table.user_id == str(user_id)),
    )
    found = result.scalars().all()

    missing_ids = set(ids) - {row.id for row in found}
    if missing_ids:
        raise AssociationNotFoundError(f"{association.label} not found: {missing_ids}")

    await session.run_sync(
        lambda _sync_session: getattr(memory, association.attribute).extend(found),
    )


async def add_associations(
    session: AsyncSession,
    memory: MemoryTable,
    created: MemoryCreate,
    user_id: UUID,
) -> None:
    """Link a new memory to everything its creation request names."""
    for association in MEMORY_ASSOCIATIONS:
        ids = getattr(created, association.request_field)
        if ids:
            await _link(session, memory, association, ids, user_id)


async def replace_associations(
    session: AsyncSession,
    memory: MemoryTable,
    updated: MemoryUpdate,
    user_id: UUID,
) -> None:
    """Replace the associations of every kind the update request provides (an empty list clears)."""
    for association in MEMORY_ASSOCIATIONS:
        ids = getattr(updated, association.request_field)
        if ids is not None:
            await session.refresh(memory, attribute_names=["id", association.attribute])
            getattr(memory, association.attribute).clear()
            if ids:
                await _link(session, memory, association, ids, user_id)
