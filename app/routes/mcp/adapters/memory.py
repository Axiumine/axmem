"""Memory tool adapters: wrap MemoryService calls as registry-compatible callables."""

from typing import Any, cast

from fastmcp import Context
from fastmcp.exceptions import ToolError

from app.config.logging_config import logging
from app.middleware.auth import get_user_from_auth
from app.models.memory_models import (
    Memory,
    MemoryCreate,
    MemoryCreateResponse,
    MemoryQueryRequest,
    MemoryQueryResult,
    MemoryUpdate,
)
from app.routes.mcp.adapters.arguments import (
    clamp_int,
    clamp_optional_int,
    coerce_bool,
    coerce_int,
    coerce_optional_int_id,
    coerce_optional_int_ids,
    coerce_str_list,
    reject_unknown_args,
)
from app.routes.mcp.adapters.memory_arguments import (
    LINK_SPEC,
    UNLINK_SPEC,
    check_importance_min,
    parse_created_since,
    resolve_link_arguments,
    resolve_memory_id,
)
from app.routes.mcp.pagination import clamp_list_pagination
from app.services.memory_service import MemoryService
from app.services.re_embedding_service import ReEmbeddingService
from app.services.user_service import UserService
from app.utils.pydantic_helper import filter_none_values

logger = logging.getLogger(__name__)


class MemoryToolAdapters:
    """Wraps memory service methods as registry-compatible callables"""

    def __init__(
        self, memory_service: MemoryService, user_service: UserService,
        re_embedding_service: ReEmbeddingService,
    ):
        self.memory_service = memory_service
        self.user_service = user_service
        self.re_embedding_service = re_embedding_service

    async def create_memory(
        self,
        title: str,
        content: str,
        context: str,
        keywords: list[str],
        tags: list[str],
        importance: int,
        ctx: Context,
        project_ids: list[int] | None = None,
        code_artifact_ids: list[int] | None = None,
        document_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
        # Provenance tracking fields
        source_repo: str | None = None,
        source_files: list[str] | None = None,
        source_url: str | None = None,
        confidence: float | None = None,
        encoding_agent: str | None = None,
        encoding_version: str | None = None,
        agent_id: str | None = None,
        agent_version: str | None = None,
        agent_model: str | None = None,
    ) -> MemoryCreateResponse:
        """Adapter for create_memory tool"""
        logger.info("MCP Tool Called -> create memory", extra={"title": title})

        user = await get_user_from_auth(ctx)

        memory_data = MemoryCreate(
            title=title,
            content=content,
            context=context,
            keywords=keywords,
            tags=tags,
            importance=importance,
            project_ids=coerce_optional_int_ids(project_ids, "project_ids"),
            code_artifact_ids=coerce_optional_int_ids(code_artifact_ids, "code_artifact_ids"),
            document_ids=coerce_optional_int_ids(document_ids, "document_ids"),
            file_ids=coerce_optional_int_ids(file_ids, "file_ids"),
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

        memory, similar_memories = await self.memory_service.create_memory(
            user_id=user.id, memory_data=memory_data,
        )
        obsolete_matches = await self.memory_service.find_obsolete_matches(
            user_id=user.id,
            memory_id=memory.id,
        )

        logger.info(
            "MCP Tool Call -> create memory completed",
            extra={
                "user_id": user.id,
                "memory_id": memory.id,
                "title": memory.title,
                "linked_memory_ids": memory.linked_memory_ids,
                "similar_memories_count": len(similar_memories),
            },
        )

        return MemoryCreateResponse(
            id=memory.id,
            title=memory.title,
            linked_memory_ids=memory.linked_memory_ids,
            project_ids=memory.project_ids,
            code_artifact_ids=memory.code_artifact_ids,
            document_ids=memory.document_ids,
            file_ids=memory.file_ids,
            similar_memories=similar_memories,
            obsolete_matches=obsolete_matches,
        )

    async def query_memory(
        self,
        query: str,
        query_context: str,
        ctx: Context,
        k: int = 3,
        include_links: bool = True,
        max_links_per_primary: int = 5,
        importance_threshold: int | None = None,
        project_ids: list[int] | None = None,
        strict_project_filter: bool = False,
    ) -> MemoryQueryResult:
        """Adapter for query_memory tool"""
        logger.info(
            "MCP Tool -> query_memory",
            extra={"query": query[:50], "k": k, "include_links": include_links},
        )

        user = await get_user_from_auth(ctx)

        k = clamp_int(k, "k", 1, 20)
        importance_threshold = clamp_optional_int(
            importance_threshold, "importance_threshold", 1, 10,
        )
        project_ids = coerce_optional_int_ids(project_ids, "project_ids")

        result = await self.memory_service.query_memory(
            user_id=user.id,
            memory_query=MemoryQueryRequest(
                query=query,
                query_context=query_context,
                k=k,
                include_links=include_links,
                max_links_per_primary=max_links_per_primary,
                importance_threshold=importance_threshold,
                project_ids=project_ids,
                strict_project_filter=strict_project_filter,
            ),
        )

        logger.info(
            "MCP Tool -> query memory completed",
            extra={
                "total_memories_returned": result.total_count,
                "token_count": result.token_count,
            },
        )

        return result

    async def update_memory(
        self,
        ctx: Context,
        memory_id: int | None = None,
        id: int | None = None,
        title: str | None = None,
        content: str | None = None,
        context: str | None = None,
        keywords: list[str] | None = None,
        tags: list[str] | None = None,
        importance: int | None = None,
        project_ids: list[int] | None = None,
        code_artifact_ids: list[int] | None = None,
        document_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
        # Provenance tracking fields
        source_repo: str | None = None,
        source_files: list[str] | None = None,
        source_url: str | None = None,
        confidence: float | None = None,
        encoding_agent: str | None = None,
        encoding_version: str | None = None,
        agent_id: str | None = None,
        agent_version: str | None = None,
        agent_model: str | None = None,
        **kwargs: Any,
    ) -> Memory:
        """Adapter for update_memory tool"""
        reject_unknown_args("update_memory", kwargs)
        mid = resolve_memory_id("update_memory", memory_id, id)
        logger.info("MCP Tool -> update_memory", extra={"memory_id": mid})

        user = await get_user_from_auth(ctx)

        updated_dict = filter_none_values(
            title=title,
            content=content,
            context=context,
            keywords=keywords,
            tags=tags,
            importance=clamp_optional_int(importance, "importance", 1, 10),
            project_ids=coerce_optional_int_ids(project_ids, "project_ids"),
            code_artifact_ids=coerce_optional_int_ids(code_artifact_ids, "code_artifact_ids"),
            document_ids=coerce_optional_int_ids(document_ids, "document_ids"),
            file_ids=coerce_optional_int_ids(file_ids, "file_ids"),
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

        updated_memory = MemoryUpdate(**updated_dict)

        refreshed_memory = await self.memory_service.update_memory(
            user_id=user.id,
            memory_id=mid,
            updated_memory=updated_memory,
        )

        return cast(Memory, refreshed_memory)

    async def link_memories(
        self,
        ctx: Context,
        memory_id: int | None = None,
        related_ids: list[int] | int | None = None,
        source_id: int | None = None,
        target_id: int | None = None,
        target_ids: list[int] | int | None = None,
        related_id: int | None = None,
        memory_id_1: int | None = None,
        memory_id_2: int | None = None,
        from_id: int | None = None,
        to_id: int | None = None,
        from_memory_id: int | None = None,
        to_memory_id: int | None = None,
        memory_ids: list[int] | None = None,
        ids: list[int] | None = None,
        id: int | None = None,
        linked_ids: list[int] | int | None = None,
        related_memory_ids: list[int] | int | None = None,
        **kwargs: Any,
    ) -> dict:
        """Adapter for link_memories tool with alias resilience"""
        reject_unknown_args("link_memories", kwargs)
        src, cleaned_related_ids = resolve_link_arguments(
            LINK_SPEC,
            {
                "memory_ids": memory_ids,
                "ids": ids,
                "memory_id": memory_id,
                "source_id": source_id,
                "from_id": from_id,
                "from_memory_id": from_memory_id,
                "memory_id_1": memory_id_1,
                "id": id,
                "related_ids": related_ids,
                "target_ids": target_ids,
                "target_id": target_id,
                "related_id": related_id,
                "to_id": to_id,
                "to_memory_id": to_memory_id,
                "memory_id_2": memory_id_2,
                "linked_ids": linked_ids,
                "related_memory_ids": related_memory_ids,
            },
        )

        logger.info(
            "MCP Tool -> link_memories",
            extra={"memory_id": src, "related_ids": cleaned_related_ids},
        )

        user = await get_user_from_auth(ctx)

        links_created = await self.memory_service.link_memories(
            user_id=user.id,
            memory_id=src,
            related_ids=cleaned_related_ids,
        )

        logger.info(
            "MCP Tool - memories linked",
            extra={"memory_id": src, "memories_linked": links_created},
        )

        return {"linked_memory_ids": links_created}

    async def unlink_memories(
        self,
        ctx: Context,
        source_id: int | None = None,
        target_id: int | None = None,
        memory_id: int | None = None,
        related_id: int | None = None,
        related_ids: list[int] | int | None = None,
        target_ids: list[int] | int | None = None,
        memory_ids: list[int] | None = None,
        ids: list[int] | None = None,
        memory_id_1: int | None = None,
        memory_id_2: int | None = None,
        from_id: int | None = None,
        to_id: int | None = None,
        from_memory_id: int | None = None,
        to_memory_id: int | None = None,
        id: int | None = None,
        linked_ids: list[int] | int | None = None,
        related_memory_ids: list[int] | int | None = None,
        **kwargs: Any,
    ) -> dict:
        """Adapter for unlink_memories tool with alias resilience"""
        reject_unknown_args("unlink_memories", kwargs)
        src, cleaned_targets = resolve_link_arguments(
            UNLINK_SPEC,
            {
                "memory_ids": memory_ids,
                "ids": ids,
                "source_id": source_id,
                "memory_id": memory_id,
                "from_id": from_id,
                "from_memory_id": from_memory_id,
                "memory_id_1": memory_id_1,
                "id": id,
                "target_id": target_id,
                "related_id": related_id,
                "target_ids": target_ids,
                "related_ids": related_ids,
                "to_id": to_id,
                "to_memory_id": to_memory_id,
                "memory_id_2": memory_id_2,
                "linked_ids": linked_ids,
                "related_memory_ids": related_memory_ids,
            },
        )

        logger.info(
            "MCP Tool -> unlink_memories",
            extra={"source_id": src, "target_ids": cleaned_targets},
        )

        user = await get_user_from_auth(ctx)

        all_success = True
        for tgt in cleaned_targets:
            success = await self.memory_service.unlink_memories(
                user_id=user.id,
                memory_id=src,
                target_id=tgt,
            )
            if not success:
                all_success = False

        logger.info(
            "MCP Tool - memories unlinked",
            extra={"source_id": src, "target_ids": cleaned_targets, "success": all_success},
        )

        return {"success": all_success}

    async def get_memory(
        self,
        ctx: Context,
        memory_id: int | None = None,
        id: int | None = None,
        **kwargs: Any,
    ) -> Memory:
        """Adapter for get_memory tool"""
        reject_unknown_args("get_memory", kwargs)
        mid = resolve_memory_id("get_memory", memory_id, id)
        logger.info("MCP Tool -> get_memory", extra={"memory_id": mid})

        user = await get_user_from_auth(ctx)

        # the repository raises NotFoundError for a missing memory instead of returning None
        memory = cast(
            Memory, await self.memory_service.get_memory(user_id=user.id, memory_id=mid),
        )

        logger.info(
            "MCP Tool - successfully retrieved memory",
            extra={"memory_id": memory.id, "user_id": user.id},
        )

        return memory

    async def mark_memory_obsolete(
        self,
        ctx: Context,
        memory_id: int | None = None,
        id: int | None = None,
        *,
        reason: str,
        superseded_by: int | None = None,
        **kwargs: Any,
    ) -> dict:
        """Adapter for mark_memory_obsolete tool"""
        reject_unknown_args("mark_memory_obsolete", kwargs)
        mid = resolve_memory_id("mark_memory_obsolete", memory_id, id)
        superseded_by = coerce_optional_int_id(superseded_by, "superseded_by")
        logger.info("MCP Tool -> mark_memory_obsolete", extra={"memory_id": mid})

        user = await get_user_from_auth(ctx)

        success = await self.memory_service.mark_memory_obsolete(
            user_id=user.id,
            memory_id=mid,
            reason=reason,
            superseded_by=superseded_by,
        )

        return {"success": success}

    async def unobsolete_memory(
        self,
        ctx: Context,
        memory_id: int | None = None,
        id: int | None = None,
        **kwargs: Any,
    ) -> dict:
        """Adapter for unobsolete_memory tool"""
        reject_unknown_args("unobsolete_memory", kwargs)
        mid = resolve_memory_id("unobsolete_memory", memory_id, id)
        logger.info("MCP Tool -> unobsolete_memory", extra={"memory_id": mid})

        user = await get_user_from_auth(ctx)

        success = await self.memory_service.unobsolete_memory(
            user_id=user.id,
            memory_id=mid,
        )

        return {"success": success}

    async def get_recent_memories(
        self,
        ctx: Context,
        limit: int = 10,
        offset: int = 0,
        project_ids: list[int] | None = None,
        include_obsolete: bool = False,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        tags: list[str] | None = None,
        importance_min: int | None = None,
        created_since: str | None = None,
    ) -> dict:
        """Adapter for get_recent_memories tool"""
        user = await get_user_from_auth(ctx)
        limit, offset = clamp_list_pagination(
            coerce_int(limit, "limit"), coerce_int(offset, "offset"),
        )
        include_obsolete = (
            False if include_obsolete is None
            else coerce_bool(include_obsolete, "include_obsolete")
        )
        tags = coerce_str_list(tags, "tags")
        check_importance_min(importance_min)
        since = parse_created_since(created_since)
        project_ids = coerce_optional_int_ids(project_ids, "project_ids")

        logger.info(
            "MCP Tool -> get_recent_memories",
            extra={
                "limit": limit,
                "offset": offset,
                "project_ids": project_ids,
                "include_obsolete": include_obsolete,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "tags": tags,
                "importance_min": importance_min,
                "created_since": created_since,
            },
        )

        # Envelope the list in a dict (like the list_* tools) so an empty result keeps
        # an object root: a bare [] carries no MCP structured content and collapses to
        # null on the remote wire. total_count is already computed by the service.
        memories, total_count = await self.memory_service.list_memories(
            user_id=user.id,
            limit=limit,
            offset=offset,
            project_ids=project_ids,
            include_obsolete=include_obsolete,
            sort_by=sort_by,
            sort_order=sort_order,
            tags=tags,
            importance_min=importance_min,
            created_since=since,
        )

        return {"memories": memories, "total_count": total_count}

    async def rebuild_embeddings(
        self,
        ctx: Context,
        memory_ids: list[int] | None = None,
        project_id: int | None = None,
    ) -> dict[str, Any]:
        """Adapter for `rebuild_embeddings` MCP tool (issue #39).

        Re-runs the embedding pipeline on a *user-scoped* subset of memories
        without ever resetting global vector storage. Designed to refresh
        explicit ids, a single project, or all caller-owned memories instead
        of forcing the destructive `--re-embed` full-migration path used by
        `re_embed_all`.

        Returns a structured result so callers can act on partial success:
        `rebuilt_ids`, `skipped_ids`, `failed`.
        """
        memory_ids = coerce_optional_int_ids(memory_ids, "memory_ids")
        project_id = coerce_optional_int_id(project_id, "project_id")

        logger.info(
            "MCP Tool -> rebuild_embeddings",
            extra={
                "memory_ids": memory_ids,
                "project_id": project_id,
            },
        )

        user = await get_user_from_auth(ctx)
        service = self.re_embedding_service
        try:
            await service.validate_targeted_scope(user.id, memory_ids, project_id)
        except ValueError as exc:
            raise ToolError(f"VALIDATION_ERROR: {exc}") from exc

        result = await service.rebuild_targeted(
            user_id=user.id,
            memory_ids=memory_ids,
            project_id=project_id,
        )
        return {
            "rebuilt_ids": result.rebuilt_ids,
            "skipped_ids": result.skipped_ids,
            "failed": result.failed,
        }


def create_memory_adapters(
    memory_service: MemoryService, user_service: UserService,
    re_embedding_service: ReEmbeddingService,
) -> dict[str, Any]:
    """Create all memory tool adapters and return as dict"""
    adapters = MemoryToolAdapters(memory_service, user_service, re_embedding_service)
    return {
        "create_memory": adapters.create_memory,
        "query_memory": adapters.query_memory,
        "update_memory": adapters.update_memory,
        "link_memories": adapters.link_memories,
        "unlink_memories": adapters.unlink_memories,
        "get_memory": adapters.get_memory,
        "mark_memory_obsolete": adapters.mark_memory_obsolete,
        "unobsolete_memory": adapters.unobsolete_memory,
        "get_recent_memories": adapters.get_recent_memories,
        "rebuild_embeddings": adapters.rebuild_embeddings,
    }
