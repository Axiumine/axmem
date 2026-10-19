"""Criteria and dependency methods of the in-memory TaskRepository stub."""

from datetime import UTC, datetime
from uuid import UUID

from app.models.plan_models import (
    Criterion,
    CriterionCreate,
    CriterionUpdate,
    TaskDependency,
)
from tests.integration.stubs.helpers import (
    find_item,
    patch_item,
    remove_item,
    require_item,
    store_item,
)


class TaskSubresourceMixin:
    """Criteria CRUD and dependency edges; expects the stores from the host class."""

    _criteria: dict[UUID, dict[int, Criterion]]
    _dependencies: dict[UUID, dict[int, TaskDependency]]
    _next_criterion_id: int
    _next_dependency_id: int

    # ---- Criteria CRUD ----

    async def create_criterion(
        self, user_id: UUID, task_id: int, criterion_data: CriterionCreate,
    ) -> Criterion:
        criterion_id = self._next_criterion_id
        self._next_criterion_id += 1
        now = datetime.now(UTC)

        new_criterion = Criterion(
            id=criterion_id,
            task_id=task_id,
            description=criterion_data.description,
            met=False,
            met_at=None,
            created_at=now,
            updated_at=now,
        )
        return store_item(self._criteria, user_id, criterion_id, new_criterion)

    async def update_criterion(
        self, user_id: UUID, criterion_id: int, criterion_data: CriterionUpdate,
    ) -> Criterion:
        criterion = require_item(
            find_item(self._criteria, user_id, criterion_id),
            f"Criterion with id {criterion_id} not found",
        )
        was_met = criterion.met and criterion.met_at is not None
        patch_item(criterion, criterion_data.model_dump(exclude_unset=True))

        # Stamp met_at only when met actually changes to True (mirrors the real repositories)
        if criterion_data.met is True and not was_met:
            criterion.met_at = datetime.now(UTC)
        elif criterion_data.met is False:
            criterion.met_at = None
        return criterion

    async def delete_criterion(self, user_id: UUID, criterion_id: int) -> bool:
        return remove_item(self._criteria, user_id, criterion_id)

    async def get_criterion_by_id(
        self, user_id: UUID, criterion_id: int,
    ) -> Criterion | None:
        criterion = find_item(self._criteria, user_id, criterion_id)
        return criterion.model_copy() if criterion else None  # a snapshot, like a database read

    async def get_criteria_for_task(
        self, user_id: UUID, task_id: int,
    ) -> list[Criterion]:
        user_criteria = self._criteria.get(user_id, {})
        criteria = [c for c in user_criteria.values() if c.task_id == task_id]
        criteria.sort(key=lambda c: c.id)
        return criteria

    # ---- Dependencies ----

    async def add_dependency(
        self, user_id: UUID, task_id: int, depends_on_task_id: int,
    ) -> TaskDependency:
        dep_id = self._next_dependency_id
        self._next_dependency_id += 1

        new_dep = TaskDependency(
            id=dep_id,
            task_id=task_id,
            depends_on_task_id=depends_on_task_id,
            created_at=datetime.now(UTC),
        )
        return store_item(self._dependencies, user_id, dep_id, new_dep)

    async def remove_dependency(
        self, user_id: UUID, task_id: int, depends_on_task_id: int,
    ) -> bool:
        user_deps = self._dependencies.get(user_id, {})
        edge = (task_id, depends_on_task_id)
        match = next(
            (
                did
                for did, dep in user_deps.items()
                if (dep.task_id, dep.depends_on_task_id) == edge
            ),
            None,
        )
        if match is None:
            return False
        del user_deps[match]
        return True

    async def get_dependencies(self, user_id: UUID, task_id: int) -> list[int]:
        """Get IDs of tasks that this task depends on."""
        user_deps = self._dependencies.get(user_id, {})
        return [
            dep.depends_on_task_id
            for dep in user_deps.values()
            if dep.task_id == task_id
        ]
