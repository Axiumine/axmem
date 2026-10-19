"""Document tool adapters: wrap DocumentService calls as registry-compatible callables."""

from typing import Any

from fastmcp import Context

from app.middleware.auth import get_user_from_auth
from app.models.document_models import Document, DocumentCreate, DocumentUpdate
from app.routes.mcp.adapters.arguments import (
    coerce_int_id,
    coerce_optional_int_id,
    coerce_str_list,
)
from app.services.document_service import DocumentService
from app.services.user_service import UserService
from app.utils.pydantic_helper import filter_none_values


class DocumentToolAdapters:
    """Wraps document service methods as registry-compatible callables"""

    def __init__(self, document_service: DocumentService, user_service: UserService):
        self.document_service = document_service
        self.user_service = user_service

    async def create_document(
        self,
        title: str,
        description: str,
        content: str,
        ctx: Context,
        document_type: str = "text",
        filename: str | None = None,
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
    ) -> Document:
        """Adapter for create_document tool"""
        project_id = coerce_optional_int_id(project_id, "project_id")
        user = await get_user_from_auth(ctx)

        document_data = DocumentCreate(
            title=title,
            description=description,
            content=content,
            document_type=document_type,
            filename=filename,
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

        document = await self.document_service.create_document(
            user_id=user.id, document_data=document_data,
        )

        return document

    async def get_document(self, document_id: int, ctx: Context) -> Document:
        """Adapter for get_document tool"""
        document_id = coerce_int_id(document_id, "document_id")
        user = await get_user_from_auth(ctx)

        document = await self.document_service.get_document(
            user_id=user.id, document_id=document_id,
        )

        return document

    async def list_documents(
        self,
        ctx: Context,
        project_id: int | None = None,
        document_type: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Adapter for list_documents tool"""
        project_id = coerce_optional_int_id(project_id, "project_id")
        tags = coerce_str_list(tags, "tags")
        user = await get_user_from_auth(ctx)

        result = await self.document_service.list_documents(
            user_id=user.id,
            project_id=project_id,
            document_type=document_type,
            tags=tags,
        )

        return {"documents": result, "total_count": len(result)}

    async def update_document(
        self,
        document_id: int,
        ctx: Context,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        document_type: str | None = None,
        filename: str | None = None,
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
    ) -> Document:
        """Adapter for update_document tool"""
        document_id = coerce_int_id(document_id, "document_id")
        project_id = coerce_optional_int_id(project_id, "project_id")
        user = await get_user_from_auth(ctx)

        updated_dict = filter_none_values(
            title=title,
            description=description,
            content=content,
            document_type=document_type,
            filename=filename,
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

        document_data = DocumentUpdate(**updated_dict)

        document = await self.document_service.update_document(
            user_id=user.id,
            document_id=document_id,
            document_data=document_data,
        )

        return document

    async def delete_document(self, document_id: int, ctx: Context) -> dict:
        """Adapter for delete_document tool"""
        document_id = coerce_int_id(document_id, "document_id")
        user = await get_user_from_auth(ctx)

        result = await self.document_service.delete_document(
            user_id=user.id, document_id=document_id,
        )

        return {"success": result, "deleted_id": document_id}


def create_document_adapters(
    document_service: DocumentService, user_service: UserService,
) -> dict[str, Any]:
    """Create all document tool adapters and return as dict"""
    adapters = DocumentToolAdapters(document_service, user_service)
    return {
        "create_document": adapters.create_document,
        "get_document": adapters.get_document,
        "list_documents": adapters.list_documents,
        "update_document": adapters.update_document,
        "delete_document": adapters.delete_document,
    }
