"""Task repository on a real SQLite database: filters, unknown ids and lost updates.

The task service validates a transition before it calls the repository, so the repository's
own answers (unknown rows, foreign-key failures, a guarded UPDATE that matches nothing) are
only reachable by calling it directly.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.exceptions import ConflictError, NotFoundError
from app.models.plan_models import (
    CriterionCreate,
    CriterionUpdate,
    PlanCreate,
    TaskCreate,
    TaskState,
    TaskUpdate,
)
from app.models.project_models import ProjectCreate, ProjectType
from app.repositories.sqlite.plan_repository import SqlitePlanRepository
from app.repositories.sqlite.project_repository import SqliteProjectRepository
from app.repositories.sqlite.task_repository import SqliteTaskRepository
from tests.e2e_sqlite.sqlite_repository_helpers import (
    UNKNOWN_ID,
    create_user,
    open_adapter,
)


@pytest.fixture
async def adapter(monkeypatch):
    async with open_adapter(monkeypatch) as opened:
        yield opened


@pytest.fixture
async def user_id(adapter):
    return await create_user(adapter)


@pytest.fixture
async def plan_id(adapter, user_id):
    project = await SqliteProjectRepository(adapter).create_project(
        user_id,
        ProjectCreate(name="Atlas", description="Task target", project_type=ProjectType.WORK),
    )
    plan = await SqlitePlanRepository(adapter).create_plan(
        user_id, PlanCreate(title="Ship v2", project_id=project.id),
    )
    return plan.id


@pytest.fixture
def tasks(adapter):
    return SqliteTaskRepository(adapter)


async def test_tasks_can_be_listed_by_assigned_agent(tasks, user_id, plan_id):
    await tasks.create_task(user_id, TaskCreate(title="Write docs", plan_id=plan_id, assigned_agent="agent-a"))
    await tasks.create_task(user_id, TaskCreate(title="Fix bug", plan_id=plan_id, assigned_agent="agent-b"))
    await tasks.create_task(user_id, TaskCreate(title="Triage", plan_id=plan_id))

    summaries = await tasks.list_tasks(user_id, plan_id, assigned_agent="agent-a")

    assert [summary.title for summary in summaries] == ["Write docs"]
    assert summaries[0].assigned_agent == "agent-a"


async def test_task_update_of_an_unknown_task_raises_not_found(tasks, user_id):
    with pytest.raises(NotFoundError, match=f"^Task with id {UNKNOWN_ID} not found$"):
        await tasks.update_task(user_id, UNKNOWN_ID, TaskUpdate(title="Renamed"))


async def test_unknown_criterion_reads_as_none(tasks, user_id):
    assert await tasks.get_criterion_by_id(user_id, UNKNOWN_ID) is None


@pytest.mark.parametrize(
    "patch",
    [CriterionUpdate(), CriterionUpdate(met=True)],
    ids=["empty-patch", "met-patch"],
)
async def test_criterion_update_of_an_unknown_criterion_raises_not_found(tasks, user_id, patch):
    with pytest.raises(NotFoundError, match=f"^Criterion with id {UNKNOWN_ID} not found$"):
        await tasks.update_criterion(user_id, UNKNOWN_ID, patch)


async def test_empty_criterion_update_returns_the_stored_criterion(tasks, user_id, plan_id):
    task = await tasks.create_task(user_id, TaskCreate(title="Write docs", plan_id=plan_id))
    criterion = await tasks.create_criterion(user_id, task.id, CriterionCreate(description="Reviewed"))

    result = await tasks.update_criterion(user_id, criterion.id, CriterionUpdate())

    assert result == criterion
    assert result.met is False


async def test_dependency_on_an_unknown_task_surfaces_the_foreign_key_failure(tasks, user_id, plan_id):
    task = await tasks.create_task(user_id, TaskCreate(title="Write docs", plan_id=plan_id))

    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        await tasks.add_dependency(user_id, task.id, UNKNOWN_ID)

    assert await tasks.get_dependencies(user_id, task.id) == []


@pytest.mark.parametrize("new_state", [TaskState.TODO, TaskState.DOING, TaskState.DONE])
async def test_transition_whose_update_is_silently_dropped_reports_a_conflict(
    adapter, tasks, user_id, plan_id, new_state,
):
    """Nothing blocks the move (no open dependency, no unmet criterion), yet the row is not updated."""
    task = await tasks.create_task(user_id, TaskCreate(title="Write docs", plan_id=plan_id))
    async with adapter.system_session() as session:
        await session.execute(text(
            "CREATE TRIGGER tasks_ignore_updates BEFORE UPDATE ON tasks BEGIN SELECT RAISE(IGNORE); END",
        ))

    with pytest.raises(
        ConflictError,
        match=f"^Task {task.id} changed while moving to {new_state.value}; reload it and retry$",
    ):
        await tasks.transition_task_state(user_id, task.id, new_state, expected_version=task.version)

    unchanged = await tasks.get_task_by_id(user_id, task.id)
    assert unchanged is not None
    assert unchanged.state == TaskState.TODO
    assert unchanged.version == task.version
