"""User tool adapters: wrap UserService calls as registry-compatible callables."""

from typing import Any, cast

from fastmcp import Context

from app.config.logging_config import logging
from app.middleware.auth import get_user_from_auth
from app.models.user_models import User, UserResponse, UserUpdate
from app.services.user_service import UserService

logger = logging.getLogger(__name__)


class UserToolAdapters:
    """Wraps user service methods as registry-compatible callables"""

    def __init__(self, user_service: UserService):
        self.user_service = user_service

    async def get_current_user(self, ctx: Context) -> UserResponse:
        """Adapter for get_current_user tool"""
        user = await get_user_from_auth(ctx)
        logger.info(
            "Successfully retrieved current user",
            extra={
                "user": user.name,
                "user_id": user.id,
                "external_id": user.external_id,
            },
        )
        return UserResponse(**user.model_dump())

    async def update_user_notes(self, user_notes: str, ctx: Context) -> UserResponse:
        """Adapter for update_user_notes tool"""
        user = await get_user_from_auth(ctx)

        user_update = UserUpdate(external_id=user.external_id, notes=user_notes)

        # get_user_from_auth provisioned the user, so update_user finds it instead of returning None
        updated_user = cast(
            User, await self.user_service.update_user(user_update=user_update),
        )
        return UserResponse(**updated_user.model_dump())


def create_user_adapters(user_service: UserService) -> dict[str, Any]:
    """Create all user tool adapters and return as dict"""
    adapters = UserToolAdapters(user_service)
    return {
        "get_current_user": adapters.get_current_user,
        "update_user_notes": adapters.update_user_notes,
    }
