"""Drop the files table and its two association tables.

Revision ID: 20261019_drop_files
Revises: 20261019_drop_skills

The files feature was removed from the application. This revision deletes its data:

* ``memory_file_association``, ``entity_file_association`` and ``files`` (the binary
  content included). Nothing else references them any more: ``skill_file_association`` went
  with the skills in the previous revision; memories, entities and projects stay;
* the ``activity_log`` rows that recorded file events, because the application no longer
  knows that entity type and could not read the rows back.

Downgrade recreates the empty tables exactly as the previous revisions left them
(provenance columns, the ``encoding_agent`` index and every other index included), so the
older revisions can still be downgraded through this one. It restores the schema only: the
files and the activity events deleted by the upgrade are gone for good.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261019_drop_files"
down_revision: str | Sequence[str] | None = "20261019_drop_skills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FILE_ENTITY_TYPE = "file"
ASSOCIATION_TABLES = (
    "entity_file_association",
    "memory_file_association",
)


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _user_id_type() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True) if _is_postgres() else sa.String(36)


def _provenance_columns() -> list[sa.Column]:
    """The nine optional provenance columns that 20260408_provenance_all added."""
    source_files = postgresql.ARRAY(sa.String()) if _is_postgres() else sa.JSON()
    return [
        sa.Column("source_repo", sa.Text(), nullable=True),
        sa.Column("source_files", source_files, nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("encoding_agent", sa.Text(), nullable=True),
        sa.Column("encoding_version", sa.Text(), nullable=True),
        sa.Column("agent_id", sa.Text(), nullable=True),
        sa.Column("agent_version", sa.Text(), nullable=True),
        sa.Column("agent_model", sa.Text(), nullable=True),
    ]


def _file_columns() -> list[sa.Column]:
    """Columns of ``files`` as 20260315_files created them, plus the provenance columns."""
    if _is_postgres():
        tags = sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}")
    else:
        tags = sa.Column("tags", sa.JSON(), nullable=False, server_default="[]")
    return [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", _user_id_type(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        tags,
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        *_provenance_columns(),
    ]


def upgrade() -> None:
    """Delete file activity events, then drop the association tables and the files table."""
    activity_log = sa.table("activity_log", sa.column("entity_type", sa.String()))
    op.execute(activity_log.delete().where(activity_log.c.entity_type == FILE_ENTITY_TYPE))

    for association in ASSOCIATION_TABLES:
        op.drop_table(association)
    op.drop_table("files")


def downgrade() -> None:
    """Recreate the empty file tables; the dropped files and events are not restored."""
    op.create_table("files", *_file_columns())
    op.create_index("ix_files_user_id", "files", ["user_id"])
    op.create_index("ix_files_project_id", "files", ["project_id"])
    op.create_index("ix_files_mime_type", "files", ["mime_type"])
    op.create_index("ix_files_encoding_agent", "files", ["encoding_agent"])
    if _is_postgres():
        op.create_index("ix_files_tags", "files", ["tags"], postgresql_using="gin")

    op.create_table(
        "memory_file_association",
        sa.Column("memory_id", sa.Integer(), sa.ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("files.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "entity_file_association",
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("files.id", ondelete="CASCADE"), primary_key=True),
    )
