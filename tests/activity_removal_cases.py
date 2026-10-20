"""Contracts of the revision that dropped the activity log and the memory access counters, on SQLite and PostgreSQL."""

from typing import Any

from sqlalchemy import text

from app.bootstrap import create_repositories
from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.models.user_models import UserCreate
from tests.migration_helpers import (
    alembic_config,
    create_project_row,
    original_chain_shapes,
    run_migration,
    table_shapes,
)

LAST_REVISION_WITH_ACTIVITY = "20261019_drop_entities"
SCRATCH_SCHEMA = "scratch_activity"
REMOVED_TABLE = "activity_log"
REMOVED_MEMORY_COLUMNS = ("access_count", "last_accessed_at")
REMOVED_MEMORY_INDEX = "ix_memories_last_accessed_at"
EVENT_TYPES = ("memory", "project", "link")


class StubEmbeddings:
    """Deterministic embeddings so the tests never load a model."""

    async def generate_embedding(self, text: str) -> list[float]:
        return [0.1] * settings.EMBEDDING_DIMENSIONS


def is_postgres(db: Any) -> bool:
    return db._engine.dialect.name == "postgresql"


def removed_parts(shapes: dict[str, Any]) -> dict[str, Any]:
    """The activity log and the part of ``memories`` that the revision touches."""
    memories = shapes["memories"]
    return {
        REMOVED_TABLE: shapes[REMOVED_TABLE],
        "memory_columns": {name: memories["columns"].get(name) for name in REMOVED_MEMORY_COLUMNS},
        "memory_indexes": [index for index in memories["indexes"] if index[0] == REMOVED_MEMORY_INDEX],
    }


async def current_parts(db: Any) -> dict[str, Any]:
    return removed_parts(await table_shapes(db, (REMOVED_TABLE, "memories")))


def assert_removed(parts: dict[str, Any]) -> None:
    assert parts == {
        REMOVED_TABLE: None,
        "memory_columns": dict.fromkeys(REMOVED_MEMORY_COLUMNS),
        "memory_indexes": [],
    }


async def store_events_and_counters(db: Any) -> dict[str, Any]:
    """Fill the activity log and the access counters; returns the owner and the kept rows."""
    repos = create_repositories(db, StubEmbeddings(), None)
    user = await repos["user"].create_user(
        UserCreate(external_id="activity-removal", name="Activity", email="activity@example.test"),
    )
    owner = user.id
    await create_project_row(db, owner)
    first = await repos["memory"].create_memory(
        owner,
        MemoryCreate(title="Kept memory", content="c", context="x", keywords=["k"], tags=["t"], importance=7),
    )
    second = await repos["memory"].create_memory(
        owner,
        MemoryCreate(title="Linked memory", content="c", context="x", keywords=["k"], tags=["t"], importance=7),
    )
    await repos["memory"].create_links_batch(owner, first.id, [second.id])

    event = text(
        "INSERT INTO activity_log (user_id, entity_type, entity_id, action, snapshot, actor, created_at) "
        "VALUES (:owner, :entity_type, 1, 'created', '{}', 'user', CURRENT_TIMESTAMP)",
    )
    async with db.system_session() as session:
        for entity_type in EVENT_TYPES:
            await session.execute(event, {"owner": str(owner), "entity_type": entity_type})
        await session.execute(
            text("UPDATE memories SET access_count = 3, last_accessed_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"id": first.id},
        )
    return {"owner": str(owner), "project": "Kept", "memory": first.id, "linked": second.id}


class TestActivityRemoval:
    async def test_upgrade_drops_the_log_and_the_counters_and_keeps_the_rows(self, activity_db):
        config = alembic_config()
        await run_migration(activity_db, config, "downgrade", LAST_REVISION_WITH_ACTIVITY)
        restored = await current_parts(activity_db)
        assert restored[REMOVED_TABLE] and all(restored["memory_columns"].values()), "downgrade must bring them back"
        kept = await store_events_and_counters(activity_db)

        await run_migration(activity_db, config, "upgrade", "head")

        assert_removed(await current_parts(activity_db))
        async with activity_db.system_session() as session:
            projects = await session.execute(
                text("SELECT name FROM projects WHERE user_id = :owner"), {"owner": kept["owner"]},
            )
            memories = await session.execute(
                text("SELECT id FROM memories WHERE user_id = :owner ORDER BY id"), {"owner": kept["owner"]},
            )
            links = await session.execute(text("SELECT source_id, target_id FROM memory_links"))
            assert [row[0] for row in projects] == [kept["project"]]
            assert [row[0] for row in memories] == [kept["memory"], kept["linked"]]
            assert {frozenset(row) for row in links} == {frozenset({kept["memory"], kept["linked"]})}

    async def test_downgrade_restores_the_counters_as_never_accessed(self, activity_db):
        config = alembic_config()
        assert_removed(await current_parts(activity_db))
        repos = create_repositories(activity_db, StubEmbeddings(), None)
        user = await repos["user"].create_user(
            UserCreate(external_id="activity-restore", name="Restore", email="restore@example.test"),
        )
        await repos["memory"].create_memory(
            user.id,
            MemoryCreate(title="Old memory", content="c", context="x", keywords=["k"], tags=["t"], importance=5),
        )
        try:
            await run_migration(activity_db, config, "downgrade", LAST_REVISION_WITH_ACTIVITY)
            async with activity_db.system_session() as session:
                counters = await session.execute(text("SELECT access_count, last_accessed_at FROM memories"))
                events = await session.execute(text("SELECT count(*) FROM activity_log"))
                assert [tuple(row) for row in counters] == [(0, None)]
                assert events.scalar_one() == 0
        finally:
            await run_migration(activity_db, config, "upgrade", "head")

        assert_removed(await current_parts(activity_db))

    async def test_downgrade_recreates_the_schema_the_earlier_revisions_produce(self, activity_db):
        config = alembic_config()
        assert_removed(await current_parts(activity_db))
        original = removed_parts(
            await original_chain_shapes(
                activity_db, LAST_REVISION_WITH_ACTIVITY, (REMOVED_TABLE, "memories"), SCRATCH_SCHEMA,
            ),
        )
        try:
            await run_migration(activity_db, config, "downgrade", LAST_REVISION_WITH_ACTIVITY)
            recreated = await current_parts(activity_db)
        finally:
            await run_migration(activity_db, config, "upgrade", "head")

        assert original[REMOVED_TABLE] and all(original["memory_columns"].values()) and original["memory_indexes"]
        assert recreated == original
        assert_removed(await current_parts(activity_db))
