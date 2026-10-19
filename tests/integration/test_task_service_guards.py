"""TaskService guards: unknown tasks, forbidden states and writes that lose a race.

Covers what the service answers when the task it is asked about does not exist, is in a
state that forbids the operation, or disappears between its own reads and writes, plus the
rollback of a half-created task and the plan auto-completion rules.
"""

import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.exceptions import InvalidStateTransitionError, NotFoundError
from app.models.activity_models import ActionType, EntityType
from app.models.plan_models import (
    CriterionCreate,
    PlanCreate,
    PlanStatus,
    PlanUpdate,
    TaskCreate,
    TaskState,
)
from app.services.plan_service import PlanService
from app.services.task_service import TaskService
from tests.integration.stubs import CollectingEventBus


@pytest.fixture
def world(mock_plan_repository, mock_task_repository):
    bus = CollectingEventBus()
    plans = PlanService(mock_plan_repository, event_bus=bus)
    tasks = TaskService(mock_task_repository, plan_service=plans, event_bus=bus)
    return SimpleNamespace(
        plans=plans, tasks=tasks, repo=mock_task_repository, bus=bus, user_id=uuid4(),
    )


async def _plan(world, *, active: bool = True):
    plan = await world.plans.create_plan(
        user_id=world.user_id, plan_data=PlanCreate(title="Plan", project_id=1, goal="g"),
    )
    if active:
        plan = await world.plans.update_plan(
            user_id=world.user_id, plan_id=plan.id, plan_data=PlanUpdate(status=PlanStatus.ACTIVE),
        )
    return plan


async def _task(world, plan, title="Task", **fields):
    return await world.tasks.create_task(
        user_id=world.user_id, task_data=TaskCreate(title=title, plan_id=plan.id, **fields),
    )


async def _move(world, task, state):
    current = await world.tasks.get_task(world.user_id, task.id)
    return await world.tasks.transition_task(
        user_id=world.user_id, task_id=task.id, new_state=state, expected_version=current.version,
    )


def _task_events(world, action):
    return [e for e in world.bus.collected_events if e.entity_type == EntityType.TASK and e.action == action]


async def test_deleting_an_unknown_task_returns_false(world):
    assert await world.tasks.delete_task(world.user_id, 999) is False
    assert _task_events(world, ActionType.DELETED) == []


async def test_delete_that_loses_a_race_returns_false_without_a_deleted_event(world, monkeypatch):
    task = await _task(world, await _plan(world))
    world.bus.collected_events.clear()

    async def already_deleted(user_id, task_id):
        return False

    monkeypatch.setattr(world.repo, "delete_task", already_deleted)

    assert await world.tasks.delete_task(world.user_id, task.id) is False
    assert _task_events(world, ActionType.DELETED) == []


@pytest.mark.parametrize("operation", ["transition", "claim"])
async def test_state_change_of_an_unknown_task_is_not_found(world, operation):
    call = (
        world.tasks.transition_task(world.user_id, 999, TaskState.DOING, 1)
        if operation == "transition"
        else world.tasks.claim_task(world.user_id, 999, "agent-1", 1)
    )

    with pytest.raises(NotFoundError, match="Task with id 999 not found"):
        await call


@pytest.mark.parametrize("state", [TaskState.DOING, TaskState.CANCELLED])
async def test_claiming_a_task_that_is_neither_todo_nor_waiting_is_refused(world, state):
    task = await _task(world, await _plan(world))
    moved = await _move(world, task, state)

    with pytest.raises(
        InvalidStateTransitionError,
        match=f"Cannot claim task in {state.value} state. Must be todo or waiting.",
    ):
        await world.tasks.claim_task(world.user_id, task.id, "agent-1", moved.version)


async def test_adding_a_criterion_to_an_unknown_task_is_not_found(world):
    with pytest.raises(NotFoundError, match="Task with id 999 not found"):
        await world.tasks.add_criterion(world.user_id, 999, CriterionCreate(description="c"))


async def test_adding_a_criterion_to_a_completed_task_is_refused(world):
    task = await _task(world, await _plan(world))
    await _move(world, task, TaskState.DOING)
    await _move(world, task, TaskState.DONE)

    with pytest.raises(InvalidStateTransitionError, match="Cannot add criteria to a completed task"):
        await world.tasks.add_criterion(world.user_id, task.id, CriterionCreate(description="late"))


async def test_criterion_delete_that_loses_a_race_returns_false_without_a_task_update(world, monkeypatch):
    task = await _task(world, await _plan(world), criteria=[CriterionCreate(description="c")])
    criterion_id = task.criteria[0].id
    world.bus.collected_events.clear()

    async def already_deleted(user_id, criterion_id):
        return False

    monkeypatch.setattr(world.repo, "delete_criterion", already_deleted)

    assert await world.tasks.delete_criterion(world.user_id, criterion_id) is False
    assert _task_events(world, ActionType.UPDATED) == []


async def test_task_removed_while_its_criterion_is_deleted_emits_no_update(world, monkeypatch):
    task = await _task(world, await _plan(world), criteria=[CriterionCreate(description="c")])
    criterion_id = task.criteria[0].id
    world.bus.collected_events.clear()
    delete_criterion = world.repo.delete_criterion

    async def delete_criterion_then_task(user_id, criterion_id):
        deleted = await delete_criterion(user_id, criterion_id)
        await world.repo.delete_task(user_id=user_id, task_id=task.id)
        return deleted

    monkeypatch.setattr(world.repo, "delete_criterion", delete_criterion_then_task)

    assert await world.tasks.delete_criterion(world.user_id, criterion_id) is True
    assert _task_events(world, ActionType.UPDATED) == []


async def test_dependency_on_or_from_an_unknown_task_is_not_found(world):
    task = await _task(world, await _plan(world))

    with pytest.raises(NotFoundError, match="Task with id 999 not found"):
        await world.tasks.add_dependency(world.user_id, 999, task.id)
    with pytest.raises(NotFoundError, match="Task with id 998 not found"):
        await world.tasks.add_dependency(world.user_id, task.id, 998)


async def test_dependency_on_a_task_with_converging_paths_is_accepted(world):
    plan = await _plan(world)
    base = await _task(world, plan, "base")
    left = await _task(world, plan, "left", dependency_ids=[base.id])
    right = await _task(world, plan, "right", dependency_ids=[base.id])
    top = await _task(world, plan, "top", dependency_ids=[left.id, right.id])
    newcomer = await _task(world, plan, "newcomer")

    await world.tasks.add_dependency(world.user_id, newcomer.id, top.id)

    stored = await world.tasks.get_task(world.user_id, newcomer.id)
    assert stored.dependency_ids == [top.id]


async def test_failed_rollback_of_a_partly_created_task_is_logged_and_the_cause_propagates(
    world, monkeypatch, caplog,
):
    plan = await _plan(world)

    async def criterion_store_down(**_):
        raise RuntimeError("criterion store down")

    async def delete_store_down(**_):
        raise RuntimeError("delete store down")

    monkeypatch.setattr(world.repo, "create_criterion", criterion_store_down)
    monkeypatch.setattr(world.repo, "delete_task", delete_store_down)
    caplog.set_level(logging.ERROR, logger="app.services.task_service")

    with pytest.raises(RuntimeError, match="criterion store down"):
        await _task(world, plan, criteria=[CriterionCreate(description="c")])

    assert "failed to roll back partially created task" in [r.getMessage() for r in caplog.records]
    assert len(await world.tasks.list_tasks(world.user_id, plan.id)) == 1


async def test_deleting_the_only_task_leaves_the_plan_active(world):
    plan = await _plan(world)
    task = await _task(world, plan)

    assert await world.tasks.delete_task(world.user_id, task.id) is True

    refreshed = await world.plans.get_plan(world.user_id, plan.id, track_read=False)
    assert refreshed.status == PlanStatus.ACTIVE


async def test_finishing_every_task_of_a_draft_plan_does_not_complete_it(world):
    plan = await _plan(world, active=False)
    task = await _task(world, plan)
    await _move(world, task, TaskState.DOING)
    await _move(world, task, TaskState.DONE)

    refreshed = await world.plans.get_plan(world.user_id, plan.id, track_read=False)
    assert refreshed.status == PlanStatus.DRAFT
