"""Contracts of the revision that dropped documents and code artifacts, run on SQLite and PostgreSQL."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.bootstrap import create_repositories
from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.models.user_models import UserCreate
from tests.migration_helpers import (
    alembic_config,
    create_project_row,
    read_shapes,
    run_migration,
    stored_event_types,
    table_shapes,
    upgraded_to,
)

LAST_REVISION_WITH_DOCS = "20261019_drop_files"
DOCS_DROP_REVISION = "20261019_drop_docs_artifacts"
REMOVED_TABLES = (
    "documents",
    "code_artifacts",
    "memory_document_association",
    "memory_code_artifact_association",
)
KEPT_EVENT_TYPES = ("memory", "project")
SCRATCH_SCHEMA = "scratch_docs_artifacts"

# The tables are not part of the application any more: the revision's downgrade brings them
# back, so rows go in with SQL (PostgreSQL has array tags, SQLite stores them as JSON).
INSERT_DOCUMENT = {
    "postgresql": (
        "INSERT INTO documents (id, user_id, project_id, title, description, content, size_bytes, tags, "
        "created_at, updated_at) VALUES (1, :owner, :project, 't', 'd', 'body', 4, '{}', "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ),
    "sqlite": (
        "INSERT INTO documents (id, user_id, project_id, title, description, content, size_bytes, tags, "
        "created_at, updated_at) VALUES (1, :owner, :project, 't', 'd', 'body', 4, '[]', "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ),
}
INSERT_CODE_ARTIFACT = {
    "postgresql": (
        "INSERT INTO code_artifacts (id, user_id, project_id, title, description, code, language, tags, "
        "created_at, updated_at) VALUES (1, :owner, :project, 't', 'd', 'print(1)', 'python', '{}', "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ),
    "sqlite": (
        "INSERT INTO code_artifacts (id, user_id, project_id, title, description, code, language, tags, "
        "created_at, updated_at) VALUES (1, :owner, :project, 't', 'd', 'print(1)', 'python', '[]', "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ),
}


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
        command.upgrade(config, LAST_REVISION_WITH_DOCS)
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
        UserCreate(external_id="docs-artifacts-removal", name="Docs", email="docs@example.test"),
    )
    owner = user.id
    project_id = await create_project_row(db, owner)
    memory = await repos["memory"].create_memory(
        owner,
        MemoryCreate(title="Kept memory", content="c", context="x", keywords=["k"], tags=["t"], importance=7),
    )

    dialect = "postgresql" if is_postgres(db) else "sqlite"
    statements = [
        INSERT_DOCUMENT[dialect],
        INSERT_CODE_ARTIFACT[dialect],
        "INSERT INTO memory_document_association (memory_id, document_id) VALUES (:memory, 1)",
        "INSERT INTO memory_code_artifact_association (memory_id, code_artifact_id) VALUES (:memory, 1)",
    ]
    parameters = {"owner": str(owner), "project": project_id, "memory": memory.id}
    event = text(
        "INSERT INTO activity_log (user_id, entity_type, entity_id, action, snapshot, actor, created_at) "
        "VALUES (:owner, :entity_type, 1, 'created', '{}', 'user', CURRENT_TIMESTAMP)",
    )
    async with db.system_session() as session:
        for statement in statements:
            await session.execute(text(statement), parameters)
        for entity_type in ("document", "code_artifact", *KEPT_EVENT_TYPES):
            await session.execute(event, {"owner": str(owner), "entity_type": entity_type})
    return {"owner": str(owner), "memory": memory.id}


class TestDocumentsAndCodeArtifactsRemoval:
    async def test_upgrade_drops_the_tables_and_their_activity_events(self, docs_db):
        config = alembic_config()
        await run_migration(docs_db, config, "downgrade", LAST_REVISION_WITH_DOCS)
        assert all(await removed_shapes(docs_db)), "downgrade must bring the tables back"
        kept = await store_removed_rows_and_events(docs_db)

        async with upgraded_to(docs_db, config, DOCS_DROP_REVISION):
            assert_removed_tables_absent(await removed_shapes(docs_db))
            assert await stored_event_types(docs_db) == sorted(KEPT_EVENT_TYPES)
            async with docs_db.system_session() as session:
                projects = await session.execute(
                    text("SELECT name FROM projects WHERE user_id = :owner"), {"owner": kept["owner"]},
                )
                memories = await session.execute(text("SELECT id FROM memories WHERE id = :id"), {"id": kept["memory"]})
                assert [row[0] for row in projects] == ["Kept"]
                assert [row[0] for row in memories] == [kept["memory"]]

    async def test_downgrade_recreates_the_schema_the_earlier_revisions_produce(self, docs_db):
        config = alembic_config()
        assert_removed_tables_absent(await removed_shapes(docs_db))
        original = await original_shapes(docs_db)
        try:
            await run_migration(docs_db, config, "downgrade", LAST_REVISION_WITH_DOCS)
            recreated = await removed_shapes(docs_db)
        finally:
            await run_migration(docs_db, config, "upgrade", "head")

        assert all(original.values())
        assert recreated == original
        assert_removed_tables_absent(await removed_shapes(docs_db))
