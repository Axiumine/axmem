"""Drop the documents and code artifacts tables and their two association tables.

Revision ID: 20261019_drop_docs_artifacts
Revises: 20261019_drop_files

The documents and code artifacts features were removed from the application. This
revision deletes their data:

* ``memory_document_association``, ``memory_code_artifact_association``, ``documents``
  and ``code_artifacts``. Nothing else references them any more: the skill association
  tables went with the skills, ``projects`` and ``memories`` stay;
* the ``activity_log`` rows that recorded document and code artifact events, because the
  application no longer knows those entity types and could not read the rows back.

Downgrade recreates the empty tables exactly as the earlier revisions left them (the
initial schema plus the provenance columns and the ``encoding_agent`` index of
``20260408_provenance_all``), so the older revisions can still be downgraded through this
one. It restores the schema only: the documents, code artifacts, memory links and
activity events deleted by the upgrade are gone for good.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261019_drop_docs_artifacts"
down_revision: str | Sequence[str] | None = "20261019_drop_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REMOVED_ENTITY_TYPES = ("document", "code_artifact")
ASSOCIATION_TABLES = (
    "memory_document_association",
    "memory_code_artifact_association",
)


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _user_id_type() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True) if _is_postgres() else sa.String(36)


def _tags_column() -> sa.Column:
    return sa.Column("tags", postgresql.ARRAY(sa.String()) if _is_postgres() else sa.JSON(), nullable=False)


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


def _owner_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", _user_id_type(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
    ]


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    """Delete the document and code artifact activity events, then drop the four tables."""
    activity_log = sa.table("activity_log", sa.column("entity_type", sa.String()))
    op.execute(activity_log.delete().where(activity_log.c.entity_type.in_(REMOVED_ENTITY_TYPES)))

    for association in ASSOCIATION_TABLES:
        op.drop_table(association)
    op.drop_table("documents")
    op.drop_table("code_artifacts")


def downgrade() -> None:
    """Recreate the empty tables; the dropped rows and events are not restored."""
    op.create_table(
        "code_artifacts",
        *_owner_columns(),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("language", sa.String(100), nullable=False),
        _tags_column(),
        *_timestamp_columns(),
        *_provenance_columns(),
    )
    op.create_index("ix_code_artifacts_language", "code_artifacts", ["language"])
    op.create_index("ix_code_artifacts_project_id", "code_artifacts", ["project_id"])
    op.create_index("ix_code_artifacts_user_id", "code_artifacts", ["user_id"])
    op.create_index("ix_code_artifacts_encoding_agent", "code_artifacts", ["encoding_agent"])

    op.create_table(
        "documents",
        *_owner_columns(),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("document_type", sa.String(100), nullable=True),
        sa.Column("filename", sa.String(500), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        _tags_column(),
        *_timestamp_columns(),
        *_provenance_columns(),
    )
    op.create_index("ix_documents_document_type", "documents", ["document_type"])
    op.create_index("ix_documents_project_id", "documents", ["project_id"])
    op.create_index("ix_documents_user_id", "documents", ["user_id"])
    op.create_index("ix_documents_encoding_agent", "documents", ["encoding_agent"])

    op.create_table(
        "memory_code_artifact_association",
        sa.Column("memory_id", sa.Integer(), sa.ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "code_artifact_id",
            sa.Integer(),
            sa.ForeignKey("code_artifacts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "memory_document_association",
        sa.Column("memory_id", sa.Integer(), sa.ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    )
