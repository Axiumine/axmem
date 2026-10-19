"""Code artifact tool adapters: wrap CodeArtifactService calls as registry-compatible callables."""

from typing import Any

from fastmcp import Context

from app.middleware.auth import get_user_from_auth
from app.models.code_artifact_models import (
    CodeArtifact,
    CodeArtifactCreate,
    CodeArtifactUpdate,
)
from app.routes.mcp.adapters.arguments import (
    coerce_int_id,
    coerce_optional_int_id,
    coerce_str_list,
)
from app.services.code_artifact_service import CodeArtifactService
from app.services.user_service import UserService
from app.utils.pydantic_helper import filter_none_values


class CodeArtifactToolAdapters:
    """Wraps code artifact service methods as registry-compatible callables"""

    def __init__(
        self, code_artifact_service: CodeArtifactService, user_service: UserService,
    ):
        self.code_artifact_service = code_artifact_service
        self.user_service = user_service

    async def create_code_artifact(
        self,
        title: str,
        description: str,
        code: str,
        language: str,
        ctx: Context,
        tags: list[str] | None = None,
        project_id: int | None = None,
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
    ) -> CodeArtifact:
        """Adapter for create_code_artifact tool"""
        project_id = coerce_optional_int_id(project_id, "project_id")
        user = await get_user_from_auth(ctx)

        artifact_data = CodeArtifactCreate(
            title=title,
            description=description,
            code=code,
            language=language,
            tags=tags or [],
            project_id=project_id,
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

        artifact = await self.code_artifact_service.create_code_artifact(
            user_id=user.id, artifact_data=artifact_data,
        )

        return artifact

    async def get_code_artifact(self, artifact_id: int, ctx: Context) -> CodeArtifact:
        """Adapter for get_code_artifact tool"""
        artifact_id = coerce_int_id(artifact_id, "artifact_id")
        user = await get_user_from_auth(ctx)

        artifact = await self.code_artifact_service.get_code_artifact(
            user_id=user.id, artifact_id=artifact_id,
        )

        return artifact

    async def list_code_artifacts(
        self,
        ctx: Context,
        project_id: int | None = None,
        language: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Adapter for list_code_artifacts tool"""
        project_id = coerce_optional_int_id(project_id, "project_id")
        tags = coerce_str_list(tags, "tags")
        user = await get_user_from_auth(ctx)

        result = await self.code_artifact_service.list_code_artifacts(
            user_id=user.id,
            project_id=project_id,
            language=language,
            tags=tags,
        )

        return {"code_artifacts": result, "total_count": len(result)}

    async def update_code_artifact(
        self,
        artifact_id: int,
        ctx: Context,
        title: str | None = None,
        description: str | None = None,
        code: str | None = None,
        language: str | None = None,
        tags: list[str] | None = None,
        project_id: int | None = None,
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
    ) -> CodeArtifact:
        """Adapter for update_code_artifact tool"""
        artifact_id = coerce_int_id(artifact_id, "artifact_id")
        project_id = coerce_optional_int_id(project_id, "project_id")
        user = await get_user_from_auth(ctx)

        updated_dict = filter_none_values(
            title=title,
            description=description,
            code=code,
            language=language,
            tags=tags,
            project_id=project_id,
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

        artifact_data = CodeArtifactUpdate(**updated_dict)

        artifact = await self.code_artifact_service.update_code_artifact(
            user_id=user.id,
            artifact_id=artifact_id,
            artifact_data=artifact_data,
        )

        return artifact

    async def delete_code_artifact(self, artifact_id: int, ctx: Context) -> dict:
        """Adapter for delete_code_artifact tool"""
        artifact_id = coerce_int_id(artifact_id, "artifact_id")
        user = await get_user_from_auth(ctx)

        result = await self.code_artifact_service.delete_code_artifact(
            user_id=user.id, artifact_id=artifact_id,
        )

        return {"success": result, "deleted_id": artifact_id}


def create_code_artifact_adapters(
    code_artifact_service: CodeArtifactService, user_service: UserService,
) -> dict[str, Any]:
    """Create all code artifact tool adapters and return as dict"""
    adapters = CodeArtifactToolAdapters(code_artifact_service, user_service)
    return {
        "create_code_artifact": adapters.create_code_artifact,
        "get_code_artifact": adapters.get_code_artifact,
        "list_code_artifacts": adapters.list_code_artifacts,
        "update_code_artifact": adapters.update_code_artifact,
        "delete_code_artifact": adapters.delete_code_artifact,
    }
