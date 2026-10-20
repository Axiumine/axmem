"""REST API endpoints for Memory operations.

Phase 1 of the Web UI foundation (Issue #3).
Provides CRUD operations for memories with pagination, filtering, and semantic search.
"""
import logging
from uuid import UUID

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.exceptions import (
    AssociationNotFoundError,
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
)
from app.models.memory_models import (
    Memory,
    MemoryCreate,
    MemoryCreateResponse,
    MemoryLinkRequest,
    MemoryListResponse,
    MemoryQueryRequest,
    MemoryUpdate,
    clean_scope,
)
from app.models.tenant_models import Tenant
from app.routes.api.access import rest_route
from app.routes.api.query_params import parse_int_param
from app.routes.api.request_utils import path_id, read_json_object
from app.routes.api.validation import validation_error_response
from app.routes.server_state import AxmemMCP

logger = logging.getLogger(__name__)

# Valid values for query parameters
VALID_SORT_BY = {"created_at", "updated_at", "importance"}
VALID_SORT_ORDER = {"asc", "desc"}


def register(mcp: AxmemMCP):
    """Register memory REST routes with FastMCP"""

    @rest_route(mcp, "GET", "/api/v1/memories")
    async def list_memories(request: Request, tenant: Tenant) -> JSONResponse:
        """List memories with pagination, sorting, and filtering.

        Query params:
            limit: Max results per page (1-100, default 20)
            offset: Skip N results (default 0)
            sort_by: Sort field - created_at, updated_at, importance (default created_at)
            sort_order: Sort direction - asc, desc (default desc)
            project: Filter by project scope (optional)
            importance_min: Minimum importance 1-10 (optional)
            tags: Comma-separated tags (optional)
            include_obsolete: Include obsolete memories (default false)
        """
        params = request.query_params

        # Parse and validate query params with strict validation
        try:
            limit = parse_int_param(params, "limit", 20)
            offset = parse_int_param(params, "offset", 0)
            importance_min = parse_int_param(params, "importance_min")
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

        # Validate limit range
        if limit < 1 or limit > 100:
            return JSONResponse(
                {"error": "limit must be between 1 and 100"},
                status_code=400,
            )

        # Validate offset is non-negative
        if offset < 0:
            return JSONResponse(
                {"error": "offset must be non-negative"},
                status_code=400,
            )

        if importance_min is not None and not 1 <= importance_min <= 10:
            return JSONResponse(
                {"error": "importance_min must be between 1 and 10"},
                status_code=400,
            )

        # Validate sort_by
        sort_by = params.get("sort_by", "created_at")
        if sort_by not in VALID_SORT_BY:
            return JSONResponse(
                {"error": f"sort_by must be one of: {', '.join(VALID_SORT_BY)}"},
                status_code=400,
            )

        # Validate sort_order
        sort_order = params.get("sort_order", "desc")
        if sort_order not in VALID_SORT_ORDER:
            return JSONResponse(
                {"error": f"sort_order must be one of: {', '.join(VALID_SORT_ORDER)}"},
                status_code=400,
            )

        # Parse tags (comma-separated)
        tags_raw = params.get("tags")
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else None

        # Parse include_obsolete
        include_obsolete = params.get("include_obsolete", "false").lower() == "true"

        try:
            project = clean_scope(params.get("project") or None)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

        memories, total = await mcp.memory_service.list_memories(
            user_id=tenant.id,
            limit=limit,
            offset=offset,
            project=project,
            include_obsolete=include_obsolete,
            sort_by=sort_by,
            sort_order=sort_order,
            tags=tags,
            importance_min=importance_min,
        )

        response = MemoryListResponse(
            memories=memories,
            total=total,
            limit=limit,
            offset=offset,
        )

        return JSONResponse(response.model_dump(mode="json"))

    @rest_route(mcp, "GET", "/api/v1/memories/{memory_id}")
    async def get_memory(request: Request, tenant: Tenant) -> JSONResponse:
        """Get a single memory by ID."""
        memory_id = path_id(request, "memory_id")

        try:
            memory = await mcp.memory_service.get_memory(
                user_id=tenant.id,
                memory_id=memory_id,
            )
        except NotFoundError:
            return JSONResponse({"error": "Memory not found"}, status_code=404)

        if not memory:
            return JSONResponse({"error": "Memory not found"}, status_code=404)

        return JSONResponse(memory.model_dump(mode="json"))

    @rest_route(mcp, "POST", "/api/v1/memories")
    async def create_memory(request: Request, tenant: Tenant) -> JSONResponse:
        """Create a new memory."""
        try:
            body = await read_json_object(request)
            memory_data = MemoryCreate(**body)
        except ValidationError as e:
            return validation_error_response(e)
        except Exception as e:
            return JSONResponse({"error": f"Invalid request body: {e!s}"}, status_code=400)

        memory, similar_memories = await mcp.memory_service.create_memory(
            user_id=tenant.id,
            memory_data=memory_data,
        )
        obsolete_matches = await mcp.memory_service.find_obsolete_matches(
            user_id=tenant.id,
            memory_id=memory.id,
        )

        response = MemoryCreateResponse(
            id=memory.id,
            title=memory.title,
            linked_memory_ids=memory.linked_memory_ids,
            project=memory.project,
            similar_memories=similar_memories,
            obsolete_matches=obsolete_matches,
        )

        return JSONResponse(response.model_dump(mode="json"), status_code=201)

    @rest_route(mcp, "PUT", "/api/v1/memories/{memory_id}")
    async def update_memory(request: Request, tenant: Tenant) -> JSONResponse:
        """Update an existing memory."""
        memory_id = path_id(request, "memory_id")

        try:
            body = await read_json_object(request)
            update_data = MemoryUpdate(**body)
        except ValidationError as e:
            return validation_error_response(e)
        except Exception as e:
            return JSONResponse({"error": f"Invalid request body: {e!s}"}, status_code=400)

        try:
            memory = await mcp.memory_service.update_memory(
                user_id=tenant.id,
                memory_id=memory_id,
                updated_memory=update_data,
            )
        except NotFoundError:
            return JSONResponse({"error": "Memory not found"}, status_code=404)
        except ConflictError as e:
            return JSONResponse({"error": str(e)}, status_code=409)

        if not memory:
            return JSONResponse({"error": "Memory not found"}, status_code=404)

        return JSONResponse(memory.model_dump(mode="json"))

    @rest_route(mcp, "DELETE", "/api/v1/memories/{memory_id}")
    async def delete_memory(request: Request, tenant: Tenant) -> JSONResponse:
        """Mark a memory as obsolete (soft delete)."""
        memory_id = path_id(request, "memory_id")

        try:
            body = await request.json()
            reason = body.get("reason", "Marked obsolete via API")
            superseded_by = body.get("superseded_by")
        except Exception:
            reason = "Marked obsolete via API"
            superseded_by = None

        if superseded_by is not None:
            try:
                superseded_by = UUID(superseded_by) if isinstance(superseded_by, str) else None
            except ValueError:
                superseded_by = None
            if superseded_by is None:
                return JSONResponse({"error": "superseded_by must be a UUID memory id"}, status_code=400)

        try:
            success = await mcp.memory_service.mark_memory_obsolete(
                user_id=tenant.id,
                memory_id=memory_id,
                reason=reason,
                superseded_by=superseded_by,
            )
        except ValueError as e:  # a reason that is not a bounded, non-blank text
            return JSONResponse({"error": str(e)}, status_code=400)
        except AssociationNotFoundError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        except NotFoundError:
            return JSONResponse({"error": "Memory not found"}, status_code=404)
        except InvalidStateTransitionError as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        except ConflictError as e:
            return JSONResponse({"error": str(e)}, status_code=409)

        if not success:
            return JSONResponse({"error": "Memory not found"}, status_code=404)

        return JSONResponse({"success": True})

    @rest_route(mcp, "POST", "/api/v1/memories/search")
    async def search_memories(request: Request, tenant: Tenant) -> JSONResponse:
        """Semantic search across memories."""
        try:
            body = await read_json_object(request)
            query_request = MemoryQueryRequest(**body)
        except ValidationError as e:
            return validation_error_response(e)
        except Exception as e:
            return JSONResponse({"error": f"Invalid request body: {e!s}"}, status_code=400)

        result = await mcp.memory_service.query_memory(
            user_id=tenant.id,
            memory_query=query_request,
        )

        return JSONResponse(result.model_dump(mode="json"))

    @rest_route(mcp, "POST", "/api/v1/memories/{memory_id}/links")
    async def link_memories(request: Request, tenant: Tenant) -> JSONResponse:
        """Link memories together (appends to existing links)."""
        memory_id = path_id(request, "memory_id")

        try:
            body = await request.json()
            related_ids = body.get("related_ids", [])
        except Exception:
            return JSONResponse({"error": "Invalid request body"}, status_code=400)

        if not related_ids:
            return JSONResponse({"error": "related_ids is required"}, status_code=400)

        try:
            link_request = MemoryLinkRequest(memory_id=memory_id, related_ids=related_ids)
        except ValidationError as e:
            return JSONResponse(
                {"error": e.errors(include_url=False, include_context=False, include_input=False)},
                status_code=400,
            )

        try:
            linked_ids = await mcp.memory_service.link_memories(
                user_id=tenant.id,
                memory_id=memory_id,
                related_ids=link_request.related_ids,
            )
        except NotFoundError:
            return JSONResponse({"error": "Memory not found"}, status_code=404)

        return JSONResponse({"linked_ids": [str(linked_id) for linked_id in linked_ids]})

    @rest_route(mcp, "GET", "/api/v1/memories/{memory_id}/links")
    async def get_memory_links(request: Request, tenant: Tenant) -> JSONResponse:
        """Get memories linked to this memory.

        Query params:
            limit: Max linked memories to return, at least 1 (default 20, capped at 100)
            include_obsolete: Include obsolete linked memories (default false)
        """
        memory_id = path_id(request, "memory_id")

        params = request.query_params
        try:
            parsed_limit = parse_int_param(params, "limit", 20)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

        limit = 20 if parsed_limit is None else parsed_limit
        if limit < 1:
            return JSONResponse({"error": "limit must be at least 1"}, status_code=400)
        limit = min(limit, 100)

        include_obsolete = params.get("include_obsolete", "false").lower() == "true"

        # Get the memory first to access linked_memory_ids
        try:
            memory = await mcp.memory_service.get_memory(
                user_id=tenant.id,
                memory_id=memory_id,
            )
        except NotFoundError:
            return JSONResponse({"error": "Memory not found"}, status_code=404)

        if not memory:
            return JSONResponse({"error": "Memory not found"}, status_code=404)

        # Fetch linked memories, skipping missing and (by default) obsolete ones
        # so that `limit` counts only the memories actually returned
        linked_memories: list[Memory] = []
        for linked_id in memory.linked_memory_ids:
            if len(linked_memories) >= limit:
                break
            try:
                linked_memory = await mcp.memory_service.get_memory(
                    user_id=tenant.id,
                    memory_id=linked_id,
                )
            except NotFoundError:
                # Skip if linked memory no longer exists
                continue
            if linked_memory and (include_obsolete or not linked_memory.is_obsolete):
                linked_memories.append(linked_memory)

        return JSONResponse({
            "memory_id": str(memory_id),
            "linked_memories": [m.model_dump(mode="json") for m in linked_memories],
        })

    @rest_route(mcp, "DELETE", "/api/v1/memories/{memory_id}/links/{target_id}")
    async def delete_memory_link(request: Request, tenant: Tenant) -> JSONResponse:
        """Remove a specific link between memories."""
        memory_id = path_id(request, "memory_id")
        target_id = path_id(request, "target_id")

        try:
            success = await mcp.memory_service.unlink_memories(
                user_id=tenant.id,
                memory_id=memory_id,
                target_id=target_id,
            )
        except NotFoundError:
            return JSONResponse({"error": "Memory not found"}, status_code=404)

        if not success:
            return JSONResponse({"error": "Link not found"}, status_code=404)

        return JSONResponse({"success": True})
