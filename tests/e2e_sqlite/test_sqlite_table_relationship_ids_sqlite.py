"""Computed id lists on SQLite rows only read relationships that were actually loaded.

``EntitiesTable.project_ids`` feeds the Pydantic models. On an
async session an unloaded relationship cannot be lazy-loaded, so the properties must report
an empty list instead of touching it, and the real ids once the relationship is loaded.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.entity_models import EntityCreate, EntityType
from app.models.project_models import ProjectCreate, ProjectType
from app.repositories.sqlite.entity_repository import SqliteEntityRepository
from app.repositories.sqlite.project_repository import SqliteProjectRepository
from app.repositories.sqlite.sqlite_tables import EntitiesTable
from tests.e2e_sqlite.sqlite_repository_helpers import create_user, open_adapter


@pytest.fixture
async def adapter(monkeypatch):
    async with open_adapter(monkeypatch) as opened:
        yield opened


@pytest.fixture
async def user_id(adapter):
    return await create_user(adapter)


@pytest.fixture
async def project_id(adapter, user_id):
    project = await SqliteProjectRepository(adapter).create_project(
        user_id,
        ProjectCreate(name="Atlas", description="Relationship target", project_type=ProjectType.WORK),
    )
    return project.id


@pytest.fixture
async def entity_id(adapter, user_id, project_id):
    entities = SqliteEntityRepository(adapter)
    entity = await entities.create_entity(
        user_id, EntityCreate(name="Acme", entity_type=EntityType.ORGANIZATION),
    )
    await entities.link_entity_to_project(user_id, entity.id, project_id)
    return entity.id


async def test_entity_row_reports_no_project_ids_while_projects_are_not_loaded(
    adapter, user_id, entity_id,
):
    async with adapter.session(user_id) as session:
        row = (await session.execute(
            select(EntitiesTable).where(EntitiesTable.id == entity_id),
        )).scalar_one()

        assert row.project_ids == []


async def test_entity_row_reports_its_project_ids_once_projects_are_loaded(
    adapter, user_id, entity_id, project_id,
):
    async with adapter.session(user_id) as session:
        row = (await session.execute(
            select(EntitiesTable)
            .options(selectinload(EntitiesTable.projects))
            .where(EntitiesTable.id == entity_id),
        )).scalar_one()

        assert row.project_ids == [project_id]
