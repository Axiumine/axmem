"""Vector-storage DDL is built from a validated width and identifier, never formatted by hand.

A table name or the width in ``vector(n)`` / ``FLOAT[n]`` cannot be a bound parameter, so
it is written into the statement text. The builders refuse anything but a positive integer
and a plain identifier, and every repository builds its statement before it changes the
database, so a bad width fails with the old storage still in place.
"""
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text

from app.config.settings import settings
from app.repositories.postgres.memory_repository import PostgresMemoryRepository
from app.repositories.postgres.skill_repository import PostgresSkillRepository
from app.repositories.sqlite.memory_repository import SqliteMemoryRepository
from app.repositories.sqlite.skill_repository import SqliteSkillRepository
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from app.repositories.vector_ddl import (
    create_vec0_table_sql,
    resize_pgvector_column_sql,
    validated_dimensions,
    validated_identifier,
)

INJECTED_WIDTH = "384); DROP TABLE users; --"
BAD_WIDTHS = [INJECTED_WIDTH, "384", 0, -5, True, 3.5, None]
BAD_IDENTIFIERS = ["vec_memories; DROP TABLE users", "Vec", "1vec", "", "a b", "a-b", None, 7]


def bad_id(value):
    return repr(value)


# --- the builders -----------------------------------------------------------------------


def test_create_vec0_table_sql_spells_out_the_table():
    assert create_vec0_table_sql("vec_memories", "memory_id", 384) == (
        "CREATE VIRTUAL TABLE vec_memories USING vec0("
        "memory_id TEXT PRIMARY KEY, embedding FLOAT[384])"
    )


def test_create_vec0_table_sql_can_be_idempotent():
    assert create_vec0_table_sql("vec_skills", "skill_id", 8, if_not_exists=True).startswith(
        "CREATE VIRTUAL TABLE IF NOT EXISTS vec_skills USING vec0(",
    )


def test_resize_pgvector_column_sql_retypes_the_embedding_column():
    assert resize_pgvector_column_sql("memories", 1536) == (
        "ALTER TABLE memories ALTER COLUMN embedding TYPE vector(1536)"
    )


@pytest.mark.parametrize("width", BAD_WIDTHS, ids=bad_id)
def test_a_width_that_is_not_a_positive_integer_is_refused(width):
    with pytest.raises(ValueError, match="positive integer"):
        validated_dimensions(width)
    with pytest.raises(ValueError, match="positive integer"):
        create_vec0_table_sql("vec_memories", "memory_id", width)
    with pytest.raises(ValueError, match="positive integer"):
        resize_pgvector_column_sql("memories", width)


@pytest.mark.parametrize("name", BAD_IDENTIFIERS, ids=bad_id)
def test_a_name_that_is_not_a_plain_identifier_is_refused(name):
    with pytest.raises(ValueError, match="identifier"):
        validated_identifier(name)
    with pytest.raises(ValueError, match="identifier"):
        create_vec0_table_sql(name, "memory_id", 4)
    with pytest.raises(ValueError, match="identifier"):
        create_vec0_table_sql("vec_memories", name, 4)
    with pytest.raises(ValueError, match="identifier"):
        resize_pgvector_column_sql(name, 4)


# --- PostgreSQL repositories, against a recording session -------------------------------


class RecordingSession:
    def __init__(self):
        self.statements = []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(str(statement))


class RecordingAdapter:
    """Stands in for the PostgreSQL adapter: hands out one session that records its SQL."""

    def __init__(self):
        self.session = RecordingSession()

    @asynccontextmanager
    async def system_session(self):
        yield self.session


def postgres_memory_repo(adapter):
    return PostgresMemoryRepository(adapter, None)


def postgres_skill_repo(adapter):
    return PostgresSkillRepository(adapter, None)


POSTGRES_REPOS = [
    pytest.param(postgres_memory_repo, "memories", "UPDATE memories SET embedding = NULL", id="memories"),
    pytest.param(postgres_skill_repo, "skills", "UPDATE skills SET embedding = NULL", id="skills"),
]


@pytest.mark.parametrize("make_repo, table, clear_sql", POSTGRES_REPOS)
async def test_postgres_reset_resizes_the_embedding_column(
    monkeypatch, make_repo, table, clear_sql,
):
    # Arrange
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 768)
    adapter = RecordingAdapter()

    # Act
    await make_repo(adapter).reset_embedding_storage()

    # Assert: clear first (pgvector cannot cast across widths), then retype
    assert adapter.session.statements == [
        f"ALTER TABLE {table} ALTER COLUMN embedding DROP NOT NULL",
        clear_sql,
        f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector(768)",
    ]


@pytest.mark.parametrize("make_repo, table, clear_sql", POSTGRES_REPOS)
@pytest.mark.parametrize("width", BAD_WIDTHS, ids=bad_id)
async def test_postgres_reset_refuses_a_bad_width_before_touching_anything(
    monkeypatch, make_repo, table, clear_sql, width,
):
    # Arrange
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", width)
    adapter = RecordingAdapter()

    # Act / Assert: nothing ran, so no embedding was cleared for a resize that cannot happen
    with pytest.raises(ValueError, match="positive integer"):
        await make_repo(adapter).reset_embedding_storage()
    assert adapter.session.statements == []


# --- SQLite repositories, on a real sqlite-vec database ---------------------------------

SQLITE_REPOS = [
    pytest.param(SqliteMemoryRepository, "vec_memories", id="memories"),
    pytest.param(SqliteSkillRepository, "vec_skills", id="skills"),
]


@pytest.fixture
async def sqlite_db(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE", "SQLite")
    monkeypatch.setattr(settings, "SQLITE_MEMORY", True)
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 4)
    adapter = SqliteDatabaseAdapter()
    await adapter.init_db()
    try:
        yield adapter
    finally:
        await adapter.dispose()


async def vec_table_sql(adapter, table):
    async with adapter.system_session() as session:
        result = await session.execute(
            text("SELECT sql FROM sqlite_master WHERE name = :name"), {"name": table},
        )
        return result.scalar_one()


@pytest.mark.parametrize("repo_class, table", SQLITE_REPOS)
async def test_sqlite_reset_recreates_the_vec_table_at_the_new_width(
    monkeypatch, sqlite_db, repo_class, table,
):
    # Arrange
    assert "FLOAT[4]" in await vec_table_sql(sqlite_db, table)
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 6)

    # Act
    await repo_class(sqlite_db, None).reset_embedding_storage()

    # Assert
    assert "FLOAT[6]" in await vec_table_sql(sqlite_db, table)


@pytest.mark.parametrize("repo_class, table", SQLITE_REPOS)
async def test_sqlite_reset_refuses_a_bad_width_and_keeps_the_old_table(
    monkeypatch, sqlite_db, repo_class, table,
):
    # Arrange
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", INJECTED_WIDTH)

    # Act / Assert: the table was not dropped on the way to the failure
    with pytest.raises(ValueError, match="positive integer"):
        await repo_class(sqlite_db, None).reset_embedding_storage()
    assert "FLOAT[4]" in await vec_table_sql(sqlite_db, table)


async def test_sqlite_start_up_refuses_a_bad_width(monkeypatch):
    # Arrange
    monkeypatch.setattr(settings, "DATABASE", "SQLite")
    monkeypatch.setattr(settings, "SQLITE_MEMORY", True)
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", INJECTED_WIDTH)
    adapter = SqliteDatabaseAdapter()

    # Act / Assert
    try:
        with pytest.raises(ValueError, match="positive integer"):
            await adapter.init_db()
    finally:
        await adapter.dispose()
