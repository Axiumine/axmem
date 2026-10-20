"""Contracts of the revision that dropped the files tables, run against SQLite and PostgreSQL."""

from typing import Any

from sqlalchemy import text

from app.bootstrap import create_repositories
from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.models.user_models import UserCreate
from tests.migration_helpers import (
    alembic_config,
    create_project_row,
    run_migration,
    stored_event_types,
    table_shapes,
    upgraded_to,
)

LAST_REVISION_WITH_FILES = "20261019_drop_skills"
FILES_DROP_REVISION = "20261019_drop_files"
REVISION_BEFORE_FILES = "20260312_planning"
FILE_TABLES = (
    "files",
    "memory_file_association",
    "entity_file_association",
)
KEPT_EVENT_TYPES = ("memory", "project")


class StubEmbeddings:
    """Deterministic embeddings so the tests never load a model."""

    async def generate_embedding(self, text: str) -> list[float]:
        return [0.1] * settings.EMBEDDING_DIMENSIONS


async def file_shapes(db: Any) -> dict[str, dict[str, Any] | None]:
    return await table_shapes(db, FILE_TABLES)


def assert_file_tables_absent(shapes: dict[str, Any]) -> None:
    assert shapes == dict.fromkeys(FILE_TABLES)


def is_postgres(db: Any) -> bool:
    return db._engine.dialect.name == "postgresql"


async def store_file_rows_and_events(db: Any) -> dict[str, Any]:
    """Fill every file table plus the activity log; returns the owner and the kept rows."""
    repos = create_repositories(db, StubEmbeddings(), None)
    user = await repos["user"].create_user(
        UserCreate(external_id="files-removal", name="Files", email="files@example.test"),
    )
    owner = user.id
    project_id = await create_project_row(db, owner)
    memory = await repos["memory"].create_memory(
        owner,
        MemoryCreate(title="Kept memory", content="c", context="x", keywords=["k"], tags=["t"], importance=7),
    )

    # The files table is not part of the application any more: the revision's downgrade
    # brought it back, so the rows go in with SQL. PostgreSQL has array tags.
    insert_file = (
        "INSERT INTO files (id, user_id, project_id, filename, description, data, mime_type, size_bytes, tags) "
        "VALUES (1, :owner, :project, 'a.txt', 'd', 'hello', 'text/plain', 5, '{}')"
        if is_postgres(db) else
        "INSERT INTO files (id, user_id, project_id, filename, description, data, mime_type, size_bytes, tags) "
        "VALUES (1, :owner, :project, 'a.txt', 'd', 'hello', 'text/plain', 5, '[]')"
    )
    # Entities are not part of the application any more either: the same downgrade brought
    # the entities table back, and the file link needs a row to point at.
    insert_entity = (
        "INSERT INTO entities (id, user_id, name, entity_type, tags, aka, created_at, updated_at) "
        "VALUES (1, :owner, 'Linked', 'Organization', '{}', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        if is_postgres(db) else
        "INSERT INTO entities (id, user_id, name, entity_type, tags, aka, created_at, updated_at) "
        "VALUES (1, :owner, 'Linked', 'Organization', '[]', '[]', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    statements = [
        insert_entity,
        insert_file,
        "INSERT INTO memory_file_association (memory_id, file_id) VALUES (:memory, 1)",
        "INSERT INTO entity_file_association (entity_id, file_id) VALUES (1, 1)",
    ]
    parameters = {"owner": str(owner), "project": project_id, "memory": memory.id}
    event = text(
        "INSERT INTO activity_log (user_id, entity_type, entity_id, action, snapshot, actor, created_at) "
        "VALUES (:owner, :entity_type, 1, 'created', '{}', 'user', CURRENT_TIMESTAMP)",
    )
    async with db.system_session() as session:
        for statement in statements:
            await session.execute(text(statement), parameters)
        for entity_type in ("file", *KEPT_EVENT_TYPES):
            await session.execute(event, {"owner": str(owner), "entity_type": entity_type})
    return {"owner": str(owner), "memory": memory.id}


class TestFilesRemoval:
    async def test_upgrade_drops_the_file_tables_and_their_activity_events(self, files_db):
        config = alembic_config()
        await run_migration(files_db, config, "downgrade", LAST_REVISION_WITH_FILES)
        assert all(await file_shapes(files_db)), "downgrade must bring the tables back"
        kept = await store_file_rows_and_events(files_db)

        async with upgraded_to(files_db, config, FILES_DROP_REVISION):
            assert_file_tables_absent(await file_shapes(files_db))
            assert await stored_event_types(files_db) == sorted(KEPT_EVENT_TYPES)
            async with files_db.system_session() as session:
                projects = await session.execute(
                    text("SELECT name FROM projects WHERE user_id = :owner"), {"owner": kept["owner"]},
                )
                memories = await session.execute(text("SELECT id FROM memories WHERE id = :id"), {"id": kept["memory"]})
                assert [row[0] for row in projects] == ["Kept"]
                assert [row[0] for row in memories] == [kept["memory"]]

    async def test_downgrade_recreates_the_schema_the_earlier_revisions_produce(self, files_db):
        config = alembic_config()
        assert_file_tables_absent(await file_shapes(files_db))
        try:
            await run_migration(files_db, config, "downgrade", LAST_REVISION_WITH_FILES)
            recreated = await file_shapes(files_db)
            # Rebuild the same revision through the original chain (files, provenance).
            await run_migration(files_db, config, "downgrade", REVISION_BEFORE_FILES)
            assert_file_tables_absent(await file_shapes(files_db))
            await run_migration(files_db, config, "upgrade", LAST_REVISION_WITH_FILES)
            original = await file_shapes(files_db)
        finally:
            await run_migration(files_db, config, "upgrade", "head")

        assert all(recreated.values())
        assert recreated == original
        assert_file_tables_absent(await file_shapes(files_db))
