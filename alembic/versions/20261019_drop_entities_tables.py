"""Drop the entities, entity_relationships and entity association tables.

Revision ID: 20261019_drop_entities
Revises: 20261019_drop_docs_artifacts

The entities feature was removed from the application. This revision deletes its data:

* ``memory_entity_association``, ``entity_project_association``, ``entity_relationships``
  and ``entities``. Nothing else references them any more: the file association table
  went with the files, ``projects`` and ``memories`` stay;
* the ``activity_log`` rows that recorded entity, entity relationship, entity-to-memory
  link and entity-to-project link events, because the application no longer knows those
  entity types and could not read the rows back.

Downgrade recreates the empty tables exactly as the earlier revisions left them (the
initial schema plus the ``aka`` column of ``20251216143413`` and the provenance columns and
``encoding_agent`` indexes of ``20260408_provenance_all``), so the older revisions can
still be downgraded through this one. It restores the schema only: the entities,
relationships, memory and project links and activity events deleted by the upgrade are
gone for good.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261019_drop_entities"
down_revision: str | Sequence[str] | None = "20261019_drop_docs_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REMOVED_ENTITY_TYPES = (
    "entity",
    "entity_memory_link",
    "entity_relationship",
    "entity_project_link",
)
DROPPED_TABLES = (
    "memory_entity_association",
    "entity_project_association",
    "entity_relationships",
    "entities",
)


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _user_id_type() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True) if _is_postgres() else sa.String(36)


def _string_list_type() -> sa.types.TypeEngine:
    return postgresql.ARRAY(sa.String()) if _is_postgres() else sa.JSON()


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _provenance_columns(*, with_confidence: bool) -> list[sa.Column]:
    """The provenance columns that 20260408_provenance_all added (relationships have no ``confidence``)."""
    columns = [
        sa.Column("source_repo", sa.Text(), nullable=True),
        sa.Column("source_files", _string_list_type(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
    ]
    if with_confidence:
        columns.append(sa.Column("confidence", sa.Float(), nullable=True))
    columns += [
        sa.Column("encoding_agent", sa.Text(), nullable=True),
        sa.Column("encoding_version", sa.Text(), nullable=True),
        sa.Column("agent_id", sa.Text(), nullable=True),
        sa.Column("agent_version", sa.Text(), nullable=True),
        sa.Column("agent_model", sa.Text(), nullable=True),
    ]
    return columns


def upgrade() -> None:
    """Delete the entity activity events, then drop the four tables."""
    activity_log = sa.table("activity_log", sa.column("entity_type", sa.String()))
    op.execute(activity_log.delete().where(activity_log.c.entity_type.in_(REMOVED_ENTITY_TYPES)))

    for table in DROPPED_TABLES:
        op.drop_table(table)


def downgrade() -> None:
    """Recreate the empty tables; the dropped rows and events are not restored."""
    op.create_table(
        "entities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", _user_id_type(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("custom_type", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("tags", _string_list_type(), nullable=False),
        sa.Column("aka", _string_list_type(), nullable=False, server_default="{}" if _is_postgres() else "[]"),
        *_timestamp_columns(),
        *_provenance_columns(with_confidence=True),
    )
    op.create_index("ix_entities_entity_type", "entities", ["entity_type"])
    op.create_index("ix_entities_name", "entities", ["name"])
    op.create_index("ix_entities_user_id", "entities", ["user_id"])
    if _is_postgres():
        op.create_index("ix_entities_aka", "entities", ["aka"], postgresql_using="gin")
    op.create_index("ix_entities_encoding_agent", "entities", ["encoding_agent"])

    op.create_table(
        "entity_relationships",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", _user_id_type(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship_type", sa.String(100), nullable=False),
        sa.Column("strength", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "relationship_metadata",
            postgresql.JSONB() if _is_postgres() else sa.JSON(),
            nullable=True,
        ),
        *_timestamp_columns(),
        *_provenance_columns(with_confidence=False),
    )
    op.create_index("ix_entity_relationships_relationship_type", "entity_relationships", ["relationship_type"])
    op.create_index("ix_entity_relationships_source_entity_id", "entity_relationships", ["source_entity_id"])
    op.create_index("ix_entity_relationships_target_entity_id", "entity_relationships", ["target_entity_id"])
    op.create_index(
        "ix_entity_relationships_unique",
        "entity_relationships",
        ["source_entity_id", "target_entity_id", "relationship_type"],
        unique=True,
    )
    op.create_index("ix_entity_relationships_user_id", "entity_relationships", ["user_id"])
    op.create_index("ix_entity_relationships_encoding_agent", "entity_relationships", ["encoding_agent"])

    op.create_table(
        "entity_project_association",
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "memory_entity_association",
        sa.Column("memory_id", sa.Integer(), sa.ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True),
    )
