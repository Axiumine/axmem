"""Project tool adapters: wrap ProjectService calls as registry-compatible callables."""

from typing import Any

from fastmcp import Context

from app.exceptions import NotFoundError
from app.middleware.auth import get_user_from_auth
from app.models.project_models import (
    Project,
    ProjectCreate,
    ProjectStatus,
    ProjectType,
    ProjectUpdate,
)
from app.routes.mcp.adapters.arguments import coerce_int_id
from app.services.project_service import ProjectService
from app.services.user_service import UserService
from app.utils.pydantic_helper import filter_none_values


class ProjectToolAdapters:
    """Wraps project service methods as registry-compatible callables"""

    def __init__(self, project_service: ProjectService, user_service: UserService):
        self.project_service = project_service
        self.user_service = user_service

    async def create_project(
        self,
        name: str,
        description: str,
        project_type: ProjectType,
        ctx: Context,
        status: ProjectStatus = ProjectStatus.ACTIVE,
        repo_name: str | None = None,
        last_encoding_point: str | None = None,
        notes: str | None = None,
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
    ) -> Project:
        """Adapter for create_project tool"""
        user = await get_user_from_auth(ctx)

        project_data = ProjectCreate(
            name=name,
            description=description,
            project_type=project_type,
            status=status,
            repo_name=repo_name,
            last_encoding_point=last_encoding_point,
            notes=notes,
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

        project = await self.project_service.create_project(
            user_id=user.id, project_data=project_data,
        )

        return project

    async def update_project(
        self,
        project_id: int,
        ctx: Context,
        name: str | None = None,
        description: str | None = None,
        project_type: ProjectType | None = None,
        status: ProjectStatus | None = None,
        repo_name: str | None = None,
        last_encoding_point: str | None = None,
        notes: str | None = None,
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
    ) -> Project:
        """Adapter for update_project tool"""
        project_id = coerce_int_id(project_id, "project_id")
        user = await get_user_from_auth(ctx)

        updated_dict = filter_none_values(
            name=name,
            description=description,
            project_type=project_type,
            status=status,
            repo_name=repo_name,
            last_encoding_point=last_encoding_point,
            notes=notes,
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

        project_data = ProjectUpdate(**updated_dict)

        project = await self.project_service.update_project(
            user_id=user.id,
            project_id=project_id,
            project_data=project_data,
        )

        if project is None:
            raise NotFoundError(f"Project with id {project_id} not found")

        return project

    async def delete_project(self, project_id: int, ctx: Context) -> dict:
        """Adapter for delete_project tool"""
        project_id = coerce_int_id(project_id, "project_id")
        user = await get_user_from_auth(ctx)

        result = await self.project_service.delete_project(
            user_id=user.id, project_id=project_id,
        )

        return {"success": result, "project_id": project_id}

    async def list_projects(
        self,
        ctx: Context,
        status: str | None = None,
        repo_name: str | None = None,
        name: str | None = None,
    ) -> dict:
        """Adapter for list_projects tool"""
        user = await get_user_from_auth(ctx)

        # Convert string to enum if provided
        status_enum = ProjectStatus(status) if status else None

        result = await self.project_service.list_projects(
            user_id=user.id,
            status=status_enum,
            repo_name=repo_name,
            name=name,
        )

        count = len(result)
        return {
            "projects": result,
            "total_count": count,
            "count": count,
            "name_filter": name,
        }

    async def get_project(self, project_id: int, ctx: Context) -> Project:
        """Adapter for get_project tool"""
        project_id = coerce_int_id(project_id, "project_id")
        user = await get_user_from_auth(ctx)

        project = await self.project_service.get_project(
            user_id=user.id, project_id=project_id,
        )

        return project


def create_project_adapters(
    project_service: ProjectService, user_service: UserService,
) -> dict[str, Any]:
    """Create all project tool adapters and return as dict"""
    adapters = ProjectToolAdapters(project_service, user_service)
    return {
        "create_project": adapters.create_project,
        "update_project": adapters.update_project,
        "delete_project": adapters.delete_project,
        "list_projects": adapters.list_projects,
        "get_project": adapters.get_project,
    }
