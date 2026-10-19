"""Drop the plans, tasks, criteria and task_dependencies tables.

Revision ID: 20261019_drop_planning
Revises: 20261018_skill_unique_name

The planning feature was removed from the application. This revision deletes its data:

* the four planning tables (they are not referenced by any other table);
* the ``activity_log`` rows that recorded plan, task, criterion and task_dependency
  events, because the application no longer knows those entity types and could not
  read the rows back.

Downgrade recreates the empty tables exactly as the previous revisions left them
(provenance columns, ``external_ref`` and every index included), so the older
revisions can still be downgraded through this one. It restores the schema only: the
rows and activity events deleted by the upgrade are gone for good.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261019_drop_planning"
down_revision: str | Sequence[str] | None = "20261018_skill_unique_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLANNING_ENTITY_TYPES = ("plan", "task", "criterion", "task_dependency")


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


def _timestamp(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    """Delete planning activity events, then drop the tables child-first."""
    activity_log = sa.table("activity_log", sa.column("entity_type", sa.String()))
    op.execute(activity_log.delete().where(activity_log.c.entity_type.in_(PLANNING_ENTITY_TYPES)))

    op.drop_table("task_dependencies")
    op.drop_table("criteria")
    op.drop_table("tasks")
    op.drop_table("plans")


def downgrade() -> None:
    """Recreate the empty planning tables; the dropped rows and events are not restored."""
    user_id_type = _user_id_type()

    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", user_id_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        *_provenance_columns(),
        sa.Column("external_ref", sa.String(255), nullable=True),
    )
    op.create_index("ix_plans_user_id", "plans", ["user_id"])
    op.create_index("ix_plans_project_id", "plans", ["project_id"])
    op.create_index("ix_plans_status", "plans", ["status"])
    op.create_index("ix_plans_encoding_agent", "plans", ["encoding_agent"])
    op.create_index("ix_plans_user_external_ref", "plans", ["user_id", "external_ref"], unique=True)

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", user_id_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="todo"),
        sa.Column("priority", sa.String(5), nullable=False, server_default="P2"),
        sa.Column("assigned_agent", sa.String(200), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        *_provenance_columns(),
    )
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])
    op.create_index("ix_tasks_plan_id", "tasks", ["plan_id"])
    op.create_index("ix_tasks_state", "tasks", ["state"])
    op.create_index("ix_tasks_priority", "tasks", ["priority"])
    op.create_index("ix_tasks_assigned_agent", "tasks", ["assigned_agent"])
    op.create_index("ix_tasks_encoding_agent", "tasks", ["encoding_agent"])

    op.create_table(
        "criteria",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", user_id_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("met", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("met_at", sa.DateTime(timezone=True), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
    )
    op.create_index("ix_criteria_task_id", "criteria", ["task_id"])
    op.create_index("ix_criteria_met", "criteria", ["met"])

    op.create_table(
        "task_dependencies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", user_id_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "depends_on_task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False,
        ),
        _timestamp("created_at"),
    )
    op.create_index("ix_task_deps_unique", "task_dependencies", ["task_id", "depends_on_task_id"], unique=True)
