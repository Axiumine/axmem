"""File tool adapters: wrap FileService calls as registry-compatible callables."""

from typing import TYPE_CHECKING, Any

from fastmcp import Context

from app.middleware.auth import get_user_from_auth
from app.models.file_models import File, FileCreate, FileUpdate
from app.routes.mcp.adapters.arguments import (
    coerce_int_id,
    coerce_optional_int_id,
    coerce_str_list,
)
from app.services.user_service import UserService
from app.utils.pydantic_helper import filter_none_values

if TYPE_CHECKING:
    from app.services.file_service import FileService


class FileToolAdapters:
    """Wraps file service methods as registry-compatible callables"""

    def __init__(self, file_service: "FileService", user_service: UserService):
        self.file_service = file_service
        self.user_service = user_service

    async def create_file(
        self,
        filename: str,
        description: str,
        data: str,
        mime_type: str,
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
    ) -> File:
        """Adapter for create_file tool"""
        project_id = coerce_optional_int_id(project_id, "project_id")
        user = await get_user_from_auth(ctx)

        file_data = FileCreate(
            filename=filename,
            description=description,
            data=data,
            mime_type=mime_type,
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

        file = await self.file_service.create_file(
            user_id=user.id, file_data=file_data,
        )

        return file

    async def get_file(self, file_id: int, ctx: Context) -> File:
        """Adapter for get_file tool"""
        file_id = coerce_int_id(file_id, "file_id")
        user = await get_user_from_auth(ctx)

        file = await self.file_service.get_file(
            user_id=user.id, file_id=file_id,
        )

        return file

    async def list_files(
        self,
        ctx: Context,
        project_id: int | None = None,
        mime_type: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Adapter for list_files tool"""
        project_id = coerce_optional_int_id(project_id, "project_id")
        tags = coerce_str_list(tags, "tags")
        user = await get_user_from_auth(ctx)

        result = await self.file_service.list_files(
            user_id=user.id,
            project_id=project_id,
            mime_type=mime_type,
            tags=tags,
        )

        return {"files": result, "total_count": len(result)}

    async def update_file(
        self,
        file_id: int,
        ctx: Context,
        filename: str | None = None,
        description: str | None = None,
        data: str | None = None,
        mime_type: str | None = None,
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
    ) -> File:
        """Adapter for update_file tool"""
        file_id = coerce_int_id(file_id, "file_id")
        project_id = coerce_optional_int_id(project_id, "project_id")
        user = await get_user_from_auth(ctx)

        updated_dict = filter_none_values(
            filename=filename,
            description=description,
            data=data,
            mime_type=mime_type,
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

        file_data = FileUpdate(**updated_dict)

        file = await self.file_service.update_file(
            user_id=user.id,
            file_id=file_id,
            file_data=file_data,
        )

        return file

    async def delete_file(self, file_id: int, ctx: Context) -> dict:
        """Adapter for delete_file tool"""
        file_id = coerce_int_id(file_id, "file_id")
        user = await get_user_from_auth(ctx)

        result = await self.file_service.delete_file(
            user_id=user.id, file_id=file_id,
        )

        return {"success": result, "deleted_id": file_id}


def create_file_adapters(file_service: "FileService", user_service: UserService) -> dict[str, Any]:
    """Create all file tool adapters and return as dict"""
    adapters = FileToolAdapters(file_service, user_service)
    return {
        "create_file": adapters.create_file,
        "get_file": adapters.get_file,
        "list_files": adapters.list_files,
        "update_file": adapters.update_file,
        "delete_file": adapters.delete_file,
    }
