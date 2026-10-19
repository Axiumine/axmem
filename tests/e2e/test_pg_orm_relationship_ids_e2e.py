"""PostgreSQL ORM rows: related ids are reported only for relationships that were loaded.

``EntitiesTable.project_ids`` and ``TasksTable.dependency_ids`` feed the pydantic models.
In an async session an unloaded relationship cannot be lazy-loaded, so the property must
answer with an empty list for it and with the real ids once the relationship is loaded.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.entity_models import EntityCreate, EntityType
from app.models.plan_models import PlanCreate, TaskCreate
from app.models.project_models import ProjectCreate, ProjectType
from app.models.user_models import UserCreate
from app.repositories.postgres.entity_repository import PostgresEntityRepository
from app.repositories.postgres.plan_repository import PostgresPlanRepository
from app.repositories.postgres.postgres_tables import EntitiesTable, TasksTable
from app.repositories.postgres.project_repository import PostgresProjectRepository
from app.repositories.postgres.task_repository import PostgresTaskRepository
from app.repositories.postgres.user_repository import PostgresUserRepository

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def owner(db_adapter):
    user = await PostgresUserRepository(db_adapter).create_user(
        UserCreate(external_id="orm-ids-e2e", name="u", email="u@example.com"),
    )
    return user.id


@pytest_asyncio.fixture(loop_scope="session")
async def project_id(db_adapter, owner):
    project = await PostgresProjectRepository(db_adapter).create_project(
        owner, ProjectCreate(name="p", description="d", project_type=ProjectType.DEVELOPMENT),
    )
    return project.id


async def _ids_without_and_with_relationship(
    db_adapter, owner, model, row_id, relationship, ids_attribute,
):
    """Read ``ids_attribute`` of one row loaded plainly, then of the same row loaded eagerly."""
    async with db_adapter.session(owner) as session:
        plain = (await session.execute(select(model).where(model.id == row_id))).scalar_one()
        without = getattr(plain, ids_attribute)
        session.expunge(plain)
        eager = (
            await session.execute(
                select(model).options(selectinload(relationship)).where(model.id == row_id),
            )
        ).scalar_one()
        return without, getattr(eager, ids_attribute)


async def test_entity_row_reports_project_ids_only_after_projects_are_loaded(
    db_adapter, owner, project_id,
):
    entities = PostgresEntityRepository(db_adapter)
    entity = await entities.create_entity(
        owner, EntityCreate(name="Acme", entity_type=EntityType.ORGANIZATION),
    )
    assert await entities.link_entity_to_project(owner, entity.id, project_id) is True

    without, with_relationship = await _ids_without_and_with_relationship(
        db_adapter, owner, EntitiesTable, entity.id, EntitiesTable.projects, "project_ids",
    )

    assert without == []
    assert with_relationship == [project_id]


async def test_task_row_reports_dependency_ids_only_after_dependencies_are_loaded(
    db_adapter, owner, project_id,
):
    plan = await PostgresPlanRepository(db_adapter).create_plan(
        owner, PlanCreate(title="plan", project_id=project_id),
    )
    tasks = PostgresTaskRepository(db_adapter)
    prerequisite = await tasks.create_task(owner, TaskCreate(title="first", plan_id=plan.id))
    dependent = await tasks.create_task(owner, TaskCreate(title="second", plan_id=plan.id))
    await tasks.add_dependency(owner, dependent.id, prerequisite.id)

    without, with_relationship = await _ids_without_and_with_relationship(
        db_adapter, owner, TasksTable, dependent.id, TasksTable.depends_on, "dependency_ids",
    )

    assert without == []
    assert with_relationship == [prerequisite.id]
