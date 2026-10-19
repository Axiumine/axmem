"""In-memory TaskRepository stub (task CRUD, listing and state transitions)."""

from datetime import UTC, datetime
from uuid import UUID

from app.exceptions import ConflictError
from app.models.plan_models import (
    Criterion,
    Task,
    TaskCreate,
    TaskDependency,
    TaskPriority,
    TaskState,
    TaskSummary,
    TaskUpdate,
)
from app.protocols.task_protocol import TaskRepository
from tests.integration.stubs.helpers import (
    attr_equals,
    filter_items,
    find_item,
    newest_first,
    patch_item,
    provenance_kwargs,
    require_item,
    store_item,
)
from tests.integration.stubs.task_subresources import TaskSubresourceMixin


def _has_unfinished_dependency(user_tasks: dict[int, Task], dep_ids: list[int]) -> bool:
    """A task is blocked if any of its (known) dependencies is NOT done."""
    dependencies = (user_tasks.get(dep_id) for dep_id in dep_ids)
    return any(dep and dep.state != TaskState.DONE for dep in dependencies)


class InMemoryTaskRepository(TaskSubresourceMixin, TaskRepository):
    """In-memory implementation of TaskRepository for testing.

    Stores tasks, criteria, and dependencies with user_id-scoped RLS filtering.
    """

    def __init__(self):
        self._tasks: dict[UUID, dict[int, Task]] = {}  # user_id -> {task_id -> Task}
        self._criteria: dict[
            UUID, dict[int, Criterion],
        ] = {}  # user_id -> {criterion_id -> Criterion}
        self._dependencies: dict[
            UUID, dict[int, TaskDependency],
        ] = {}  # user_id -> {dep_id -> TaskDependency}
        self._next_task_id = 1
        self._next_criterion_id = 1
        self._next_dependency_id = 1

    # ---- Task CRUD ----

    async def create_task(self, user_id: UUID, task_data: TaskCreate) -> Task:
        task_id = self._next_task_id
        self._next_task_id += 1
        now = datetime.now(UTC)

        new_task = Task(
            id=task_id,
            plan_id=task_data.plan_id,
            title=task_data.title,
            description=task_data.description,
            state=TaskState.TODO,
            priority=task_data.priority,
            assigned_agent=task_data.assigned_agent,
            version=1,
            criteria=[],
            dependency_ids=[],
            created_at=now,
            updated_at=now,
            **provenance_kwargs(task_data),
        )

        # The plan's task_count is maintained by the service layer, not here
        return store_item(self._tasks, user_id, task_id, new_task)

    async def _hydrate(self, user_id: UUID, task: Task) -> Task:
        """Fill criteria and dependency_ids from the auxiliary stores."""
        task.criteria = await self.get_criteria_for_task(user_id, task.id)
        task.dependency_ids = await self.get_dependencies(user_id, task.id)
        return task

    async def get_task_by_id(self, user_id: UUID, task_id: int) -> Task | None:
        task = find_item(self._tasks, user_id, task_id)
        if task is None:
            return None
        return await self._hydrate(user_id, task)

    async def _summarize(self, user_id: UUID, task: Task) -> TaskSummary:
        """Build the list row for a task, including criteria and blocked state."""
        criteria = await self.get_criteria_for_task(user_id, task.id)
        deps = await self.get_dependencies(user_id, task.id)
        return TaskSummary(
            id=task.id,
            title=task.title,
            plan_id=task.plan_id,
            state=task.state,
            priority=task.priority,
            assigned_agent=task.assigned_agent,
            version=task.version,
            criteria_met=sum(1 for c in criteria if c.met),
            criteria_total=len(criteria),
            blocked=_has_unfinished_dependency(self._tasks.get(user_id, {}), deps),
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    async def list_tasks(
        self,
        user_id: UUID,
        plan_id: int,
        state: TaskState | None = None,
        priority: TaskPriority | None = None,
        assigned_agent: str | None = None,
    ) -> list[TaskSummary]:
        tasks = filter_items(
            self._tasks.get(user_id, {}).values(),
            (True, attr_equals("plan_id", plan_id)),
            (state is not None, attr_equals("state", state)),
            (priority is not None, attr_equals("priority", priority)),
            (assigned_agent is not None, attr_equals("assigned_agent", assigned_agent)),
        )
        return [await self._summarize(user_id, t) for t in newest_first(tasks)]

    async def update_task(
        self, user_id: UUID, task_id: int, task_data: TaskUpdate,
    ) -> Task:
        task = require_item(
            await self.get_task_by_id(user_id, task_id),
            f"Task with id {task_id} not found",
        )
        return patch_item(task, task_data.model_dump(exclude_unset=True))

    async def delete_task(self, user_id: UUID, task_id: int) -> bool:
        user_tasks = self._tasks.get(user_id, {})
        if task_id not in user_tasks:
            return False
        del user_tasks[task_id]
        # Clean up criteria and dependencies involving this task
        user_criteria = self._criteria.get(user_id, {})
        for cid in [cid for cid, c in user_criteria.items() if c.task_id == task_id]:
            del user_criteria[cid]
        user_deps = self._dependencies.get(user_id, {})
        for did in [
            did
            for did, d in user_deps.items()
            if task_id in (d.task_id, d.depends_on_task_id)
        ]:
            del user_deps[did]
        return True

    # ---- Atomic state transition ----

    async def transition_task_state(
        self,
        user_id: UUID,
        task_id: int,
        new_state: TaskState,
        expected_version: int,
        assigned_agent: str | None = None,
    ) -> Task:
        task = require_item(
            find_item(self._tasks, user_id, task_id),
            f"Task with id {task_id} not found",
        )

        if task.version != expected_version:
            raise ConflictError(
                f"Version mismatch: expected {expected_version}, got {task.version}. "
                f"Task was modified by another agent.",
            )

        task.state = new_state
        task.version += 1
        if assigned_agent is not None:
            task.assigned_agent = assigned_agent
        task.updated_at = datetime.now(UTC)

        # Re-hydrate criteria and deps before returning
        return await self._hydrate(user_id, task)
