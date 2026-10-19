"""Helpers shared by the tests of the revisions that removed a feature's tables."""

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command


def alembic_config() -> Config:
    return Config(str(Path(__file__).parents[1] / "alembic.ini"))


async def run_migration(db: Any, config: Config, direction: str, revision: str) -> None:
    """Run an Alembic ``upgrade``/``downgrade`` on the test database's connection."""

    def migrate(connection: Any) -> None:
        config.attributes["connection"] = connection
        getattr(command, direction)(config, revision)

    async with db._engine.begin() as connection:
        await connection.run_sync(migrate)


def read_shapes(connection: Any, tables: Iterable[str]) -> dict[str, dict[str, Any] | None]:
    """Columns, keys and indexes of each table (None when the table does not exist)."""
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    shapes: dict[str, dict[str, Any] | None] = {}
    for table in tables:
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


async def table_shapes(db: Any, tables: Iterable[str]) -> dict[str, dict[str, Any] | None]:
    async with db._engine.begin() as connection:
        return await connection.run_sync(read_shapes, tuple(tables))


async def stored_event_types(db: Any) -> list[str]:
    async with db.system_session() as session:
        rows = await session.execute(text("SELECT entity_type FROM activity_log ORDER BY entity_type"))
        return [row[0] for row in rows]
