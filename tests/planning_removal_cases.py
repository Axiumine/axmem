"""Contracts of the revision that dropped the planning tables, run against SQLite and PostgreSQL."""

from pathlib import Path
from typing import Any

from alembic.config import Config
from sqlalchemy import inspect, text

from app.bootstrap import create_repositories
from app.models.project_models import ProjectCreate, ProjectType
from app.models.user_models import UserCreate
from tests.skill_name_uniqueness_cases import run_migration

LAST_REVISION_WITH_PLANNING = "20261018_skill_unique_name"
REVISION_BEFORE_PLANNING = "20260106_activity"
PLANNING_TABLES = ("plans", "tasks", "criteria", "task_dependencies")
PLANNING_EVENT_TYPES = ("plan", "task", "criterion", "task_dependency")
KEPT_EVENT_TYPES = ("memory", "project")


def alembic_config() -> Config:
    return Config(str(Path(__file__).parents[1] / "alembic.ini"))


def read_shapes(connection: Any) -> dict[str, dict[str, Any] | None]:
    """Columns, keys and indexes of each planning table (None when the table does not exist)."""
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    shapes: dict[str, dict[str, Any] | None] = {}
    for table in PLANNING_TABLES:
        if table not in existing:
            shapes[table] = None
            continue
        shapes[table] = {
            "columns": {
                column["name"]: (str(column["type"]), column["nullable"], str(column["default"]))
                for column in inspector.get_columns(table)
            },
            "primary_key": inspector.get_pk_constraint(table)["constrained_columns"],
            "indexes": sorted(
                (index["name"], tuple(index["column_names"]), bool(index["unique"]))
                for index in inspector.get_indexes(table)
            ),
            "foreign_keys": sorted(
                (
                    tuple(key["constrained_columns"]),
                    key["referred_table"],
                    tuple(key["referred_columns"]),
                    str(key["options"]),
                )
                for key in inspector.get_foreign_keys(table)
            ),
        }
    return shapes


async def planning_shapes(db: Any) -> dict[str, dict[str, Any] | None]:
    async with db._engine.begin() as connection:
        return await connection.run_sync(read_shapes)


def assert_planning_tables_absent(shapes: dict[str, Any]) -> None:
    assert shapes == dict.fromkeys(PLANNING_TABLES)


async def store_planning_rows_and_events(db: Any) -> str:
    """Fill every planning table plus the activity log; returns the owner's id as text."""
    repos = create_repositories(db, None, None)
    user = await repos["user"].create_user(
        UserCreate(external_id="planning-removal", name="Planning", email="planning@example.test"),
    )
    project = await repos["project"].create_project(
        user.id,
        ProjectCreate(name="Kept", description="Survives the migration", project_type=ProjectType.WORK),
    )
    owner = str(user.id)
    statements = [
        "INSERT INTO plans (id, user_id, project_id, title) VALUES (1, :owner, :project, 'plan')",
        "INSERT INTO tasks (id, user_id, plan_id, title) VALUES (1, :owner, 1, 'first'), (2, :owner, 1, 'second')",
        "INSERT INTO criteria (id, user_id, task_id, description) VALUES (1, :owner, 1, 'done')",
        "INSERT INTO task_dependencies (id, user_id, task_id, depends_on_task_id) VALUES (1, :owner, 2, 1)",
    ]
    event = text(
        "INSERT INTO activity_log (user_id, entity_type, entity_id, action, snapshot, actor, created_at) "
        "VALUES (:owner, :entity_type, 1, 'created', '{}', 'user', CURRENT_TIMESTAMP)",
    )
    async with db.system_session() as session:
        for statement in statements:
            await session.execute(text(statement), {"owner": owner, "project": project.id})
        for entity_type in (*PLANNING_EVENT_TYPES, *KEPT_EVENT_TYPES):
            await session.execute(event, {"owner": owner, "entity_type": entity_type})
    return owner


async def stored_event_types(db: Any) -> list[str]:
    async with db.system_session() as session:
        rows = await session.execute(text("SELECT entity_type FROM activity_log ORDER BY entity_type"))
        return [row[0] for row in rows]


class TestPlanningRemoval:
    async def test_upgrade_drops_the_planning_tables_and_their_activity_events(self, planning_db):
        config = alembic_config()
        await run_migration(planning_db, config, "downgrade", LAST_REVISION_WITH_PLANNING)
        assert all(await planning_shapes(planning_db)), "downgrade must bring the tables back"
        owner = await store_planning_rows_and_events(planning_db)

        await run_migration(planning_db, config, "upgrade", "head")

        assert_planning_tables_absent(await planning_shapes(planning_db))
        assert await stored_event_types(planning_db) == sorted(KEPT_EVENT_TYPES)
        async with planning_db.system_session() as session:
            kept = await session.execute(
                text("SELECT name FROM projects WHERE user_id = :owner"), {"owner": owner},
            )
            assert [row[0] for row in kept] == ["Kept"]

    async def test_downgrade_recreates_the_schema_the_earlier_revisions_produce(self, planning_db):
        config = alembic_config()
        assert_planning_tables_absent(await planning_shapes(planning_db))
        try:
            await run_migration(planning_db, config, "downgrade", LAST_REVISION_WITH_PLANNING)
            recreated = await planning_shapes(planning_db)
            # Rebuild the same revision through the original chain (planning, provenance, external_ref).
            await run_migration(planning_db, config, "downgrade", REVISION_BEFORE_PLANNING)
            assert_planning_tables_absent(await planning_shapes(planning_db))
            await run_migration(planning_db, config, "upgrade", LAST_REVISION_WITH_PLANNING)
            original = await planning_shapes(planning_db)
        finally:
            await run_migration(planning_db, config, "upgrade", "head")

        assert all(recreated.values())
        assert recreated == original
        assert_planning_tables_absent(await planning_shapes(planning_db))
