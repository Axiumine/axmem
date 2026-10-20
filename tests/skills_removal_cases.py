"""Contracts of the revision that dropped the skills tables, run against SQLite and PostgreSQL."""

from typing import Any

from sqlalchemy import text

from app.bootstrap import create_repositories
from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.models.project_models import ProjectCreate, ProjectType
from app.models.user_models import UserCreate
from app.repositories.vector_ddl import create_vec0_table_sql
from tests.docs_artifacts_removal_cases import INSERT_CODE_ARTIFACT, INSERT_DOCUMENT
from tests.migration_helpers import (
    alembic_config,
    run_migration,
    stored_event_types,
    table_shapes,
    upgraded_to,
)

LAST_REVISION_WITH_SKILLS = "20261019_drop_planning"
SKILLS_DROP_REVISION = "20261019_drop_skills"
REVISION_BEFORE_SKILLS = "20260315_files"
SKILL_TABLES = (
    "skills",
    "memory_skill_association",
    "skill_file_association",
    "skill_code_artifact_association",
    "skill_document_association",
)
KEPT_EVENT_TYPES = ("memory", "project")
VEC_SKILLS_QUERY = "SELECT name FROM sqlite_master WHERE name = 'vec_skills'"


class StubEmbeddings:
    """Deterministic embeddings so the tests never load a model."""

    async def generate_embedding(self, text: str) -> list[float]:
        return [0.1] * settings.EMBEDDING_DIMENSIONS


async def skill_shapes(db: Any) -> dict[str, dict[str, Any] | None]:
    return await table_shapes(db, SKILL_TABLES)


def assert_skill_tables_absent(shapes: dict[str, Any]) -> None:
    assert shapes == dict.fromkeys(SKILL_TABLES)


def is_postgres(db: Any) -> bool:
    return db._engine.dialect.name == "postgresql"


async def store_skill_rows_and_events(db: Any) -> dict[str, Any]:
    """Fill every skill table plus the activity log; returns the owner and the kept rows."""
    repos = create_repositories(db, StubEmbeddings(), None)
    user = await repos["user"].create_user(
        UserCreate(external_id="skills-removal", name="Skills", email="skills@example.test"),
    )
    owner = user.id
    project = await repos["project"].create_project(
        owner,
        ProjectCreate(name="Kept", description="Survives the migration", project_type=ProjectType.WORK),
    )
    memory = await repos["memory"].create_memory(
        owner,
        MemoryCreate(title="Kept memory", content="c", context="x", keywords=["k"], tags=["t"], importance=7),
    )

    # PostgreSQL stores the skill embedding in the row and has array tags; SQLite keeps the
    # embedding in vec_skills and stores the tags as JSON.
    insert_skill = (
        "INSERT INTO skills (id, user_id, project_id, name, description, content, tags, embedding) "
        "VALUES (1, :owner, :project, 'skill', 'd', 'c', '{}', CAST(:embedding AS vector))"
        if is_postgres(db) else
        "INSERT INTO skills (id, user_id, project_id, name, description, content, tags) "
        "VALUES (1, :owner, :project, 'skill', 'd', 'c', '[]')"
    )
    parameters = {
        "owner": str(owner),
        "project": project.id,
        "memory": memory.id,
        "embedding": str([0.1] * settings.EMBEDDING_DIMENSIONS),
    }
    # The files, documents and code artifacts tables are not part of the application any more:
    # their revisions bring them back.
    insert_file = (
        "INSERT INTO files (id, user_id, filename, description, data, mime_type, size_bytes, tags) "
        "VALUES (1, :owner, 'a.txt', 'd', 'hello', 'text/plain', 5, '{}')"
        if is_postgres(db) else
        "INSERT INTO files (id, user_id, filename, description, data, mime_type, size_bytes, tags) "
        "VALUES (1, :owner, 'a.txt', 'd', 'hello', 'text/plain', 5, '[]')"
    )
    dialect = "postgresql" if is_postgres(db) else "sqlite"
    statements = [
        INSERT_DOCUMENT[dialect],
        INSERT_CODE_ARTIFACT[dialect],
        insert_file,
        insert_skill,
        "INSERT INTO memory_skill_association (memory_id, skill_id) VALUES (:memory, 1)",
        "INSERT INTO skill_file_association (skill_id, file_id) VALUES (1, 1)",
        "INSERT INTO skill_code_artifact_association (skill_id, code_artifact_id) VALUES (1, 1)",
        "INSERT INTO skill_document_association (skill_id, document_id) VALUES (1, 1)",
    ]
    event = text(
        "INSERT INTO activity_log (user_id, entity_type, entity_id, action, snapshot, actor, created_at) "
        "VALUES (:owner, :entity_type, 1, 'created', '{}', 'user', CURRENT_TIMESTAMP)",
    )
    async with db.system_session() as session:
        for statement in statements:
            await session.execute(text(statement), parameters)
        for entity_type in ("skill", *KEPT_EVENT_TYPES):
            await session.execute(event, {"owner": str(owner), "entity_type": entity_type})
    return {"owner": str(owner), "memory": memory.id}


async def create_vec_skills(db: Any) -> None:
    """What the previous application version did at start-up on SQLite."""
    async with db.system_session() as session:
        await session.execute(text(create_vec0_table_sql(
            "vec_skills", "skill_id", settings.EMBEDDING_DIMENSIONS, if_not_exists=True,
        )))
        await session.execute(
            text("INSERT INTO vec_skills (skill_id, embedding) VALUES ('1', :embedding)"),
            {"embedding": str([0.1] * settings.EMBEDDING_DIMENSIONS)},
        )


async def vec_skills_tables(db: Any) -> list[str]:
    async with db.system_session() as session:
        return [row[0] for row in await session.execute(text(VEC_SKILLS_QUERY))]


class TestSkillsRemoval:
    async def test_upgrade_drops_the_skill_tables_and_their_activity_events(self, skills_db):
        config = alembic_config()
        await run_migration(skills_db, config, "downgrade", LAST_REVISION_WITH_SKILLS)
        assert all(await skill_shapes(skills_db)), "downgrade must bring the tables back"
        kept = await store_skill_rows_and_events(skills_db)
        if not is_postgres(skills_db):
            await create_vec_skills(skills_db)
            assert await vec_skills_tables(skills_db) == ["vec_skills"]

        async with upgraded_to(skills_db, config, SKILLS_DROP_REVISION):
            assert_skill_tables_absent(await skill_shapes(skills_db))
            assert await stored_event_types(skills_db) == sorted(KEPT_EVENT_TYPES)
            if not is_postgres(skills_db):
                assert await vec_skills_tables(skills_db) == []
            async with skills_db.system_session() as session:
                projects = await session.execute(
                    text("SELECT name FROM projects WHERE user_id = :owner"), {"owner": kept["owner"]},
                )
                memories = await session.execute(text("SELECT id FROM memories WHERE id = :id"), {"id": kept["memory"]})
                assert [row[0] for row in projects] == ["Kept"]
                assert [row[0] for row in memories] == [kept["memory"]]

    async def test_downgrade_recreates_the_schema_the_earlier_revisions_produce(self, skills_db):
        config = alembic_config()
        assert_skill_tables_absent(await skill_shapes(skills_db))
        try:
            await run_migration(skills_db, config, "downgrade", LAST_REVISION_WITH_SKILLS)
            recreated = await skill_shapes(skills_db)
            # Rebuild the same revision through the original chain (skills, provenance, unique name).
            await run_migration(skills_db, config, "downgrade", REVISION_BEFORE_SKILLS)
            assert_skill_tables_absent(await skill_shapes(skills_db))
            await run_migration(skills_db, config, "upgrade", LAST_REVISION_WITH_SKILLS)
            original = await skill_shapes(skills_db)
        finally:
            await run_migration(skills_db, config, "upgrade", "head")

        assert all(recreated.values())
        assert recreated == original
        assert_skill_tables_absent(await skill_shapes(skills_db))
