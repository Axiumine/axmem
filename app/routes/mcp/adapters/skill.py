"""Skill tool adapters: wrap SkillService calls as registry-compatible callables."""

from typing import TYPE_CHECKING, Any

from fastmcp import Context

from app.middleware.auth import get_user_from_auth
from app.models.skill_models import Skill, SkillCreate, SkillUpdate
from app.services.user_service import UserService
from app.utils.pydantic_helper import filter_none_values

if TYPE_CHECKING:
    from app.services.skill_service import SkillService


class SkillToolAdapters:
    """Wraps skill service methods as registry-compatible callables"""

    def __init__(self, skill_service: SkillService, user_service: UserService):
        self.skill_service = skill_service
        self.user_service = user_service

    async def create_skill(
        self,
        name: str,
        description: str,
        content: str,
        ctx: Context,
        license: str | None = None,
        compatibility: str | None = None,
        allowed_tools: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        importance: int = 7,
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
    ) -> Skill:
        """Adapter for create_skill tool"""
        user = await get_user_from_auth(ctx)

        skill_data = SkillCreate(
            name=name,
            description=description,
            content=content,
            license=license,
            compatibility=compatibility,
            allowed_tools=allowed_tools,
            metadata=metadata,
            tags=tags or [],
            importance=importance,
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

        skill = await self.skill_service.create_skill(
            user_id=user.id, skill_data=skill_data,
        )

        return skill

    async def get_skill(self, skill_id: int, ctx: Context) -> Skill:
        """Adapter for get_skill tool"""
        user = await get_user_from_auth(ctx)

        skill = await self.skill_service.get_skill(
            user_id=user.id, skill_id=skill_id,
        )

        return skill

    async def list_skills(
        self,
        ctx: Context,
        project_id: int | None = None,
        tags: list[str] | None = None,
        importance_threshold: int | None = None,
    ) -> dict:
        """Adapter for list_skills tool"""
        user = await get_user_from_auth(ctx)

        result = await self.skill_service.list_skills(
            user_id=user.id,
            project_id=project_id,
            tags=tags,
            importance_threshold=importance_threshold,
        )

        return {"skills": result, "total_count": len(result)}

    async def update_skill(
        self,
        skill_id: int,
        ctx: Context,
        name: str | None = None,
        description: str | None = None,
        content: str | None = None,
        license: str | None = None,
        compatibility: str | None = None,
        allowed_tools: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        importance: int | None = None,
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
    ) -> Skill:
        """Adapter for update_skill tool"""
        user = await get_user_from_auth(ctx)

        updated_dict = filter_none_values(
            name=name,
            description=description,
            content=content,
            license=license,
            compatibility=compatibility,
            allowed_tools=allowed_tools,
            metadata=metadata,
            tags=tags,
            importance=importance,
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

        skill_data = SkillUpdate(**updated_dict)

        skill = await self.skill_service.update_skill(
            user_id=user.id,
            skill_id=skill_id,
            skill_data=skill_data,
        )

        return skill

    async def delete_skill(self, skill_id: int, ctx: Context) -> dict:
        """Adapter for delete_skill tool"""
        user = await get_user_from_auth(ctx)

        result = await self.skill_service.delete_skill(
            user_id=user.id, skill_id=skill_id,
        )

        return {"success": result, "deleted_id": skill_id}

    async def search_skills(
        self,
        query: str,
        ctx: Context,
        k: int = 5,
        project_id: int | None = None,
    ) -> dict:
        """Adapter for search_skills tool"""
        user = await get_user_from_auth(ctx)

        result = await self.skill_service.search_skills(
            user_id=user.id,
            query=query,
            k=k,
            project_id=project_id,
        )

        return {
            "skills": result,
            "total_count": len(result),
            "search_query": query,
        }

    async def import_skill(
        self,
        skill_md_content: str,
        ctx: Context,
        project_id: int | None = None,
        importance: int = 7,
    ) -> Skill:
        """Adapter for import_skill tool"""
        user = await get_user_from_auth(ctx)

        skill = await self.skill_service.import_skill(
            user_id=user.id,
            skill_md_content=skill_md_content,
            project_id=project_id,
            importance=importance,
        )

        return skill

    async def export_skill(self, skill_id: int, ctx: Context) -> str:
        """Adapter for export_skill tool"""
        user = await get_user_from_auth(ctx)

        result = await self.skill_service.export_skill(
            user_id=user.id, skill_id=skill_id,
        )

        return result

    async def link_skill_to_memory(
        self, skill_id: int, memory_id: int, ctx: Context,
    ) -> dict:
        """Adapter for link_skill_to_memory tool"""
        user = await get_user_from_auth(ctx)

        result = await self.skill_service.link_skill_to_memory(
            user_id=user.id, skill_id=skill_id, memory_id=memory_id,
        )

        return result

    async def unlink_skill_from_memory(
        self, skill_id: int, memory_id: int, ctx: Context,
    ) -> dict:
        """Adapter for unlink_skill_from_memory tool"""
        user = await get_user_from_auth(ctx)

        result = await self.skill_service.unlink_skill_from_memory(
            user_id=user.id, skill_id=skill_id, memory_id=memory_id,
        )

        return result

    async def link_skill_to_file(
        self, skill_id: int, file_id: int, ctx: Context,
    ) -> dict:
        """Adapter for link_skill_to_file tool"""
        user = await get_user_from_auth(ctx)

        result = await self.skill_service.link_skill_to_file(
            user_id=user.id, skill_id=skill_id, file_id=file_id,
        )

        return result

    async def unlink_skill_from_file(
        self, skill_id: int, file_id: int, ctx: Context,
    ) -> dict:
        """Adapter for unlink_skill_from_file tool"""
        user = await get_user_from_auth(ctx)

        result = await self.skill_service.unlink_skill_from_file(
            user_id=user.id, skill_id=skill_id, file_id=file_id,
        )

        return result

    async def link_skill_to_code_artifact(
        self, skill_id: int, code_artifact_id: int, ctx: Context,
    ) -> dict:
        """Adapter for link_skill_to_code_artifact tool"""
        user = await get_user_from_auth(ctx)

        result = await self.skill_service.link_skill_to_code_artifact(
            user_id=user.id, skill_id=skill_id, code_artifact_id=code_artifact_id,
        )

        return result

    async def unlink_skill_from_code_artifact(
        self, skill_id: int, code_artifact_id: int, ctx: Context,
    ) -> dict:
        """Adapter for unlink_skill_from_code_artifact tool"""
        user = await get_user_from_auth(ctx)

        result = await self.skill_service.unlink_skill_from_code_artifact(
            user_id=user.id, skill_id=skill_id, code_artifact_id=code_artifact_id,
        )

        return result

    async def link_skill_to_document(
        self, skill_id: int, document_id: int, ctx: Context,
    ) -> dict:
        """Adapter for link_skill_to_document tool"""
        user = await get_user_from_auth(ctx)

        result = await self.skill_service.link_skill_to_document(
            user_id=user.id, skill_id=skill_id, document_id=document_id,
        )

        return result

    async def unlink_skill_from_document(
        self, skill_id: int, document_id: int, ctx: Context,
    ) -> dict:
        """Adapter for unlink_skill_from_document tool"""
        user = await get_user_from_auth(ctx)

        result = await self.skill_service.unlink_skill_from_document(
            user_id=user.id, skill_id=skill_id, document_id=document_id,
        )

        return result

    async def get_skill_links(self, skill_id: int, ctx: Context) -> dict:
        """Adapter for get_skill_links tool"""
        user = await get_user_from_auth(ctx)

        links = await self.skill_service.get_skill_links(
            user_id=user.id, skill_id=skill_id,
        )

        return {
            "memory_ids": links.memory_ids,
            "file_ids": links.file_ids,
            "code_artifact_ids": links.code_artifact_ids,
            "document_ids": links.document_ids,
            "total_count": (
                len(links.memory_ids) + len(links.file_ids)
                + len(links.code_artifact_ids) + len(links.document_ids)
            ),
        }


def create_skill_adapters(
    skill_service: SkillService, user_service: UserService,
) -> dict[str, Any]:
    """Create all skill tool adapters and return as dict"""
    adapters = SkillToolAdapters(skill_service, user_service)
    return {
        "create_skill": adapters.create_skill,
        "get_skill": adapters.get_skill,
        "list_skills": adapters.list_skills,
        "update_skill": adapters.update_skill,
        "delete_skill": adapters.delete_skill,
        "search_skills": adapters.search_skills,
        "import_skill": adapters.import_skill,
        "export_skill": adapters.export_skill,
        "link_skill_to_memory": adapters.link_skill_to_memory,
        "unlink_skill_from_memory": adapters.unlink_skill_from_memory,
        "link_skill_to_file": adapters.link_skill_to_file,
        "unlink_skill_from_file": adapters.unlink_skill_from_file,
        "link_skill_to_code_artifact": adapters.link_skill_to_code_artifact,
        "unlink_skill_from_code_artifact": adapters.unlink_skill_from_code_artifact,
        "link_skill_to_document": adapters.link_skill_to_document,
        "unlink_skill_from_document": adapters.unlink_skill_from_document,
        "get_skill_links": adapters.get_skill_links,
    }
