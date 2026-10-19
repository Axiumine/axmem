"""Drop the skills table and its four association tables.

Revision ID: 20261019_drop_skills
Revises: 20261019_drop_planning

The skills feature was removed from the application. This revision deletes its data:

* ``memory_skill_association``, ``skill_file_association``,
  ``skill_code_artifact_association``, ``skill_document_association`` and ``skills``
  (nothing else references them; memories, files, code artifacts and documents stay);
* on SQLite the ``vec_skills`` virtual table that held the skill embeddings (the
  application created it at start-up, it was never an Alembic table);
* the ``activity_log`` rows that recorded skill events, because the application no
  longer knows that entity type and could not read the rows back.

Downgrade recreates the empty tables exactly as the previous revisions left them
(provenance columns, the unique ``(user_id, name)`` index and every other index
included), so the older revisions can still be downgraded through this one. It restores
the schema only: the rows and activity events deleted by the upgrade are gone for good.
SQLite's ``vec_skills`` is not recreated here, because the application version that
needs it creates it at start-up with ``IF NOT EXISTS``, as it always did.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.config.settings import settings

revision: str = "20261019_drop_skills"
down_revision: str | Sequence[str] | None = "20261019_drop_planning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SKILL_ENTITY_TYPE = "skill"
ASSOCIATION_TABLES = (
    "skill_document_association",
    "skill_code_artifact_association",
    "skill_file_association",
    "memory_skill_association",
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


def _skill_columns() -> list[sa.Column]:
    """Columns of ``skills`` as 20260321_skills created them (embedding on PostgreSQL only)."""
    if _is_postgres():
        from pgvector.sqlalchemy import Vector

        tags = sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}")
        allowed_tools = sa.Column("allowed_tools", postgresql.ARRAY(sa.String()), nullable=True)
        skill_metadata = sa.Column("skill_metadata", postgresql.JSONB(), nullable=True)
        embedding = [sa.Column("embedding", Vector(settings.EMBEDDING_DIMENSIONS), nullable=False)]
    else:
        tags = sa.Column("tags", sa.JSON(), nullable=False, server_default="[]")
        allowed_tools = sa.Column("allowed_tools", sa.JSON(), nullable=True)
        skill_metadata = sa.Column("skill_metadata", sa.JSON(), nullable=True)
        embedding = []
    return [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", _user_id_type(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(1024), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("license", sa.String(100), nullable=True),
        sa.Column("compatibility", sa.String(500), nullable=True),
        allowed_tools,
        skill_metadata,
        tags,
        sa.Column("importance", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        *embedding,
        *_provenance_columns(),
    ]


def upgrade() -> None:
    """Delete skill activity events, then drop the vector table and the tables child-first."""
    activity_log = sa.table("activity_log", sa.column("entity_type", sa.String()))
    op.execute(activity_log.delete().where(activity_log.c.entity_type == SKILL_ENTITY_TYPE))

    if not _is_postgres():
        op.execute("DROP TABLE IF EXISTS vec_skills")

    for association in ASSOCIATION_TABLES:
        op.drop_table(association)
    op.drop_table("skills")


def downgrade() -> None:
    """Recreate the empty skill tables; the dropped rows and events are not restored."""
    op.create_table("skills", *_skill_columns())
    op.create_index("ix_skills_user_id", "skills", ["user_id"])
    op.create_index("ix_skills_project_id", "skills", ["project_id"])
    op.create_index("ix_skills_name", "skills", ["name"])
    op.create_index("ix_skills_importance", "skills", ["importance"])
    op.create_index("ix_skills_encoding_agent", "skills", ["encoding_agent"])
    op.create_index("ix_skills_user_name", "skills", ["user_id", "name"], unique=True)
    if _is_postgres():
        op.create_index("ix_skills_tags", "skills", ["tags"], postgresql_using="gin")

    op.create_table(
        "memory_skill_association",
        sa.Column("memory_id", sa.Integer(), sa.ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "skill_file_association",
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("files.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "skill_code_artifact_association",
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "code_artifact_id", sa.Integer(), sa.ForeignKey("code_artifacts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "skill_document_association",
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    )
