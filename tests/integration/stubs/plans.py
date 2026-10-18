"""In-memory PlanRepository stub."""

from datetime import UTC, datetime
from uuid import UUID

from app.models.plan_models import (
    Plan,
    PlanCreate,
    PlanStatus,
    PlanSummary,
    PlanUpdate,
)
from app.protocols.plan_protocol import PlanRepository
from tests.integration.stubs.helpers import (
    attr_equals,
    filter_items,
    find_item,
    newest_first,
    patch_item,
    provenance_kwargs,
    remove_item,
    require_item,
    store_item,
)


class InMemoryPlanRepository(PlanRepository):
    """In-memory implementation of PlanRepository for testing"""

    def __init__(self):
        self._plans: dict[UUID, dict[int, Plan]] = {}  # user_id -> {plan_id -> Plan}
        self._next_id = 1

    async def create_plan(self, user_id: UUID, plan_data: PlanCreate) -> Plan:
        plan_id = self._next_id
        self._next_id += 1
        now = datetime.now(UTC)

        new_plan = Plan(
            id=plan_id,
            user_id=str(user_id),
            title=plan_data.title,
            project_id=plan_data.project_id,
            goal=plan_data.goal,
            context=plan_data.context,
            status=plan_data.status,
            external_ref=plan_data.external_ref,
            task_count=0,
            created_at=now,
            updated_at=now,
            **provenance_kwargs(plan_data),
        )
        return store_item(self._plans, user_id, plan_id, new_plan)

    async def get_plan_by_id(self, user_id: UUID, plan_id: int) -> Plan | None:
        return find_item(self._plans, user_id, plan_id)

    async def list_plans(
        self,
        user_id: UUID,
        project_id: int | None = None,
        status: PlanStatus | None = None,
        external_ref: str | None = None,
    ) -> list[PlanSummary]:
        plans = filter_items(
            self._plans.get(user_id, {}).values(),
            (project_id is not None, attr_equals("project_id", project_id)),
            (status is not None, attr_equals("status", status)),
            (external_ref is not None, attr_equals("external_ref", external_ref)),
        )

        return [
            PlanSummary(
                id=p.id,
                title=p.title,
                project_id=p.project_id,
                status=p.status,
                external_ref=p.external_ref,
                task_count=p.task_count,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
            for p in newest_first(plans)
        ]

    async def update_plan(
        self, user_id: UUID, plan_id: int, plan_data: PlanUpdate,
    ) -> Plan:
        plan = require_item(
            await self.get_plan_by_id(user_id, plan_id),
            f"Plan with id {plan_id} not found",
        )
        return patch_item(plan, plan_data.model_dump(exclude_unset=True))

    async def delete_plan(self, user_id: UUID, plan_id: int) -> bool:
        return remove_item(self._plans, user_id, plan_id)
