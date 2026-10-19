"""Task tool adapters: wrap TaskService calls as registry-compatible callables."""

from typing import TYPE_CHECKING, Any

from fastmcp import Context

from app.middleware.auth import get_user_from_auth
from app.models.plan_models import (
    Criterion,
    CriterionCreate,
    CriterionUpdate,
    Task,
    TaskCreate,
    TaskPriority,
    TaskState,
    TaskUpdate,
)
from app.services.user_service import UserService
from app.utils.pydantic_helper import filter_none_values

if TYPE_CHECKING:
    from app.services.task_service import TaskService


class TaskToolAdapters:
    """Wraps task service methods as registry-compatible callables"""

    def __init__(self, task_service: TaskService, user_service: UserService):
        self.task_service = task_service
        self.user_service = user_service

    async def create_task(
        self,
        title: str,
        plan_id: int,
        ctx: Context,
        description: str | None = None,
        priority: str = "P2",
        assigned_agent: str | None = None,
        criteria: list[dict[str, Any]] | None = None,
        dependency_ids: list[int] | None = None,
        # Provenance
        source_repo: str | None = None,
        source_files: list[str] | None = None,
        source_url: str | None = None,
        confidence: float | None = None,
        encoding_agent: str | None = None,
        encoding_version: str | None = None,
        agent_id: str | None = None,
        agent_version: str | None = None,
        agent_model: str | None = None,
    ) -> Task:
        user = await get_user_from_auth(ctx)
        criteria_models = None
        if criteria:
            criteria_models = [CriterionCreate(**c) for c in criteria]
        task_data = TaskCreate(
            title=title,
            plan_id=plan_id,
            description=description,
            priority=TaskPriority(priority),
            assigned_agent=assigned_agent,
            criteria=criteria_models,
            dependency_ids=dependency_ids,
            source_repo=source_repo,
            source_files=source_files,
            source_url=source_url,
            confidence=confidence,
            encoding_agent=encoding_agent,
            encoding_version=encoding_version,
            agent_id=agent_id,
            agent_version=agent_version,
            agent_model=agent_model,
        )
        return await self.task_service.create_task(user_id=user.id, task_data=task_data)

    async def update_task(
        self,
        task_id: int,
        ctx: Context,
        title: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        # Provenance
        source_repo: str | None = None,
        source_files: list[str] | None = None,
        source_url: str | None = None,
        confidence: float | None = None,
        encoding_agent: str | None = None,
        encoding_version: str | None = None,
        agent_id: str | None = None,
        agent_version: str | None = None,
        agent_model: str | None = None,
    ) -> Task:
        user = await get_user_from_auth(ctx)
        updated_dict = filter_none_values(
            title=title, description=description,
            priority=TaskPriority(priority) if priority else None,
            source_repo=source_repo,
            source_files=source_files,
            source_url=source_url,
            confidence=confidence,
            encoding_agent=encoding_agent,
            encoding_version=encoding_version,
            agent_id=agent_id,
            agent_version=agent_version,
            agent_model=agent_model,
        )
        task_data = TaskUpdate(**updated_dict)
        return await self.task_service.update_task(
            user_id=user.id, task_id=task_id, task_data=task_data,
        )

    async def get_task(self, task_id: int, ctx: Context) -> Task:
        user = await get_user_from_auth(ctx)
        return await self.task_service.get_task(user_id=user.id, task_id=task_id)

    async def query_tasks(
        self,
        plan_id: int,
        ctx: Context,
        state: str | None = None,
        priority: str | None = None,
        assigned_agent: str | None = None,
    ) -> dict[str, Any]:
        user = await get_user_from_auth(ctx)
        state_enum = TaskState(state) if state else None
        priority_enum = TaskPriority(priority) if priority else None
        tasks = await self.task_service.list_tasks(
            user_id=user.id, plan_id=plan_id,
            state=state_enum, priority=priority_enum,
            assigned_agent=assigned_agent,
        )
        return {"tasks": tasks, "total_count": len(tasks)}

    async def claim_task(
        self,
        task_id: int,
        agent_id: str,
        version: int,
        ctx: Context,
    ) -> Task:
        user = await get_user_from_auth(ctx)
        return await self.task_service.claim_task(
            user_id=user.id, task_id=task_id,
            agent_id=agent_id, expected_version=version,
        )

    async def transition_task(
        self,
        task_id: int,
        state: str,
        version: int,
        ctx: Context,
    ) -> Task:
        user = await get_user_from_auth(ctx)
        return await self.task_service.transition_task(
            user_id=user.id, task_id=task_id,
            new_state=TaskState(state), expected_version=version,
        )

    async def add_criterion(
        self,
        task_id: int,
        description: str,
        ctx: Context,
    ) -> Criterion:
        user = await get_user_from_auth(ctx)
        return await self.task_service.add_criterion(
            user_id=user.id, task_id=task_id,
            criterion_data=CriterionCreate(description=description),
        )

    async def verify_criterion(
        self,
        criterion_id: int,
        met: bool,
        ctx: Context,
    ) -> Criterion:
        user = await get_user_from_auth(ctx)
        return await self.task_service.update_criterion(
            user_id=user.id, criterion_id=criterion_id,
            criterion_data=CriterionUpdate(met=met),
        )

    async def delete_criterion(self, criterion_id: int, ctx: Context) -> dict[str, Any]:
        user = await get_user_from_auth(ctx)
        success = await self.task_service.delete_criterion(
            user_id=user.id, criterion_id=criterion_id,
        )
        return {"success": success}

    async def add_dependency(
        self,
        task_id: int,
        depends_on_task_id: int,
        ctx: Context,
    ) -> None:
        user = await get_user_from_auth(ctx)
        return await self.task_service.add_dependency(
            user_id=user.id, task_id=task_id,
            depends_on_task_id=depends_on_task_id,
        )

    async def remove_dependency(
        self,
        task_id: int,
        depends_on_task_id: int,
        ctx: Context,
    ) -> dict[str, Any]:
        user = await get_user_from_auth(ctx)
        success = await self.task_service.remove_dependency(
            user_id=user.id, task_id=task_id,
            depends_on_task_id=depends_on_task_id,
        )
        return {"success": success}


def create_task_adapters(task_service: TaskService, user_service: UserService) -> dict[str, Any]:
    """Create all task tool adapters and return as dict"""
    adapters = TaskToolAdapters(task_service, user_service)
    return {
        "create_task": adapters.create_task,
        "update_task": adapters.update_task,
        "get_task": adapters.get_task,
        "query_tasks": adapters.query_tasks,
        "claim_task": adapters.claim_task,
        "transition_task": adapters.transition_task,
        "add_criterion": adapters.add_criterion,
        "verify_criterion": adapters.verify_criterion,
        "delete_criterion": adapters.delete_criterion,
        "add_dependency": adapters.add_dependency,
        "remove_dependency": adapters.remove_dependency,
    }
