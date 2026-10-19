"""Memory associations with projects, code artifacts, documents, files and skills."""
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AssociationNotFoundError
from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.repositories.postgres.postgres_tables import (
    CodeArtifactsTable,
    DocumentsTable,
    FilesTable,
    MemoryTable,
    ProjectsTable,
    SkillsTable,
)

AssociatedTable = ProjectsTable | CodeArtifactsTable | DocumentsTable | FilesTable | SkillsTable


@dataclass(frozen=True)
class AssociationKind:
    """One kind of entity a memory can be associated with."""

    ids_field: str  # field of MemoryCreate / MemoryUpdate holding the ids
    relationship: str  # MemoryTable relationship that stores the association
    table: type[AssociatedTable]
    label: str  # plural name used in the not-found error


# Order in which associations are written
ASSOCIATION_KINDS: tuple[AssociationKind, ...] = (
    AssociationKind("project_ids", "projects", ProjectsTable, "Projects"),
    AssociationKind("code_artifact_ids", "code_artifacts", CodeArtifactsTable, "Code artifacts"),
    AssociationKind("document_ids", "documents", DocumentsTable, "Documents"),
    AssociationKind("file_ids", "files", FilesTable, "Files"),
    AssociationKind("skill_ids", "skills", SkillsTable, "Skills"),
)

# MemoryCreate / MemoryUpdate fields that are stored as associations, not as memory columns
ASSOCIATION_ID_FIELDS = frozenset(kind.ids_field for kind in ASSOCIATION_KINDS)


async def link_associations(
        session: AsyncSession,
        memory: MemoryTable,
        kind: AssociationKind,
        ids: list[int],
        user_id: UUID,
) -> None:
    """Link the memory to the user's entities of one kind, all of which must exist."""
    stmt = select(kind.table).where(
        kind.table.id.in_(ids),
        kind.table.user_id == user_id,
    )
    result = await session.execute(stmt)
    entities = cast(Sequence[AssociatedTable], result.scalars().all())

    found_ids = {entity.id for entity in entities}
    missing_ids = set(ids) - found_ids
    if missing_ids:
        raise AssociationNotFoundError(f"{kind.label} not found: {missing_ids}")

    await session.run_sync(lambda _: getattr(memory, kind.relationship).extend(entities))


async def attach_associations(
        session: AsyncSession,
        memory: MemoryTable,
        data: MemoryCreate,
        user_id: UUID,
) -> None:
    """Link a new memory to every kind of entity the creation data names."""
    for kind in ASSOCIATION_KINDS:
        ids = getattr(data, kind.ids_field)
        if ids:
            await link_associations(session, memory, kind, ids, user_id)


async def replace_associations(
        session: AsyncSession,
        memory: MemoryTable,
        data: MemoryUpdate,
        user_id: UUID,
) -> None:
    """Replace the associations of each kind the update data names (an empty list clears them)."""
    for kind in ASSOCIATION_KINDS:
        ids = getattr(data, kind.ids_field)
        if ids is None:
            continue
        # Load the relationship for manipulation (must include 'id' for refresh to work)
        await session.refresh(memory, attribute_names=["id", kind.relationship])
        getattr(memory, kind.relationship).clear()
        if ids:
            await link_associations(session, memory, kind, ids, user_id)
