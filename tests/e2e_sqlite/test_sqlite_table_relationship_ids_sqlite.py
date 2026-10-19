"""Computed id lists on SQLite rows only read relationships that were actually loaded.

``EntitiesTable.project_ids`` and ``TasksTable.dependency_ids`` feed the Pydantic models. On an
async session an unloaded relationship cannot be lazy-loaded, so the properties must report
an empty list instead of touching it, and the real ids once the relationship is loaded.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.entity_models import EntityCreate, EntityType
from app.models.plan_models import PlanCreate, TaskCreate
from app.models.project_models import ProjectCreate, ProjectType
from app.repositories.sqlite.entity_repository import SqliteEntityRepository
from app.repositories.sqlite.plan_repository import SqlitePlanRepository
from app.repositories.sqlite.project_repository import SqliteProjectRepository
from app.repositories.sqlite.sqlite_tables import EntitiesTable, TasksTable
from app.repositories.sqlite.task_repository import SqliteTaskRepository
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


@pytest.fixture
async def dependent_task_ids(adapter, user_id, project_id):
    plan = await SqlitePlanRepository(adapter).create_plan(
        user_id, PlanCreate(title="Ship v2", project_id=project_id),
    )
    tasks = SqliteTaskRepository(adapter)
    prerequisite = await tasks.create_task(user_id, TaskCreate(title="Design", plan_id=plan.id))
    dependent = await tasks.create_task(user_id, TaskCreate(title="Build", plan_id=plan.id))
    await tasks.add_dependency(user_id, dependent.id, prerequisite.id)
    return dependent.id, prerequisite.id


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


async def test_task_row_reports_no_dependency_ids_while_dependencies_are_not_loaded(
    adapter, user_id, dependent_task_ids,
):
    dependent_id, _ = dependent_task_ids
    async with adapter.session(user_id) as session:
        row = (await session.execute(
            select(TasksTable).where(TasksTable.id == dependent_id),
        )).scalar_one()

        assert row.dependency_ids == []


async def test_task_row_reports_its_dependency_ids_once_dependencies_are_loaded(
    adapter, user_id, dependent_task_ids,
):
    dependent_id, prerequisite_id = dependent_task_ids
    async with adapter.session(user_id) as session:
        row = (await session.execute(
            select(TasksTable)
            .options(selectinload(TasksTable.depends_on))
            .where(TasksTable.id == dependent_id),
        )).scalar_one()

        assert row.dependency_ids == [prerequisite_id]
