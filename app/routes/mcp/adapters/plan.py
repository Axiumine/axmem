"""Plan tool adapters: wrap PlanService calls as registry-compatible callables."""

from typing import TYPE_CHECKING, Any

from fastmcp import Context

from app.middleware.auth import get_user_from_auth
from app.models.plan_models import Plan, PlanCreate, PlanStatus, PlanUpdate
from app.services.user_service import UserService
from app.utils.pydantic_helper import filter_none_values

if TYPE_CHECKING:
    from app.services.plan_service import PlanService


class PlanToolAdapters:
    """Wraps plan service methods as registry-compatible callables"""

    def __init__(self, plan_service: "PlanService", user_service: UserService):
        self.plan_service = plan_service
        self.user_service = user_service

    async def create_plan(
        self,
        title: str,
        project_id: int,
        ctx: Context,
        goal: str | None = None,
        context: str | None = None,
        status: str = "draft",
        external_ref: str | None = None,
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
    ) -> Plan:
        user = await get_user_from_auth(ctx)
        plan_data = PlanCreate(
            title=title,
            project_id=project_id,
            goal=goal,
            context=context,
            status=PlanStatus(status),
            external_ref=external_ref,
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
        return await self.plan_service.create_plan(user_id=user.id, plan_data=plan_data)

    async def update_plan(
        self,
        plan_id: int,
        ctx: Context,
        title: str | None = None,
        goal: str | None = None,
        context: str | None = None,
        status: str | None = None,
        external_ref: str | None = None,
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
    ) -> Plan:
        user = await get_user_from_auth(ctx)
        updated_dict = filter_none_values(
            title=title, goal=goal, context=context,
            status=PlanStatus(status) if status else None,
            external_ref=external_ref,
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
        plan_data = PlanUpdate(**updated_dict)
        plan = await self.plan_service.update_plan(
            user_id=user.id, plan_id=plan_id, plan_data=plan_data,
        )
        return plan

    async def get_plan(self, plan_id: int, ctx: Context) -> Plan:
        user = await get_user_from_auth(ctx)
        return await self.plan_service.get_plan(user_id=user.id, plan_id=plan_id)

    async def list_plans(
        self,
        ctx: Context,
        project_id: int | None = None,
        status: str | None = None,
        external_ref: str | None = None,
    ) -> dict[str, Any]:
        user = await get_user_from_auth(ctx)
        status_enum = PlanStatus(status) if status else None
        plans = await self.plan_service.list_plans(
            user_id=user.id, project_id=project_id, status=status_enum,
            external_ref=external_ref,
        )
        return {"plans": plans, "total_count": len(plans)}


def create_plan_adapters(plan_service: "PlanService", user_service: UserService) -> dict[str, Any]:
    """Create all plan tool adapters and return as dict"""
    adapters = PlanToolAdapters(plan_service, user_service)
    return {
        "create_plan": adapters.create_plan,
        "update_plan": adapters.update_plan,
        "get_plan": adapters.get_plan,
        "list_plans": adapters.list_plans,
    }
