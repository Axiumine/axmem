"""Contracts of the revision that dropped the entity tables, run on SQLite and PostgreSQL."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.bootstrap import create_repositories
from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.models.project_models import ProjectCreate, ProjectType
from app.models.user_models import UserCreate
from tests.migration_helpers import (
    alembic_config,
    read_shapes,
    run_migration,
    stored_event_types,
    table_shapes,
)

LAST_REVISION_WITH_ENTITIES = "20261019_drop_docs_artifacts"
REMOVED_TABLES = (
    "entities",
    "entity_relationships",
    "entity_project_association",
    "memory_entity_association",
)
REMOVED_EVENT_TYPES = ("entity", "entity_memory_link", "entity_relationship", "entity_project_link")
KEPT_EVENT_TYPES = ("link", "memory", "project")
SCRATCH_SCHEMA = "scratch_entities"

# The tables are not part of the application any more: the revision's downgrade brings
# them back, so rows go in with SQL (PostgreSQL has array columns, SQLite stores JSON).
EMPTY_LIST = {"postgresql": "'{}'", "sqlite": "'[]'"}
INSERT_ENTITY = (
    "INSERT INTO entities (id, user_id, name, entity_type, tags, aka, created_at, updated_at) "
    "VALUES (:id, :owner, :name, 'Organization', {empty}, {empty}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
)
INSERT_RELATIONSHIP = (
    "INSERT INTO entity_relationships (user_id, source_entity_id, target_entity_id, relationship_type, "
    "created_at, updated_at) VALUES (:owner, 1, 2, 'works_with', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
)


class StubEmbeddings:
    """Deterministic embeddings so the tests never load a model."""

    async def generate_embedding(self, text: str) -> list[float]:
        return [0.1] * settings.EMBEDDING_DIMENSIONS


async def removed_shapes(db: Any) -> dict[str, dict[str, Any] | None]:
    return await table_shapes(db, REMOVED_TABLES)


def assert_removed_tables_absent(shapes: dict[str, Any]) -> None:
    assert shapes == dict.fromkeys(REMOVED_TABLES)


def is_postgres(db: Any) -> bool:
    return db._engine.dialect.name == "postgresql"


async def original_shapes(db: Any) -> dict[str, dict[str, Any] | None]:
    """The tables as the revisions before the drop leave them, built in a scratch database.

    Only the original revision chain runs here, so the new revision's downgrade cannot
    vouch for itself. PostgreSQL builds it in a scratch schema that a second engine
    makes its default schema (the inspector and Alembic resolve the default schema once
    per engine, so a ``search_path`` set on the shared engine would not be seen); SQLite
    builds it in a private in-memory database.
    """
    config = alembic_config()

    def build(connection: Any) -> dict[str, dict[str, Any] | None]:
        config.attributes["connection"] = connection
        command.upgrade(config, LAST_REVISION_WITH_ENTITIES)
        return read_shapes(connection, REMOVED_TABLES)

    if is_postgres(db):
        async with db._engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {SCRATCH_SCHEMA} CASCADE"))
            await connection.execute(text(f"CREATE SCHEMA {SCRATCH_SCHEMA}"))
            # Alembic finds its version table through the search path, and an empty one
            # here stops it from reading the real database's table behind the scratch one.
            await connection.execute(
                text(f"CREATE TABLE {SCRATCH_SCHEMA}.alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"),
            )
        scratch = create_async_engine(
            db._engine.url,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": f"{SCRATCH_SCHEMA},public"}},
        )
        try:
            async with scratch.begin() as connection:
                return await connection.run_sync(build)
        finally:
            await scratch.dispose()
            async with db._engine.begin() as connection:
                await connection.execute(text(f"DROP SCHEMA IF EXISTS {SCRATCH_SCHEMA} CASCADE"))

    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as connection:
            return await connection.run_sync(build)
    finally:
        await engine.dispose()


async def store_removed_rows_and_events(db: Any) -> dict[str, Any]:
    """Fill every removed table plus the activity log; returns the owner and the kept rows."""
    repos = create_repositories(db, StubEmbeddings(), None)
    user = await repos["user"].create_user(
        UserCreate(external_id="entities-removal", name="Entities", email="entities@example.test"),
    )
    owner = user.id
    project = await repos["project"].create_project(
        owner,
        ProjectCreate(name="Kept", description="Survives the migration", project_type=ProjectType.WORK),
    )
    first = await repos["memory"].create_memory(
        owner,
        MemoryCreate(title="Kept memory", content="c", context="x", keywords=["k"], tags=["t"], importance=7),
    )
    second = await repos["memory"].create_memory(
        owner,
        MemoryCreate(title="Linked memory", content="c", context="x", keywords=["k"], tags=["t"], importance=7),
    )
    await repos["memory"].create_links_batch(owner, first.id, [second.id])

    dialect = "postgresql" if is_postgres(db) else "sqlite"
    insert_entity = text(INSERT_ENTITY.format(empty=EMPTY_LIST[dialect]))
    event = text(
        "INSERT INTO activity_log (user_id, entity_type, entity_id, action, snapshot, actor, created_at) "
        "VALUES (:owner, :entity_type, 1, 'created', '{}', 'user', CURRENT_TIMESTAMP)",
    )
    statements = [
        INSERT_RELATIONSHIP,
        "INSERT INTO entity_project_association (entity_id, project_id) VALUES (1, :project)",
        "INSERT INTO memory_entity_association (memory_id, entity_id) VALUES (:memory, 1)",
    ]
    parameters = {"owner": str(owner), "project": project.id, "memory": first.id}
    async with db.system_session() as session:
        for entity_id, name in ((1, "Anthropic"), (2, "Partner")):
            await session.execute(insert_entity, {"id": entity_id, "owner": str(owner), "name": name})
        for statement in statements:
            await session.execute(text(statement), parameters)
        for entity_type in (*REMOVED_EVENT_TYPES, *KEPT_EVENT_TYPES):
            await session.execute(event, {"owner": str(owner), "entity_type": entity_type})
    return {"owner": str(owner), "memory": first.id, "linked": second.id}


class TestEntitiesRemoval:
    async def test_upgrade_drops_the_tables_and_their_activity_events(self, entities_db):
        config = alembic_config()
        await run_migration(entities_db, config, "downgrade", LAST_REVISION_WITH_ENTITIES)
        assert all(await removed_shapes(entities_db)), "downgrade must bring the tables back"
        kept = await store_removed_rows_and_events(entities_db)

        await run_migration(entities_db, config, "upgrade", "head")

        assert_removed_tables_absent(await removed_shapes(entities_db))
        assert await stored_event_types(entities_db) == sorted(KEPT_EVENT_TYPES)
        async with entities_db.system_session() as session:
            projects = await session.execute(
                text("SELECT name FROM projects WHERE user_id = :owner"), {"owner": kept["owner"]},
            )
            memories = await session.execute(
                text("SELECT id FROM memories WHERE user_id = :owner ORDER BY id"), {"owner": kept["owner"]},
            )
            links = await session.execute(text("SELECT source_id, target_id FROM memory_links"))
            assert [row[0] for row in projects] == ["Kept"]
            assert [row[0] for row in memories] == [kept["memory"], kept["linked"]]
            assert {frozenset(row) for row in links} == {frozenset({kept["memory"], kept["linked"]})}

    async def test_downgrade_recreates_the_schema_the_earlier_revisions_produce(self, entities_db):
        config = alembic_config()
        assert_removed_tables_absent(await removed_shapes(entities_db))
        original = await original_shapes(entities_db)
        try:
            await run_migration(entities_db, config, "downgrade", LAST_REVISION_WITH_ENTITIES)
            recreated = await removed_shapes(entities_db)
        finally:
            await run_migration(entities_db, config, "upgrade", "head")

        assert all(original.values())
        assert recreated == original
        assert_removed_tables_absent(await removed_shapes(entities_db))
