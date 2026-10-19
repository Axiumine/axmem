"""Entity tool adapters: wrap EntityService calls as registry-compatible callables."""

from typing import Any

from fastmcp import Context

from app.middleware.auth import get_user_from_auth
from app.models.entity_models import (
    Entity,
    EntityCreate,
    EntityRelationship,
    EntityRelationshipCreate,
    EntityRelationshipUpdate,
    EntityType,
    EntityUpdate,
)
from app.routes.mcp.adapters.arguments import (
    coerce_int_id,
    coerce_optional_int_ids,
    coerce_str_list,
)
from app.services.entity_service import EntityService
from app.services.user_service import UserService
from app.utils.pydantic_helper import filter_none_values


class EntityToolAdapters:
    """Wraps entity service methods as registry-compatible callables"""

    def __init__(self, entity_service: EntityService, user_service: UserService):
        self.entity_service = entity_service
        self.user_service = user_service

    async def create_entity(
        self,
        name: str,
        entity_type: str,
        ctx: Context,
        custom_type: str | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        aka: list[str] | None = None,
        project_ids: list[int] | None = None,
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
    ) -> Entity:
        """Adapter for create_entity tool"""
        project_ids = coerce_optional_int_ids(project_ids, "project_ids")
        user = await get_user_from_auth(ctx)

        # Use filter_none_values to avoid passing None for fields with defaults
        entity_dict = filter_none_values(
            name=name,
            entity_type=entity_type,
            custom_type=custom_type,
            notes=notes,
            tags=tags,
            aka=aka,
            project_ids=project_ids,
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
        entity_data = EntityCreate(**entity_dict)

        entity = await self.entity_service.create_entity(
            user_id=user.id, entity_data=entity_data,
        )

        return entity

    async def get_entity(self, entity_id: int, ctx: Context) -> Entity:
        """Adapter for get_entity tool"""
        entity_id = coerce_int_id(entity_id, "entity_id")
        user = await get_user_from_auth(ctx)

        entity = await self.entity_service.get_entity(
            user_id=user.id, entity_id=entity_id,
        )

        return entity

    async def list_entities(
        self,
        ctx: Context,
        project_ids: list[int] | None = None,
        entity_type: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Adapter for list_entities tool"""
        project_ids = coerce_optional_int_ids(project_ids, "project_ids")
        tags = coerce_str_list(tags, "tags")
        user = await get_user_from_auth(ctx)

        # Convert string to enum if provided
        entity_type_enum = EntityType(entity_type) if entity_type else None

        # MCP adapter returns all entities (no pagination params exposed)
        entities, total = await self.entity_service.list_entities(
            user_id=user.id,
            project_ids=project_ids,
            entity_type=entity_type_enum,
            tags=tags,
            limit=10000,  # High limit to return all entities for MCP
        )

        return {"entities": entities, "total_count": total}

    async def search_entities(
        self,
        query: str,
        ctx: Context,
        entity_type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> dict:
        """Adapter for search_entities tool"""
        tags = coerce_str_list(tags, "tags")
        limit = 20 if limit is None else coerce_int_id(limit, "limit")
        limit = max(1, min(limit, 100))
        user = await get_user_from_auth(ctx)

        # Convert string to enum if provided
        entity_type_enum = EntityType(entity_type) if entity_type else None

        result = await self.entity_service.search_entities(
            user_id=user.id,
            search_query=query,
            entity_type=entity_type_enum,
            tags=tags,
            limit=limit,
        )

        return {
            "entities": result,
            "total_count": len(result),
            "search_query": query,
            "filters": {"entity_type": entity_type, "tags": tags, "limit": limit},
        }

    async def update_entity(
        self,
        entity_id: int,
        ctx: Context,
        name: str | None = None,
        entity_type: str | None = None,
        custom_type: str | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        aka: list[str] | None = None,
        project_ids: list[int] | None = None,
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
    ) -> Entity:
        """Adapter for update_entity tool"""
        entity_id = coerce_int_id(entity_id, "entity_id")
        project_ids = coerce_optional_int_ids(project_ids, "project_ids")
        user = await get_user_from_auth(ctx)

        updated_dict = filter_none_values(
            name=name,
            entity_type=entity_type,
            custom_type=custom_type,
            notes=notes,
            tags=tags,
            aka=aka,
            project_ids=project_ids,
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

        entity_data = EntityUpdate(**updated_dict)

        entity = await self.entity_service.update_entity(
            user_id=user.id,
            entity_id=entity_id,
            entity_data=entity_data,
        )

        return entity

    async def delete_entity(self, entity_id: int, ctx: Context) -> dict:
        """Adapter for delete_entity tool"""
        entity_id = coerce_int_id(entity_id, "entity_id")
        user = await get_user_from_auth(ctx)

        result = await self.entity_service.delete_entity(
            user_id=user.id, entity_id=entity_id,
        )

        return {"success": result, "deleted_id": entity_id}

    async def link_entity_to_memory(
        self, entity_id: int, memory_id: int, ctx: Context,
    ) -> dict:
        """Adapter for link_entity_to_memory tool"""
        entity_id = coerce_int_id(entity_id, "entity_id")
        memory_id = coerce_int_id(memory_id, "memory_id")
        user = await get_user_from_auth(ctx)

        result = await self.entity_service.link_entity_to_memory(
            user_id=user.id, entity_id=entity_id, memory_id=memory_id,
        )

        return {"success": result}

    async def unlink_entity_from_memory(
        self, entity_id: int, memory_id: int, ctx: Context,
    ) -> dict:
        """Adapter for unlink_entity_from_memory tool"""
        entity_id = coerce_int_id(entity_id, "entity_id")
        memory_id = coerce_int_id(memory_id, "memory_id")
        user = await get_user_from_auth(ctx)

        result = await self.entity_service.unlink_entity_from_memory(
            user_id=user.id, entity_id=entity_id, memory_id=memory_id,
        )

        return {"success": result}

    async def link_entity_to_project(
        self, entity_id: int, project_id: int, ctx: Context,
    ) -> dict:
        """Adapter for link_entity_to_project tool"""
        entity_id = coerce_int_id(entity_id, "entity_id")
        project_id = coerce_int_id(project_id, "project_id")
        user = await get_user_from_auth(ctx)

        result = await self.entity_service.link_entity_to_project(
            user_id=user.id, entity_id=entity_id, project_id=project_id,
        )

        return {"success": result}

    async def unlink_entity_from_project(
        self, entity_id: int, project_id: int, ctx: Context,
    ) -> dict:
        """Adapter for unlink_entity_from_project tool"""
        entity_id = coerce_int_id(entity_id, "entity_id")
        project_id = coerce_int_id(project_id, "project_id")
        user = await get_user_from_auth(ctx)

        result = await self.entity_service.unlink_entity_from_project(
            user_id=user.id, entity_id=entity_id, project_id=project_id,
        )

        return {"success": result}

    async def create_entity_relationship(
        self,
        source_entity_id: int,
        target_entity_id: int,
        relationship_type: str,
        ctx: Context,
        strength: float | None = None,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
        # Provenance (no confidence - already has its own parameter)
        source_repo: str | None = None,
        source_files: list[str] | None = None,
        source_url: str | None = None,
        encoding_agent: str | None = None,
        encoding_version: str | None = None,
        agent_id: str | None = None,
        agent_version: str | None = None,
        agent_model: str | None = None,
    ) -> EntityRelationship:
        """Adapter for create_entity_relationship tool"""
        source_entity_id = coerce_int_id(source_entity_id, "source_entity_id")
        target_entity_id = coerce_int_id(target_entity_id, "target_entity_id")
        user = await get_user_from_auth(ctx)

        relationship_data = EntityRelationshipCreate(
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            relationship_type=relationship_type,
            strength=strength,
            confidence=confidence,
            metadata=metadata,
            source_repo=source_repo,
            source_files=source_files,
            source_url=source_url,
            encoding_agent=encoding_agent,
            encoding_version=encoding_version,
            agent_id=agent_id,
            agent_version=agent_version,
            agent_model=agent_model,
        )

        relationship = await self.entity_service.create_entity_relationship(
            user_id=user.id, relationship_data=relationship_data,
        )

        return relationship

    async def get_entity_relationships(
        self,
        entity_id: int,
        ctx: Context,
        direction: str | None = None,
        relationship_type: str | None = None,
    ) -> dict:
        """Adapter for get_entity_relationships tool"""
        entity_id = coerce_int_id(entity_id, "entity_id")
        user = await get_user_from_auth(ctx)

        result = await self.entity_service.get_entity_relationships(
            user_id=user.id,
            entity_id=entity_id,
            direction=direction,
            relationship_type=relationship_type,
        )

        return {"relationships": result}

    async def update_entity_relationship(
        self,
        relationship_id: int,
        ctx: Context,
        relationship_type: str | None = None,
        strength: float | None = None,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
        # Provenance (no confidence - already has its own parameter)
        source_repo: str | None = None,
        source_files: list[str] | None = None,
        source_url: str | None = None,
        encoding_agent: str | None = None,
        encoding_version: str | None = None,
        agent_id: str | None = None,
        agent_version: str | None = None,
        agent_model: str | None = None,
    ) -> EntityRelationship:
        """Adapter for update_entity_relationship tool"""
        relationship_id = coerce_int_id(relationship_id, "relationship_id")
        user = await get_user_from_auth(ctx)

        updated_dict = filter_none_values(
            relationship_type=relationship_type,
            strength=strength,
            confidence=confidence,
            metadata=metadata,
            source_repo=source_repo,
            source_files=source_files,
            source_url=source_url,
            encoding_agent=encoding_agent,
            encoding_version=encoding_version,
            agent_id=agent_id,
            agent_version=agent_version,
            agent_model=agent_model,
        )

        relationship_data = EntityRelationshipUpdate(**updated_dict)

        relationship = await self.entity_service.update_entity_relationship(
            user_id=user.id,
            relationship_id=relationship_id,
            relationship_data=relationship_data,
        )

        return relationship

    async def delete_entity_relationship(
        self, relationship_id: int, ctx: Context,
    ) -> dict:
        """Adapter for delete_entity_relationship tool"""
        relationship_id = coerce_int_id(relationship_id, "relationship_id")
        user = await get_user_from_auth(ctx)

        result = await self.entity_service.delete_entity_relationship(
            user_id=user.id, relationship_id=relationship_id,
        )

        return {"success": result, "deleted_id": relationship_id}

    async def get_entity_memories(self, entity_id: int, ctx: Context) -> dict:
        """Adapter for get_entity_memories tool"""
        entity_id = coerce_int_id(entity_id, "entity_id")
        user = await get_user_from_auth(ctx)

        memory_ids, count, memories = await self.entity_service.get_entity_memories(
            user_id=user.id, entity_id=entity_id,
        )

        return {
            "memory_ids": memory_ids,
            "count": count,
            "memories": [{"id": mid, "title": title} for mid, title in memories],
        }

    async def get_memory_entities(self, memory_id: int, ctx: Context) -> dict:
        """Adapter for get_memory_entities tool"""
        memory_id = coerce_int_id(memory_id, "memory_id")
        user = await get_user_from_auth(ctx)

        entity_ids, count, entities = await self.entity_service.get_memory_entities(
            user_id=user.id, memory_id=memory_id,
        )

        return {
            "entity_ids": entity_ids,
            "count": count,
            "entities": [
                {"id": entity_id, "name": name, "entity_type": entity_type}
                for entity_id, name, entity_type in entities
            ],
        }


def create_entity_adapters(
    entity_service: EntityService, user_service: UserService,
) -> dict[str, Any]:
    """Create all entity tool adapters and return as dict"""
    adapters = EntityToolAdapters(entity_service, user_service)
    return {
        "create_entity": adapters.create_entity,
        "get_entity": adapters.get_entity,
        "list_entities": adapters.list_entities,
        "search_entities": adapters.search_entities,
        "update_entity": adapters.update_entity,
        "delete_entity": adapters.delete_entity,
        "link_entity_to_memory": adapters.link_entity_to_memory,
        "unlink_entity_from_memory": adapters.unlink_entity_from_memory,
        "link_entity_to_project": adapters.link_entity_to_project,
        "unlink_entity_from_project": adapters.unlink_entity_from_project,
        "create_entity_relationship": adapters.create_entity_relationship,
        "get_entity_relationships": adapters.get_entity_relationships,
        "update_entity_relationship": adapters.update_entity_relationship,
        "delete_entity_relationship": adapters.delete_entity_relationship,
        "get_entity_memories": adapters.get_entity_memories,
        "get_memory_entities": adapters.get_memory_entities,
    }
