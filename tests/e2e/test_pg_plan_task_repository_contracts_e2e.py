"""PostgreSQL plan and task repositories: behaviour the services do not reach.

Empty patches, ids that vanished between the service's check and the repository's write,
list filters and the explanation of a refused state transition when the cause is gone by the
time it is looked up. Everything runs against the real repositories and read back through
other repository calls.
"""
from dataclasses import dataclass
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from app.exceptions import ConflictError, NotFoundError
from app.models.plan_models import (
    CriterionCreate,
    CriterionUpdate,
    PlanCreate,
    PlanUpdate,
    TaskCreate,
    TaskPriority,
    TaskState,
    TaskUpdate,
)
from app.models.project_models import ProjectCreate, ProjectType
from app.models.user_models import UserCreate
from app.repositories.postgres.plan_repository import PostgresPlanRepository
from app.repositories.postgres.project_repository import PostgresProjectRepository
from app.repositories.postgres.task_repository import PostgresTaskRepository
from app.repositories.postgres.user_repository import PostgresUserRepository
from tests.e2e.pg_session_interference_helpers import InterferingAdapter

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

UNKNOWN_ID = 987654


@dataclass
class Workspace:
    user_id: UUID
    plan_id: int
    plans: PostgresPlanRepository
    tasks: PostgresTaskRepository

    async def new_task(self, title: str, **extra):
        return await self.tasks.create_task(
            self.user_id, TaskCreate(title=title, plan_id=self.plan_id, **extra),
        )

    async def new_criterion(self, task_id: int, description: str):
        return await self.tasks.create_criterion(
            self.user_id, task_id, CriterionCreate(description=description),
        )


@pytest_asyncio.fixture(loop_scope="session")
async def workspace(db_adapter):
    user = await PostgresUserRepository(db_adapter).create_user(
        UserCreate(external_id="plan-task-contracts", name="u", email="u@example.com"),
    )
    project = await PostgresProjectRepository(db_adapter).create_project(
        user.id, ProjectCreate(name="p", description="d", project_type=ProjectType.DEVELOPMENT),
    )
    plans = PostgresPlanRepository(db_adapter)
    plan = await plans.create_plan(user.id, PlanCreate(title="plan", project_id=project.id, goal="g"))
    return Workspace(user.id, plan.id, plans, PostgresTaskRepository(db_adapter))


# ---- plans ----


async def test_plan_update_without_fields_returns_the_stored_plan(workspace):
    stored = await workspace.plans.update_plan(workspace.user_id, workspace.plan_id, PlanUpdate())

    assert (stored.id, stored.title, stored.goal) == (workspace.plan_id, "plan", "g")


@pytest.mark.parametrize(
    "patch",
    [pytest.param(PlanUpdate(), id="without-fields"), pytest.param(PlanUpdate(title="x"), id="with-fields")],
)
async def test_updating_an_unknown_plan_raises_not_found(workspace, patch):
    with pytest.raises(NotFoundError, match=f"Plan with id {UNKNOWN_ID} not found"):
        await workspace.plans.update_plan(workspace.user_id, UNKNOWN_ID, patch)


# ---- tasks ----


async def test_task_listing_filters_by_state_and_assigned_agent(workspace):
    waiting = await workspace.new_task("waiting", assigned_agent="agent-a")
    await workspace.tasks.transition_task_state(
        workspace.user_id, waiting.id, TaskState.WAITING, expected_version=1,
    )
    await workspace.new_task("other agent", assigned_agent="agent-b")
    await workspace.new_task("unassigned")

    by_state = await workspace.tasks.list_tasks(
        workspace.user_id, workspace.plan_id, state=TaskState.WAITING,
    )
    by_agent = await workspace.tasks.list_tasks(
        workspace.user_id, workspace.plan_id, assigned_agent="agent-b",
    )
    combined = await workspace.tasks.list_tasks(
        workspace.user_id, workspace.plan_id, state=TaskState.TODO, assigned_agent="agent-a",
    )

    assert [t.title for t in by_state] == ["waiting"]
    assert [t.title for t in by_agent] == ["other agent"]
    assert combined == []


async def test_task_update_without_fields_returns_the_stored_task(workspace):
    task = await workspace.new_task("kept", priority=TaskPriority.P1)

    stored = await workspace.tasks.update_task(workspace.user_id, task.id, TaskUpdate())

    assert (stored.id, stored.title, stored.priority) == (task.id, "kept", TaskPriority.P1)
    assert stored.updated_at == task.updated_at


@pytest.mark.parametrize(
    "patch",
    [pytest.param(TaskUpdate(), id="without-fields"), pytest.param(TaskUpdate(title="x"), id="with-fields")],
)
async def test_updating_an_unknown_task_raises_not_found(workspace, patch):
    with pytest.raises(NotFoundError, match=f"Task with id {UNKNOWN_ID} not found"):
        await workspace.tasks.update_task(workspace.user_id, UNKNOWN_ID, patch)


async def test_dependency_on_an_unknown_task_surfaces_the_foreign_key_violation(workspace):
    task = await workspace.new_task("dependent")

    with pytest.raises(IntegrityError) as raised:
        await workspace.tasks.add_dependency(workspace.user_id, task.id, UNKNOWN_ID)

    assert type(raised.value.orig.__cause__).__name__ == "ForeignKeyViolationError"
    assert await workspace.tasks.get_dependencies(workspace.user_id, task.id) == []


# ---- criteria ----


async def test_criterion_description_update_keeps_the_met_state_and_time(workspace):
    task = await workspace.new_task("t")
    criterion = await workspace.new_criterion(task.id, "first wording")
    met = await workspace.tasks.update_criterion(
        workspace.user_id, criterion.id, CriterionUpdate(met=True),
    )

    reworded = await workspace.tasks.update_criterion(
        workspace.user_id, met.id, CriterionUpdate(description="second wording"),
    )

    assert reworded.description == "second wording"
    assert reworded.met is True
    assert reworded.met_at == met.met_at


async def test_criterion_update_without_fields_returns_the_stored_criterion(workspace):
    task = await workspace.new_task("t")
    criterion = await workspace.new_criterion(task.id, "kept")

    stored = await workspace.tasks.update_criterion(
        workspace.user_id, criterion.id, CriterionUpdate(),
    )

    assert (stored.id, stored.description, stored.met) == (criterion.id, "kept", False)


@pytest.mark.parametrize(
    "patch",
    [
        pytest.param(CriterionUpdate(), id="without-fields"),
        pytest.param(CriterionUpdate(met=True), id="with-fields"),
    ],
)
async def test_updating_an_unknown_criterion_raises_not_found(workspace, patch):
    with pytest.raises(NotFoundError, match=f"Criterion with id {UNKNOWN_ID} not found"):
        await workspace.tasks.update_criterion(workspace.user_id, UNKNOWN_ID, patch)


# ---- transitions whose blocker disappears before it is explained ----


async def test_transition_to_doing_reports_a_conflict_when_the_dependency_finishes_meanwhile(
    db_adapter, workspace,
):
    prerequisite = await workspace.new_task("prerequisite")
    task = await workspace.new_task("dependent")
    await workspace.tasks.add_dependency(workspace.user_id, task.id, prerequisite.id)

    async def prerequisite_finishes():
        await workspace.tasks.transition_task_state(
            workspace.user_id, prerequisite.id, TaskState.DONE, expected_version=1,
        )

    racing = PostgresTaskRepository(InterferingAdapter(db_adapter, prerequisite_finishes))

    with pytest.raises(ConflictError, match=f"Task {task.id} changed while moving to doing"):
        await racing.transition_task_state(
            workspace.user_id, task.id, TaskState.DOING, expected_version=1,
        )

    stored = await workspace.tasks.get_task_by_id(workspace.user_id, task.id)
    assert (stored.state, stored.version) == (TaskState.TODO, 1)


async def test_transition_to_done_reports_a_conflict_when_the_criterion_is_met_meanwhile(
    db_adapter, workspace,
):
    task = await workspace.new_task("t")
    criterion = await workspace.new_criterion(task.id, "c")

    async def criterion_gets_met():
        await workspace.tasks.update_criterion(
            workspace.user_id, criterion.id, CriterionUpdate(met=True),
        )

    racing = PostgresTaskRepository(InterferingAdapter(db_adapter, criterion_gets_met))

    with pytest.raises(ConflictError, match=f"Task {task.id} changed while moving to done"):
        await racing.transition_task_state(
            workspace.user_id, task.id, TaskState.DONE, expected_version=1,
        )

    stored = await workspace.tasks.get_task_by_id(workspace.user_id, task.id)
    assert (stored.state, stored.version) == (TaskState.TODO, 1)
