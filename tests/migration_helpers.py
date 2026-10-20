"""Helpers shared by the tests of the revisions that removed a feature's tables."""

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

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


@asynccontextmanager
async def upgraded_to(db: Any, config: Config, revision: str) -> AsyncIterator[None]:
    """Hold the database at ``revision`` for the block, then finish at ``head`` even if it fails.

    The later revisions drop the activity log, which these tests read, so each one
    inspects its own revision rather than the head.
    """
    await run_migration(db, config, "upgrade", revision)
    try:
        yield
    finally:
        await run_migration(db, config, "upgrade", "head")


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


async def original_chain_shapes(
    db: Any, revision: str, tables: Iterable[str], scratch_schema: str,
) -> dict[str, dict[str, Any] | None]:
    """The tables as the original revision chain leaves them at ``revision``, built in a scratch database.

    Only the original revisions run here, so the downgrade of a later revision cannot vouch for
    itself. PostgreSQL builds the chain in a scratch schema that a second engine makes its default
    schema (the inspector and Alembic resolve the default schema once per engine, so a
    ``search_path`` set on the shared engine would not be seen); SQLite builds it in a private
    in-memory database.
    """
    config = alembic_config()
    wanted = tuple(tables)

    def build(connection: Any) -> dict[str, dict[str, Any] | None]:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)
        return read_shapes(connection, wanted)

    if db._engine.dialect.name == "postgresql":
        async with db._engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {scratch_schema} CASCADE"))
            await connection.execute(text(f"CREATE SCHEMA {scratch_schema}"))
            # Alembic finds its version table through the search path, and an empty one
            # here stops it from reading the real database's table behind the scratch one.
            await connection.execute(
                text(f"CREATE TABLE {scratch_schema}.alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"),
            )
        scratch = create_async_engine(
            db._engine.url,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": f"{scratch_schema},public"}},
        )
        try:
            async with scratch.begin() as connection:
                return await connection.run_sync(build)
        finally:
            await scratch.dispose()
            async with db._engine.begin() as connection:
                await connection.execute(text(f"DROP SCHEMA IF EXISTS {scratch_schema} CASCADE"))

    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as connection:
            return await connection.run_sync(build)
    finally:
        await engine.dispose()
