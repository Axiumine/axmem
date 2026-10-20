"""Drop the activity_log table and the memory access counters.

Revision ID: 20261020_drop_activity
Revises: 20261019_drop_entities

The activity bus, the activity log and the memory access tracking were removed from the
application. This revision deletes their data:

* ``activity_log`` with all its indexes (nothing else references it);
* ``memories.access_count`` and ``memories.last_accessed_at`` with the
  ``ix_memories_last_accessed_at`` index. Only the access handler wrote them and nothing
  read them, so they would stay at zero and null.

Downgrade recreates the empty ``activity_log`` table and the two memory columns exactly
as ``20260106_activity`` and ``20260704_usage_tracking`` left them, so the older revisions
can still be downgraded through this one. It restores the schema only: the activity events
and the access counters deleted by the upgrade are gone for good (every restored memory
reads as never accessed).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261020_drop_activity"
down_revision: str | Sequence[str] | None = "20261019_drop_entities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SINGLE_COLUMN_INDEXES = ("user_id", "entity_type", "entity_id", "action", "actor", "created_at")


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _user_id_type() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True) if _is_postgres() else sa.String(36)


def upgrade() -> None:
    """Drop the memory access counters, then the activity log."""
    # SQLite refuses to drop an indexed column, and PostgreSQL would drop the index with it.
    op.drop_index("ix_memories_last_accessed_at", table_name="memories")
    op.drop_column("memories", "last_accessed_at")
    op.drop_column("memories", "access_count")

    op.drop_table("activity_log")


def downgrade() -> None:
    """Recreate the empty table and the columns; the dropped events and counters are not restored."""
    op.create_table(
        "activity_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", _user_id_type(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(50), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in SINGLE_COLUMN_INDEXES:
        op.create_index(f"ix_activity_log_{column}", "activity_log", [column])
    op.create_index("ix_activity_log_user_entity", "activity_log", ["user_id", "entity_type", "entity_id"])
    op.create_index("ix_activity_log_user_created", "activity_log", ["user_id", "created_at"])

    op.add_column("memories", sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("memories", sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_memories_last_accessed_at", "memories", ["last_accessed_at"])
