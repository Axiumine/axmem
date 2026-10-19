"""Tool Metadata Registry - Registration helpers for tool metadata

This module provides helper functions for registering tools with the registry,
including detailed parameter metadata for discovery and documentation.
"""
from typing import Any

from app.config.logging_config import logging
from app.models.tool_registry_models import (
    ToolCategory,
    ToolDef,
    ToolParameter,
    ToolParameterDef,
)
from app.routes.mcp.tool_adapters import (
    create_memory_adapters,
    create_user_adapters,
)
from app.routes.mcp.tool_registry import ToolRegistry
from app.services.memory_service import MemoryService
from app.services.user_service import UserService

logger = logging.getLogger(__name__)


def register_simplified_tool(
    registry: ToolRegistry,
    name: str,
    category: ToolCategory,
    description: str,
    parameters: list[ToolParameterDef],
    returns: str,
    implementation: Any,
    examples: list[str] | None = None,
    tags: list[str] | None = None,
    mutates: bool = False,
) -> None:
    """Helper to register tools with simplified parameter definitions

    Args:
        registry: ToolRegistry instance
        name: Tool name
        category: Tool category
        description: Tool description
        parameters: List of parameter dicts with simplified format
        returns: Return value description
        implementation: Async callable
        examples: Usage examples
        tags: Tags for categorization
        mutates: Whether this tool mutates state (write operation)
    """
    tool_params = [
        ToolParameter(
            name=p["name"],
            type=p["type"],
            description=p.get("description", ""),
            required=p.get("required", False),
            default=p.get("default"),
            example=p.get("example"),
        )
        for p in parameters
    ]

    registry.register(
        name=name,
        category=category,
        description=description,
        parameters=tool_params,
        returns=returns,
        implementation=implementation,
        examples=examples or [],
        tags=tags or [],
        mutates=mutates,
    )


# ============================================================================
# User Tools Metadata
# ============================================================================

def register_user_tools_metadata(
    registry: ToolRegistry,
    adapters: dict[str, Any],
):
    """Register user tool metadata and implementations"""
    tools: list[ToolDef] = [
        {
            "name": "get_current_user",
            "description": "Returns information about the current authenticated user",
            "parameters": [
                {
                    "name": "ctx",
                    "type": "Context",
                    "description": "FastMCP Context (automatically injected)",
                    "required": True,
                },
            ],
            "returns": "UserResponse with id, external_id, name, email, notes, timestamps",
            "examples": [
                'execute_forgetful_tool("get_current_user", {})',
            ],
            "tags": ["user", "authentication", "context"],
        },
        {
            "name": "update_user_notes",
            "mutates": True,
            "description": "Update the notes field for the current user",
            "parameters": [
                {
                    "name": "user_notes",
                    "type": "str",
                    "description": "The new notes content to store for the user",
                    "required": True,
                    "example": "User prefers TypeScript, uses VSCode, timezone: PST",
                },
                {
                    "name": "ctx",
                    "type": "Context",
                    "description": "FastMCP Context (automatically injected)",
                    "required": True,
                },
            ],
            "returns": "Updated UserResponse with new notes value",
            "examples": [
                'execute_forgetful_tool("update_user_notes", {"user_notes": "Prefers React over Vue"})',
            ],
            "tags": ["user", "update", "preferences"],
        },
    ]

    for tool_def in tools:
        register_simplified_tool(
            registry=registry,
            name=tool_def["name"],
            category=ToolCategory.USER,
            description=tool_def["description"],
            parameters=tool_def["parameters"],
            returns=tool_def["returns"],
            implementation=adapters[tool_def["name"]],
            examples=tool_def.get("examples", []),
            tags=tool_def.get("tags", []),
            mutates=tool_def.get("mutates", False),
        )

    logger.info(f"Registered {len(tools)} user tools")


# ============================================================================
# Memory Tools Metadata
# ============================================================================

def register_memory_tools_metadata(
    registry: ToolRegistry,
    adapters: dict[str, Any],
):
    """Register memory tool metadata and implementations"""
    tools: list[ToolDef] = [
        {
            "name": "create_memory",
            "mutates": True,
            "description": "Create atomic memory with auto-linking and lifecycle management. Stores single concepts (<400 words).",
            "parameters": [
                {
                    "name": "title",
                    "type": "str",
                    "description": "Memory title (max 200 characters)",
                    "required": True,
                    "example": "TTS preference: XTTS-v2",
                },
                {
                    "name": "content",
                    "type": "str",
                    "description": "Memory content (max 2000 characters, ~300-400 words) - single concept",
                    "required": True,
                    "example": "Selected XTTS-v2 for voice cloning - provides high quality output with low latency",
                },
                {
                    "name": "context",
                    "type": "str",
                    "description": "WHY this memory matters, HOW it relates, WHAT implications (max 500 characters)",
                    "required": True,
                    "example": "Decision made while implementing voice integration with AI agent",
                },
                {
                    "name": "keywords",
                    "type": "List[str]",
                    "description": "Search keywords for semantic matching (max 10, each max 100 characters)",
                    "required": True,
                    "example": ["tts", "voice-cloning", "xtts"],
                },
                {
                    "name": "tags",
                    "type": "List[str]",
                    "description": "Categorization tags (max 10, each max 100 characters)",
                    "required": True,
                    "example": ["decision", "preference", "audio"],
                },
                {
                    "name": "importance",
                    "type": "int",
                    "description": "Score 1-10. 9-10: Personal/foundational, 8-9: Critical solutions, 7-8: Useful patterns, 6-7: Milestones",
                    "required": True,
                    "example": 9,
                },
                {
                    "name": "ctx",
                    "type": "Context",
                    "description": "FastMCP Context (automatically injected)",
                    "required": True,
                },
                {
                    "name": "project_ids",
                    "type": "Optional[List[int]]",
                    "description": "Project IDs to link (optional)",
                    "required": False,
                    "default": None,
                    "example": [1, 3],
                },
                {
                    "name": "source_repo",
                    "type": "Optional[str]",
                    "description": "Repository/project source (e.g., 'owner/repo') for provenance tracking",
                    "required": False,
                    "default": None,
                    "example": "scottrbk/forgetful",
                },
                {
                    "name": "source_files",
                    "type": "Optional[List[str]]",
                    "description": "Files that informed this memory (list of paths) for provenance tracking",
                    "required": False,
                    "default": None,
                    "example": ["src/main.py", "tests/test.py"],
                },
                {
                    "name": "source_url",
                    "type": "Optional[str]",
                    "description": "URL to original source material for provenance tracking",
                    "required": False,
                    "default": None,
                    "example": "https://github.com/owner/repo/blob/main/README.md",
                },
                {
                    "name": "confidence",
                    "type": "Optional[float]",
                    "description": "Encoding confidence score (0.0-1.0) for provenance tracking",
                    "required": False,
                    "default": None,
                    "example": 0.85,
                },
                {
                    "name": "encoding_agent",
                    "type": "Optional[str]",
                    "description": "Agent/process that created this memory for provenance tracking",
                    "required": False,
                    "default": None,
                    "example": "claude-sonnet-4-20250514",
                },
                {
                    "name": "encoding_version",
                    "type": "Optional[str]",
                    "description": "Version of encoding process/prompt for provenance tracking",
                    "required": False,
                    "default": None,
                    "example": "0.1.0",
                },
                {
                    "name": "agent_id",
                    "type": "Optional[str]",
                    "description": "Agent identity for provenance tracking",
                    "required": False,
                    "default": None,
                    "example": "CodeAgentUltra",
                },
                {
                    "name": "agent_version",
                    "type": "Optional[str]",
                    "description": "Agent version for provenance tracking",
                    "required": False,
                    "default": None,
                    "example": "1.0",
                },
                {
                    "name": "agent_model",
                    "type": "Optional[str]",
                    "description": "LLM model used for provenance tracking",
                    "required": False,
                    "default": None,
                    "example": "claude-sonnet-4-6",
                },
            ],
            "returns": "MemoryCreateResponse with id, title, linked_memory_ids, similar_memories, obsolete_matches",
            "examples": [
                'execute_forgetful_tool("create_memory", {"title": "FastAPI auth pattern", "content": "Use JWT with httponly cookies...", "context": "Security decision", "keywords": ["auth", "jwt"], "tags": ["security"], "importance": 9})',
            ],
            "tags": ["memory", "create", "linking"],
        },
        {
            "name": "query_memory",
            "description": "Semantic search across memories to find relevant information",
            "parameters": [
                {
                    "name": "query",
                    "type": "str",
                    "description": "Natural language search query",
                    "required": True,
                    "example": "What did we decide about authentication?",
                },
                {
                    "name": "query_context",
                    "type": "str",
                    "description": "Context explaining why you're searching (improves ranking)",
                    "required": True,
                    "example": "Implementing login system for new API",
                },
                {
                    "name": "ctx",
                    "type": "Context",
                    "description": "FastMCP Context (automatically injected)",
                    "required": True,
                },
                {
                    "name": "k",
                    "type": "int",
                    "description": "Number of primary results to return (1-20), use INSTEAD of LIMIT",
                    "required": False,
                    "default": 3,
                    "example": 5,
                },
                {
                    "name": "include_links",
                    "type": "bool",
                    "description": "Whether to include linked memories for context",
                    "required": False,
                    "default": True,
                    "example": True,
                },
                {
                    "name": "max_links_per_primary",
                    "type": "int",
                    "description": "Maximum number of linked memories per primary memory",
                    "required": False,
                    "default": 5,
                    "example": 3,
                },
                {
                    "name": "importance_threshold",
                    "type": "Optional[int]",
                    "description": "Minimum importance score (1-10) to include",
                    "required": False,
                    "default": None,
                    "example": 7,
                },
                {
                    "name": "project_ids",
                    "type": "Optional[List[int]]",
                    "description": "Filter results to specific projects",
                    "required": False,
                    "default": None,
                    "example": [1, 2],
                },
                {
                    "name": "strict_project_filter",
                    "type": "bool",
                    "description": "If True, linked memories must also be in specified projects",
                    "required": False,
                    "default": False,
                    "example": False,
                },
            ],
            "returns": "MemoryQueryResult with primary_memories, linked_memories, scores, total_count, token_count, truncated flag",
            "examples": [
                'execute_forgetful_tool("query_memory", {"query": "authentication patterns", "query_context": "building API login", "k": 5})',
                'execute_forgetful_tool("query_memory", {"query": "database design", "query_context": "schema review", "importance_threshold": 8, "k": 3})',
            ],
            "tags": ["memory", "search", "semantic", "query"],
        },
        {
            "name": "update_memory",
            "mutates": True,
            "description": "Update existing memory fields using PATCH semantics (only specified fields are updated)",
            "parameters": [
                {
                    "name": "memory_id",
                    "type": "int",
                    "description": "ID of the memory to update",
                    "required": True,
                    "example": 42,
                },
                {
                    "name": "ctx",
                    "type": "Context",
                    "description": "FastMCP Context (automatically injected)",
                    "required": True,
                },
                {
                    "name": "title",
                    "type": "Optional[str]",
                    "description": "New title (optional)",
                    "required": False,
                    "default": None,
                    "example": "Updated title",
                },
                {
                    "name": "content",
                    "type": "Optional[str]",
                    "description": "New content (optional)",
                    "required": False,
                    "default": None,
                    "example": "Updated content with new information",
                },
                {
                    "name": "context",
                    "type": "Optional[str]",
                    "description": "New context (optional)",
                    "required": False,
                    "default": None,
                    "example": "Updated context explanation",
                },
                {
                    "name": "keywords",
                    "type": "Optional[List[str]]",
                    "description": "New keywords - replaces existing (optional)",
                    "required": False,
                    "default": None,
                    "example": ["new", "keywords"],
                },
                {
                    "name": "tags",
                    "type": "Optional[List[str]]",
                    "description": "New tags - replaces existing (optional)",
                    "required": False,
                    "default": None,
                    "example": ["updated", "tag"],
                },
                {
                    "name": "importance",
                    "type": "Optional[int]",
                    "description": "New importance score 1-10 (optional)",
                    "required": False,
                    "default": None,
                    "example": 8,
                },
                {
                    "name": "project_ids",
                    "type": "Optional[List[int]]",
                    "description": "New project IDs - replaces existing links (optional)",
                    "required": False,
                    "default": None,
                    "example": [1, 2],
                },
                {
                    "name": "source_repo",
                    "type": "Optional[str]",
                    "description": "New repository/project source. Unchanged if null.",
                    "required": False,
                    "default": None,
                    "example": "scottrbk/forgetful",
                },
                {
                    "name": "source_files",
                    "type": "Optional[List[str]]",
                    "description": "New source files list. Replaces existing if provided, unchanged if null.",
                    "required": False,
                    "default": None,
                    "example": ["src/main.py", "tests/test.py"],
                },
                {
                    "name": "source_url",
                    "type": "Optional[str]",
                    "description": "New URL to source material. Unchanged if null.",
                    "required": False,
                    "default": None,
                    "example": "https://github.com/owner/repo/blob/main/README.md",
                },
                {
                    "name": "confidence",
                    "type": "Optional[float]",
                    "description": "New encoding confidence score (0.0-1.0). Unchanged if null.",
                    "required": False,
                    "default": None,
                    "example": 0.85,
                },
                {
                    "name": "encoding_agent",
                    "type": "Optional[str]",
                    "description": "New agent/process identifier. Unchanged if null.",
                    "required": False,
                    "default": None,
                    "example": "claude-sonnet-4-20250514",
                },
                {
                    "name": "encoding_version",
                    "type": "Optional[str]",
                    "description": "New encoding process version. Unchanged if null.",
                    "required": False,
                    "default": None,
                    "example": "0.1.0",
                },
                {
                    "name": "agent_id",
                    "type": "Optional[str]",
                    "description": "Agent identity for provenance tracking",
                    "required": False,
                    "default": None,
                    "example": "CodeAgentUltra",
                },
                {
                    "name": "agent_version",
                    "type": "Optional[str]",
                    "description": "Agent version for provenance tracking",
                    "required": False,
                    "default": None,
                    "example": "1.0",
                },
                {
                    "name": "agent_model",
                    "type": "Optional[str]",
                    "description": "LLM model used for provenance tracking",
                    "required": False,
                    "default": None,
                    "example": "claude-sonnet-4-6",
                },
            ],
            "returns": "Full Memory object after update",
            "examples": [
                'execute_forgetful_tool("update_memory", {"memory_id": 42, "importance": 9})',
                'execute_forgetful_tool("update_memory", {"memory_id": 42, "content": "Updated content", "tags": ["revised", "important"]})',
            ],
            "tags": ["memory", "update", "patch"],
        },
        {
            "name": "link_memories",
            "mutates": True,
            "description": "Manually create bidirectional links between memories (symmetric linking). Accepts memory_id + related_ids (or aliases source_id + target_id/target_ids, memory_ids=[src, tgt])",
            "parameters": [
                {
                    "name": "memory_id",
                    "type": "int",
                    "description": "Source memory ID (aliases: source_id, from_id, id)",
                    "required": True,
                    "example": 42,
                },
                {
                    "name": "related_ids",
                    "type": "List[int]",
                    "description": "List of target memory IDs to link (or single int; aliases: target_id, target_ids, related_id)",
                    "required": True,
                    "example": [10, 15, 20],
                },
                {
                    "name": "ctx",
                    "type": "Context",
                    "description": "FastMCP Context (automatically injected)",
                    "required": True,
                },
            ],
            "returns": "List of memory IDs that were successfully linked",
            "examples": [
                'execute_forgetful_tool("link_memories", {"memory_id": 42, "related_ids": [10, 15, 20]})',
                'execute_forgetful_tool("link_memories", {"source_id": 42, "target_id": 57})',
                'execute_forgetful_tool("link_memories", {"memory_ids": [42, 57]})',
            ],
            "tags": ["memory", "linking", "relationships"],
        },
        {
            "name": "unlink_memories",
            "mutates": True,
            "description": "Remove bidirectional link(s) between memories. Accepts source_id + target_id (or aliases memory_id + related_id, target_ids, related_ids, memory_ids=[src, tgt, ...])",
            "parameters": [
                {
                    "name": "source_id",
                    "type": "int",
                    "description": "Source memory ID (aliases: memory_id, from_id, id)",
                    "required": True,
                    "example": 42,
                },
                {
                    "name": "target_id",
                    "type": "int",
                    "description": "Target memory ID(s) to unlink (aliases: target_ids, related_id, related_ids, to_id)",
                    "required": True,
                    "example": 57,
                },
                {
                    "name": "ctx",
                    "type": "Context",
                    "description": "FastMCP Context (automatically injected)",
                    "required": True,
                },
            ],
            "returns": "Dict with 'success' boolean (True if all requested links were removed, False if any link didn't exist)",
            "examples": [
                'execute_forgetful_tool("unlink_memories", {"source_id": 42, "target_id": 57})',
                'execute_forgetful_tool("unlink_memories", {"memory_id": 42, "related_id": 57})',
                'execute_forgetful_tool("unlink_memories", {"memory_id": 42, "related_ids": [57, 58]})',
                'execute_forgetful_tool("unlink_memories", {"memory_ids": [42, 57, 58]})',
            ],
            "tags": ["memory", "unlink", "graph", "linking"],
        },
        {
            "name": "get_memory",
            "description": "Retrieve complete memory details by ID",
            "parameters": [
                {
                    "name": "memory_id",
                    "type": "int",
                    "description": "ID of the memory to retrieve",
                    "required": True,
                    "example": 42,
                },
                {
                    "name": "ctx",
                    "type": "Context",
                    "description": "FastMCP Context (automatically injected)",
                    "required": True,
                },
            ],
            "returns": "Complete Memory object with all fields",
            "examples": [
                'execute_forgetful_tool("get_memory", {"memory_id": 42})',
            ],
            "tags": ["memory", "retrieve", "read"],
        },
        {
            "name": "mark_memory_obsolete",
            "mutates": True,
            "description": (
                "Mark a memory as obsolete (soft delete with audit trail). "
                "Restore with unobsolete_memory."
            ),
            "parameters": [
                {
                    "name": "memory_id",
                    "type": "int",
                    "description": "ID of the memory to mark as obsolete",
                    "required": True,
                    "example": 42,
                },
                {
                    "name": "reason",
                    "type": "str",
                    "description": "Explanation for why this memory is obsolete",
                    "required": True,
                    "example": "Superseded by newer decision in memory #100",
                },
                {
                    "name": "ctx",
                    "type": "Context",
                    "description": "FastMCP Context (automatically injected)",
                    "required": True,
                },
                {
                    "name": "superseded_by",
                    "type": "Optional[int]",
                    "description": "Optional ID of the replacement memory",
                    "required": False,
                    "default": None,
                    "example": 100,
                },
            ],
            "returns": "Boolean indicating success",
            "examples": [
                'execute_forgetful_tool("mark_memory_obsolete", {"memory_id": 42, "reason": "Outdated approach", "superseded_by": 100})',
            ],
            "tags": ["memory", "delete", "obsolete", "lifecycle"],
        },
        {
            "name": "unobsolete_memory",
            "mutates": True,
            "description": "Restore a memory previously marked obsolete (undo soft delete)",
            "parameters": [
                {
                    "name": "memory_id",
                    "type": "int",
                    "description": "ID of the obsolete memory to restore",
                    "required": True,
                    "example": 42,
                },
                {
                    "name": "ctx",
                    "type": "Context",
                    "description": "FastMCP Context (automatically injected)",
                    "required": True,
                },
            ],
            "returns": "Boolean indicating success",
            "examples": [
                'execute_forgetful_tool("unobsolete_memory", {"memory_id": 42})',
            ],
            "tags": ["memory", "obsolete", "lifecycle"],
        },
        {
            "name": "get_recent_memories",
            "description": "Retrieve most recent memories sorted by creation timestamp (newest first by default)",
            "parameters": [
                {
                    "name": "ctx",
                    "type": "Context",
                    "description": "FastMCP Context (automatically injected)",
                    "required": True,
                },
                {
                    "name": "limit",
                    "type": "int",
                    "description": "Maximum number of memories to return (1-100)",
                    "required": False,
                    "default": 10,
                    "example": 10,
                },
                {
                    "name": "offset",
                    "type": "int",
                    "description": "Number of memories to skip for pagination",
                    "required": False,
                    "default": 0,
                    "example": 0,
                },
                {
                    "name": "project_ids",
                    "type": "Optional[List[int]]",
                    "description": "Optional filter to specific projects",
                    "required": False,
                    "default": None,
                    "example": [1, 3],
                },
                {
                    "name": "include_obsolete",
                    "type": "bool",
                    "description": "Include obsolete memories when true",
                    "required": False,
                    "default": False,
                    "example": False,
                },
                {
                    "name": "sort_by",
                    "type": "str",
                    "description": "Sort field: created_at, updated_at, or importance",
                    "required": False,
                    "default": "created_at",
                    "example": "created_at",
                },
                {
                    "name": "sort_order",
                    "type": "str",
                    "description": "Sort direction: asc or desc",
                    "required": False,
                    "default": "desc",
                    "example": "desc",
                },
                {
                    "name": "tags",
                    "type": "Optional[List[str]]",
                    "description": "Optional tag filter (OR semantics when multiple tags)",
                    "required": False,
                    "default": None,
                    "example": ["ai-champion"],
                },
                {
                    "name": "importance_min",
                    "type": "Optional[int]",
                    "description": "Minimum importance (1-10, inclusive)",
                    "required": False,
                    "default": None,
                    "example": 8,
                },
                {
                    "name": "created_since",
                    "type": "Optional[str]",
                    "description": "Inclusive ISO 8601 creation timestamp with timezone",
                    "required": False,
                    "default": None,
                    "example": "2026-08-01T00:00:00Z",
                },
            ],
            "returns": "Dictionary with memories list (sorted per sort_by/sort_order) and total_count",
            "examples": [
                'execute_forgetful_tool("get_recent_memories", {"limit": 5})',
                'execute_forgetful_tool("get_recent_memories", {"limit": 10, "project_ids": [1, 2]})',
                'execute_forgetful_tool("get_recent_memories", {"tags": ["ai-champion"], "limit": 20})',
                (
                    'execute_forgetful_tool("get_recent_memories", '
                    '{"importance_min": 8, "created_since": "2026-08-01T00:00:00Z", '
                    '"sort_by": "importance"})'
                ),
            ],
            "tags": ["memory", "query", "recency", "timeline"],
        },
        {
            "name": "rebuild_embeddings",
            "mutates": True,
            "description": (
                "Rebuild embeddings for a user-scoped subset of memories without resetting "
                "global vector storage. Targets explicit memory ids, a single project, or "
                "all caller-owned memories."
            ),
            "parameters": [
                {
                    "name": "ctx",
                    "type": "Context",
                    "description": "FastMCP Context (automatically injected)",
                    "required": True,
                },
                {
                    "name": "memory_ids",
                    "type": "Optional[List[int]]",
                    "description": "Explicit memory ids owned by the caller; leave null to use project_id or global user scope",
                    "required": False,
                    "default": None,
                    "example": [123, 124],
                },
                {
                    "name": "project_id",
                    "type": "Optional[int]",
                    "description": "Restrict the rebuild to memories of a single project owned by the caller",
                    "required": False,
                    "default": None,
                    "example": 1,
                },
            ],
            "returns": (
                "Dict with rebuilt_ids (List[int]), skipped_ids (List[int]; not owned, or edited "
                "while the rebuild ran so a newer embedding was kept), "
                "failed (List[Dict[memory_id, reason]]; reason is a code such as embedding_failed, "
                "persistence_failed or not_found_or_not_owned). "
                "An unknown or foreign project_id is rejected with VALIDATION_ERROR"
            ),
            "examples": [
                'execute_forgetful_tool("rebuild_embeddings", {"memory_ids": [123, 124]})',
                'execute_forgetful_tool("rebuild_embeddings", {"project_id": 1})',
                'execute_forgetful_tool("rebuild_embeddings", {})',
            ],
            "tags": ["memory", "embeddings", "admin", "repair"],
        },
    ]

    for tool_def in tools:
        register_simplified_tool(
            registry=registry,
            name=tool_def["name"],
            category=ToolCategory.MEMORY,
            description=tool_def["description"],
            parameters=tool_def["parameters"],
            returns=tool_def["returns"],
            implementation=adapters[tool_def["name"]],
            examples=tool_def.get("examples", []),
            tags=tool_def.get("tags", []),
            mutates=tool_def.get("mutates", False),
        )

    logger.info(f"Registered {len(tools)} memory tools")


# ============================================================================
# Master Registration Function
# ============================================================================

def register_all_tools_metadata(
    registry: ToolRegistry,
    user_service: UserService,
    memory_service: MemoryService,
    project_service,
    entity_service,
    re_embedding_service,
):
    """Register all tool metadata and implementations

    Args:
        registry: ToolRegistry instance to register tools with
        user_service: UserService instance
        memory_service: MemoryService instance
        project_service: ProjectService instance
        entity_service: EntityService instance
        re_embedding_service: ReEmbeddingService instance created at startup
    """
    logger.info("Starting tool registration")

    # Import adapter factory functions
    from app.routes.mcp.tool_adapters import (
        create_entity_adapters,
        create_project_adapters,
    )

    # Create adapters for all categories
    user_adapters = create_user_adapters(user_service)
    memory_adapters = create_memory_adapters(memory_service, user_service, re_embedding_service)
    project_adapters = create_project_adapters(project_service, user_service)
    entity_adapters = create_entity_adapters(entity_service, user_service)

    # Register tools by category
    register_user_tools_metadata(registry, user_adapters)
    register_memory_tools_metadata(registry, memory_adapters)
    register_project_tools_metadata(registry, project_adapters)
    register_entity_tools_metadata(registry, entity_adapters)

    # Log summary
    categories = registry.list_categories()
    total = sum(categories.values())
    logger.info(f"Tool registration complete: {total} tools across {len(categories)} categories")
    logger.info(f"Categories: {categories}")


# ============================================================================
# Project Tools Metadata
# ============================================================================

def register_project_tools_metadata(
    registry: ToolRegistry,
    adapters: dict[str, Any],
):
    """Register project tool metadata and implementations"""
    tools: list[ToolDef] = [
        {
            "name": "create_project",
            "mutates": True,
            "description": "Create new project for organizing memories by context",
            "parameters": [
                {"name": "name", "type": "str", "description": "Project name (max 500 chars)", "required": True, "example": "forgetful"},
                {"name": "description", "type": "str", "description": "Purpose/scope overview (max ~5000 chars)", "required": True, "example": "MIT-licensed memory service"},
                {"name": "project_type", "type": "ProjectType", "description": "Project category (personal, work, learning, development, infrastructure, template, product, marketing, finance, documentation, development-environment, third-party-library, open-source)", "required": True, "example": "development"},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
                {"name": "status", "type": "ProjectStatus", "description": "Project lifecycle status (active, archived, completed)", "required": False, "default": "active", "example": "active"},
                {
                    "name": "repo_name",
                    "type": "Optional[str]",
                    "description": (
                        "Git repository identifier or HTTPS/SSH address (max 255 chars)"
                    ),
                    "required": False,
                    "default": None,
                    "example": "scottrbk/forgetful",
                },
                {"name": "last_encoding_point", "type": "Optional[str]", "description": "Opaque caller-managed encoding checkpoint (e.g. a Git commit SHA). Never advanced by the server.", "required": False, "default": None, "example": "5d41402abc4b2a76b9719d911017c592"},
                {"name": "notes", "type": "Optional[str]", "description": "Workflow notes, setup instructions (max ~4000 chars)", "required": False, "default": None, "example": "Uses uv for dependency management"},
                {"name": "source_repo", "type": "Optional[str]", "description": "Repository/project source for provenance tracking", "required": False, "default": None, "example": "owner/repo"},
                {"name": "source_files", "type": "Optional[List[str]]", "description": "Files that informed this for provenance tracking", "required": False, "default": None, "example": ["src/main.py"]},
                {"name": "source_url", "type": "Optional[str]", "description": "URL to source material for provenance tracking", "required": False, "default": None, "example": "https://example.com"},
                {"name": "confidence", "type": "Optional[float]", "description": "Encoding confidence score (0.0-1.0) for provenance tracking", "required": False, "default": None, "example": 0.85},
                {"name": "encoding_agent", "type": "Optional[str]", "description": "Software running the agent for provenance tracking", "required": False, "default": None, "example": "OpenCode"},
                {"name": "encoding_version", "type": "Optional[str]", "description": "Version of encoding software for provenance tracking", "required": False, "default": None, "example": "1.0.0"},
                {"name": "agent_id", "type": "Optional[str]", "description": "Agent identity for provenance tracking", "required": False, "default": None, "example": "CodeAgentUltra"},
                {"name": "agent_version", "type": "Optional[str]", "description": "Agent version for provenance tracking", "required": False, "default": None, "example": "1.0"},
                {"name": "agent_model", "type": "Optional[str]", "description": "LLM model used for provenance tracking", "required": False, "default": None, "example": "claude-sonnet-4-6"},
            ],
            "returns": "Complete Project with id, timestamps, and memory_count",
            "examples": [
                'execute_forgetful_tool("create_project", {"name": "my-project", "description": "A new project", "project_type": "development"})',
            ],
            "tags": ["project", "create", "organization"],
        },
        {
            "name": "update_project",
            "mutates": True,
            "description": "Update project metadata using PATCH semantics (only specified fields are updated)",
            "parameters": [
                {"name": "project_id", "type": "int", "description": "ID of the project to update", "required": True, "example": 1},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
                {"name": "name", "type": "Optional[str]", "description": "New project name", "required": False, "default": None, "example": "updated-project"},
                {"name": "description", "type": "Optional[str]", "description": "New description", "required": False, "default": None, "example": "Updated description"},
                {"name": "project_type", "type": "Optional[ProjectType]", "description": "New project type", "required": False, "default": None, "example": "work"},
                {"name": "status", "type": "Optional[ProjectStatus]", "description": "New status", "required": False, "default": None, "example": "archived"},
                {
                    "name": "repo_name",
                    "type": "Optional[str]",
                    "description": (
                        "New Git repository identifier or address; empty string clears"
                    ),
                    "required": False,
                    "default": None,
                    "example": "user/new-repo",
                },
                {"name": "last_encoding_point", "type": "Optional[str]", "description": 'Opaque caller-managed encoding checkpoint (e.g. a Git commit SHA). Never advanced by the server. Unchanged if omitted or null; pass empty string "" to clear.', "required": False, "default": None, "example": "5d41402abc4b2a76b9719d911017c592"},
                {"name": "notes", "type": "Optional[str]", "description": "New notes", "required": False, "default": None, "example": "Additional notes"},
                {"name": "source_repo", "type": "Optional[str]", "description": "Repository/project source for provenance tracking", "required": False, "default": None, "example": "owner/repo"},
                {"name": "source_files", "type": "Optional[List[str]]", "description": "Files that informed this for provenance tracking", "required": False, "default": None, "example": ["src/main.py"]},
                {"name": "source_url", "type": "Optional[str]", "description": "URL to source material for provenance tracking", "required": False, "default": None, "example": "https://example.com"},
                {"name": "confidence", "type": "Optional[float]", "description": "Encoding confidence score (0.0-1.0) for provenance tracking", "required": False, "default": None, "example": 0.85},
                {"name": "encoding_agent", "type": "Optional[str]", "description": "Software running the agent for provenance tracking", "required": False, "default": None, "example": "OpenCode"},
                {"name": "encoding_version", "type": "Optional[str]", "description": "Version of encoding software for provenance tracking", "required": False, "default": None, "example": "1.0.0"},
                {"name": "agent_id", "type": "Optional[str]", "description": "Agent identity for provenance tracking", "required": False, "default": None, "example": "CodeAgentUltra"},
                {"name": "agent_version", "type": "Optional[str]", "description": "Agent version for provenance tracking", "required": False, "default": None, "example": "1.0"},
                {"name": "agent_model", "type": "Optional[str]", "description": "LLM model used for provenance tracking", "required": False, "default": None, "example": "claude-sonnet-4-6"},
            ],
            "returns": "Updated Project object",
            "examples": [
                'execute_forgetful_tool("update_project", {"project_id": 1, "status": "archived"})',
                'execute_forgetful_tool("update_project", {"project_id": 1, "last_encoding_point": ""})',
            ],
            "tags": ["project", "update", "patch"],
        },
        {
            "name": "delete_project",
            "mutates": True,
            "description": "Delete project while preserving linked memories",
            "parameters": [
                {"name": "project_id", "type": "int", "description": "ID of the project to delete", "required": True, "example": 1},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
            ],
            "returns": "Dictionary with deletion confirmation",
            "examples": [
                'execute_forgetful_tool("delete_project", {"project_id": 1})',
            ],
            "tags": ["project", "delete", "remove"],
        },
        {
            "name": "list_projects",
            "description": "List projects with optional name/status/repository filtering",
            "parameters": [
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
                {
                    "name": "name",
                    "type": "Optional[str]",
                    "description": "Filter by project name (case-insensitive partial match)",
                    "required": False,
                    "default": None,
                    "example": "forgetful",
                },
                {"name": "status", "type": "Optional[ProjectStatus]", "description": "Filter by status (active, archived, completed)", "required": False, "default": None, "example": "active"},
                {
                    "name": "repo_name",
                    "type": "Optional[str]",
                    "description": (
                        "Match equivalent HTTPS/SSH/host-qualified addresses; otherwise "
                        "literal. No partial match"
                    ),
                    "required": False,
                    "default": None,
                    "example": "scottrbk/forgetful",
                },
            ],
            "returns": "Dictionary with projects list and count",
            "examples": [
                'execute_forgetful_tool("list_projects", {})',
                'execute_forgetful_tool("list_projects", {"name": "forgetful"})',
                'execute_forgetful_tool("list_projects", {"status": "active"})',
            ],
            "tags": ["project", "list", "query"],
        },
        {
            "name": "get_project",
            "description": (
                "Retrieve complete project details by integer project_id. "
                "To find a project by name, use list_projects with the name filter first."
            ),
            "parameters": [
                {"name": "project_id", "type": "int", "description": "ID of the project to retrieve", "required": True, "example": 1},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
            ],
            "returns": "Complete Project object with all details",
            "examples": [
                'execute_forgetful_tool("get_project", {"project_id": 1})',
            ],
            "tags": ["project", "retrieve", "read"],
        },
    ]

    for tool_def in tools:
        register_simplified_tool(
            registry=registry,
            name=tool_def["name"],
            category=ToolCategory.PROJECT,
            description=tool_def["description"],
            parameters=tool_def["parameters"],
            returns=tool_def["returns"],
            implementation=adapters[tool_def["name"]],
            examples=tool_def.get("examples", []),
            tags=tool_def.get("tags", []),
            mutates=tool_def.get("mutates", False),
        )

    logger.info(f"Registered {len(tools)} project tools")


# ============================================================================
# Entity Tools Metadata
# ============================================================================

def register_entity_tools_metadata(
    registry: ToolRegistry,
    adapters: dict[str, Any],
):
    """Register entity tool metadata and implementations"""
    tools: list[ToolDef] = [
        {
            "name": "create_entity",
            "mutates": True,
            "description": "Create entity representing a real-world entity (organization, individual, team, device, system)",
            "parameters": [
                {"name": "name", "type": "str", "description": "Entity name", "required": True, "example": "Anthropic"},
                {"name": "entity_type", "type": "str", "description": "Entity type (organization, individual, team, device, system, other)", "required": True, "example": "organization"},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
                {"name": "custom_type", "type": "Optional[str]", "description": "Custom type if 'other' is selected", "required": False, "default": None, "example": "ai-company"},
                {"name": "notes", "type": "Optional[str]", "description": "Additional notes", "required": False, "default": None, "example": "AI safety and research company"},
                {"name": "tags", "type": "Optional[List[str]]", "description": "Tags for categorization", "required": False, "default": None, "example": ["ai", "research"]},
                {"name": "aka", "type": "Optional[List[str]]", "description": "Alternative names/aliases (searchable via search_entities)", "required": False, "default": None, "example": ["Claude AI", "Anthropic AI"]},
                {"name": "project_ids", "type": "Optional[List[int]]", "description": "Link to projects (list of project IDs)", "required": False, "default": None, "example": [1, 2]},
                {"name": "source_repo", "type": "Optional[str]", "description": "Repository/project source for provenance tracking", "required": False, "default": None, "example": "owner/repo"},
                {"name": "source_files", "type": "Optional[List[str]]", "description": "Files that informed this for provenance tracking", "required": False, "default": None, "example": ["src/main.py"]},
                {"name": "source_url", "type": "Optional[str]", "description": "URL to source material for provenance tracking", "required": False, "default": None, "example": "https://example.com"},
                {"name": "confidence", "type": "Optional[float]", "description": "Encoding confidence score (0.0-1.0) for provenance tracking", "required": False, "default": None, "example": 0.85},
                {"name": "encoding_agent", "type": "Optional[str]", "description": "Software running the agent for provenance tracking", "required": False, "default": None, "example": "OpenCode"},
                {"name": "encoding_version", "type": "Optional[str]", "description": "Version of encoding software for provenance tracking", "required": False, "default": None, "example": "1.0.0"},
                {"name": "agent_id", "type": "Optional[str]", "description": "Agent identity for provenance tracking", "required": False, "default": None, "example": "CodeAgentUltra"},
                {"name": "agent_version", "type": "Optional[str]", "description": "Agent version for provenance tracking", "required": False, "default": None, "example": "1.0"},
                {"name": "agent_model", "type": "Optional[str]", "description": "LLM model used for provenance tracking", "required": False, "default": None, "example": "claude-sonnet-4-6"},
            ],
            "returns": "Entity with id and timestamps",
            "examples": [
                'execute_forgetful_tool("create_entity", {"name": "Anthropic", "entity_type": "organization", "aka": ["Claude AI"]})',
            ],
            "tags": ["entity", "create", "knowledge-graph"],
        },
        {
            "name": "get_entity",
            "description": "Retrieve entity by ID with complete details",
            "parameters": [
                {"name": "entity_id", "type": "int", "description": "ID of the entity to retrieve", "required": True, "example": 1},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
            ],
            "returns": "Complete Entity object",
            "examples": [
                'execute_forgetful_tool("get_entity", {"entity_id": 1})',
            ],
            "tags": ["entity", "retrieve", "read"],
        },
        {
            "name": "list_entities",
            "description": "List entities with optional filtering by project, type, or tags",
            "parameters": [
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
                {"name": "project_ids", "type": "Optional[List[int]]", "description": "Filter by projects (list of project IDs)", "required": False, "default": None, "example": [1]},
                {"name": "entity_type", "type": "Optional[str]", "description": "Filter by type", "required": False, "default": None, "example": "organization"},
                {"name": "tags", "type": "Optional[List[str]]", "description": "Filter by tags", "required": False, "default": None, "example": ["ai"]},
            ],
            "returns": "Dictionary with entities list and count",
            "examples": [
                'execute_forgetful_tool("list_entities", {})',
                'execute_forgetful_tool("list_entities", {"entity_type": "organization"})',
            ],
            "tags": ["entity", "list", "query"],
        },
        {
            "name": "search_entities",
            "description": "Search entities by name or alternative names (aka) using text matching (case-insensitive)",
            "parameters": [
                {"name": "query", "type": "str", "description": "Text to search for in entity name or aka (alternative names)", "required": True, "example": "tech"},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
                {"name": "entity_type", "type": "Optional[str]", "description": "Filter by entity type", "required": False, "default": None, "example": "Organization"},
                {"name": "tags", "type": "Optional[List[str]]", "description": "Filter by tags (returns entities with ANY of these)", "required": False, "default": None, "example": ["startup"]},
                {"name": "limit", "type": "int", "description": "Maximum number of results (1-100)", "required": False, "default": 20, "example": 20},
            ],
            "returns": "Dictionary with entities list, total_count, search_query, and filters",
            "examples": [
                'execute_forgetful_tool("search_entities", {"query": "tech"})',
                'execute_forgetful_tool("search_entities", {"query": "MSFT"})',  # Finds entity with aka=["MSFT", "Microsoft"]
            ],
            "tags": ["entity", "search", "query", "text", "aka"],
        },
        {
            "name": "update_entity",
            "mutates": True,
            "description": "Update existing entity (PATCH semantics - only provided fields changed)",
            "parameters": [
                {"name": "entity_id", "type": "int", "description": "ID of the entity to update", "required": True, "example": 1},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
                {"name": "name", "type": "Optional[str]", "description": "New name", "required": False, "default": None, "example": "Updated name"},
                {"name": "entity_type", "type": "Optional[str]", "description": "New type", "required": False, "default": None, "example": "team"},
                {"name": "custom_type", "type": "Optional[str]", "description": "New custom type", "required": False, "default": None, "example": "custom"},
                {"name": "notes", "type": "Optional[str]", "description": "New notes", "required": False, "default": None, "example": "Updated notes"},
                {"name": "tags", "type": "Optional[List[str]]", "description": "New tags (replaces existing)", "required": False, "default": None, "example": ["updated"]},
                {"name": "aka", "type": "Optional[List[str]]", "description": "New alternative names (replaces existing, empty list [] clears)", "required": False, "default": None, "example": ["Alias1", "Alias2"]},
                {"name": "project_ids", "type": "Optional[List[int]]", "description": "New project links (list of project IDs, replaces existing)", "required": False, "default": None, "example": [2, 3]},
                {"name": "source_repo", "type": "Optional[str]", "description": "Repository/project source for provenance tracking", "required": False, "default": None, "example": "owner/repo"},
                {"name": "source_files", "type": "Optional[List[str]]", "description": "Files that informed this for provenance tracking", "required": False, "default": None, "example": ["src/main.py"]},
                {"name": "source_url", "type": "Optional[str]", "description": "URL to source material for provenance tracking", "required": False, "default": None, "example": "https://example.com"},
                {"name": "confidence", "type": "Optional[float]", "description": "Encoding confidence score (0.0-1.0) for provenance tracking", "required": False, "default": None, "example": 0.85},
                {"name": "encoding_agent", "type": "Optional[str]", "description": "Software running the agent for provenance tracking", "required": False, "default": None, "example": "OpenCode"},
                {"name": "encoding_version", "type": "Optional[str]", "description": "Version of encoding software for provenance tracking", "required": False, "default": None, "example": "1.0.0"},
                {"name": "agent_id", "type": "Optional[str]", "description": "Agent identity for provenance tracking", "required": False, "default": None, "example": "CodeAgentUltra"},
                {"name": "agent_version", "type": "Optional[str]", "description": "Agent version for provenance tracking", "required": False, "default": None, "example": "1.0"},
                {"name": "agent_model", "type": "Optional[str]", "description": "LLM model used for provenance tracking", "required": False, "default": None, "example": "claude-sonnet-4-6"},
            ],
            "returns": "Updated Entity object",
            "examples": [
                'execute_forgetful_tool("update_entity", {"entity_id": 1, "aka": ["NewAlias", "AnotherName"]})',
            ],
            "tags": ["entity", "update", "patch"],
        },
        {
            "name": "delete_entity",
            "mutates": True,
            "description": "Delete entity (cascade removes memory links and relationships)",
            "parameters": [
                {"name": "entity_id", "type": "int", "description": "ID of the entity to delete", "required": True, "example": 1},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
            ],
            "returns": "Dictionary with deletion confirmation",
            "examples": [
                'execute_forgetful_tool("delete_entity", {"entity_id": 1})',
            ],
            "tags": ["entity", "delete", "remove"],
        },
        {
            "name": "link_entity_to_memory",
            "mutates": True,
            "description": "Link entity to memory (establishes reference relationship)",
            "parameters": [
                {"name": "entity_id", "type": "int", "description": "ID of the entity", "required": True, "example": 1},
                {"name": "memory_id", "type": "int", "description": "ID of the memory", "required": True, "example": 5},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
            ],
            "returns": "Dictionary with link confirmation",
            "examples": [
                'execute_forgetful_tool("link_entity_to_memory", {"entity_id": 1, "memory_id": 5})',
            ],
            "tags": ["entity", "memory", "link", "linking"],
        },
        {
            "name": "unlink_entity_from_memory",
            "mutates": True,
            "description": "Unlink entity from memory (removes reference relationship)",
            "parameters": [
                {"name": "entity_id", "type": "int", "description": "ID of the entity", "required": True, "example": 1},
                {"name": "memory_id", "type": "int", "description": "ID of the memory", "required": True, "example": 5},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
            ],
            "returns": "Dictionary with unlink confirmation",
            "examples": [
                'execute_forgetful_tool("unlink_entity_from_memory", {"entity_id": 1, "memory_id": 5})',
            ],
            "tags": ["entity", "memory", "unlink", "linking"],
        },
        {
            "name": "link_entity_to_project",
            "mutates": True,
            "description": "Link entity to project (organizational grouping)",
            "parameters": [
                {"name": "entity_id", "type": "int", "description": "ID of the entity", "required": True, "example": 1},
                {"name": "project_id", "type": "int", "description": "ID of the project", "required": True, "example": 5},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
            ],
            "returns": "Dictionary with link confirmation",
            "examples": [
                'execute_forgetful_tool("link_entity_to_project", {"entity_id": 1, "project_id": 5})',
            ],
            "tags": ["entity", "project", "link", "linking"],
        },
        {
            "name": "unlink_entity_from_project",
            "mutates": True,
            "description": "Unlink entity from project (removes organizational grouping)",
            "parameters": [
                {"name": "entity_id", "type": "int", "description": "ID of the entity", "required": True, "example": 1},
                {"name": "project_id", "type": "int", "description": "ID of the project", "required": True, "example": 5},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
            ],
            "returns": "Dictionary with unlink confirmation",
            "examples": [
                'execute_forgetful_tool("unlink_entity_from_project", {"entity_id": 1, "project_id": 5})',
            ],
            "tags": ["entity", "project", "unlink", "linking"],
        },
        {
            "name": "create_entity_relationship",
            "mutates": True,
            "description": "Create typed relationship between two entities (knowledge graph edge)",
            "parameters": [
                {"name": "source_entity_id", "type": "int", "description": "Source entity ID", "required": True, "example": 1},
                {"name": "target_entity_id", "type": "int", "description": "Target entity ID", "required": True, "example": 2},
                {"name": "relationship_type", "type": "str", "description": "Relationship type (part_of, depends_on, uses, works_for, member_of, owns, etc.)", "required": True, "example": "part_of"},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
                {"name": "strength", "type": "Optional[float]", "description": "Relationship strength (0.0-1.0)", "required": False, "default": None, "example": 0.9},
                {"name": "confidence", "type": "Optional[float]", "description": "Confidence level (0.0-1.0)", "required": False, "default": None, "example": 0.95},
                {"name": "metadata", "type": "Optional[Dict[str, Any]]", "description": "Additional metadata", "required": False, "default": None, "example": {"since": "2020"}},
                {"name": "source_repo", "type": "Optional[str]", "description": "Repository/project source for provenance tracking", "required": False, "default": None, "example": "owner/repo"},
                {"name": "source_files", "type": "Optional[List[str]]", "description": "Files that informed this for provenance tracking", "required": False, "default": None, "example": ["src/main.py"]},
                {"name": "source_url", "type": "Optional[str]", "description": "URL to source material for provenance tracking", "required": False, "default": None, "example": "https://example.com"},
                {"name": "encoding_agent", "type": "Optional[str]", "description": "Software running the agent for provenance tracking", "required": False, "default": None, "example": "OpenCode"},
                {"name": "encoding_version", "type": "Optional[str]", "description": "Version of encoding software for provenance tracking", "required": False, "default": None, "example": "1.0.0"},
                {"name": "agent_id", "type": "Optional[str]", "description": "Agent identity for provenance tracking", "required": False, "default": None, "example": "CodeAgentUltra"},
                {"name": "agent_version", "type": "Optional[str]", "description": "Agent version for provenance tracking", "required": False, "default": None, "example": "1.0"},
                {"name": "agent_model", "type": "Optional[str]", "description": "LLM model used for provenance tracking", "required": False, "default": None, "example": "claude-sonnet-4-6"},
            ],
            "returns": "EntityRelationship with id and timestamps",
            "examples": [
                'execute_forgetful_tool("create_entity_relationship", {"source_entity_id": 1, "target_entity_id": 2, "relationship_type": "works_for"})',
                'execute_forgetful_tool("create_entity_relationship", {"source_entity_id": 8, "target_entity_id": 12, "relationship_type": "part_of"})',
            ],
            "tags": ["entity", "relationship", "knowledge-graph"],
        },
        {
            "name": "get_entity_relationships",
            "description": "Get relationships for an entity (knowledge graph edges)",
            "parameters": [
                {"name": "entity_id", "type": "int", "description": "Entity ID", "required": True, "example": 1},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
                {"name": "direction", "type": "Optional[str]", "description": "Filter by direction (outgoing, incoming, both)", "required": False, "default": None, "example": "outgoing"},
                {"name": "relationship_type", "type": "Optional[str]", "description": "Filter by type", "required": False, "default": None, "example": "part_of"},
            ],
            "returns": "Dictionary with relationships list",
            "examples": [
                'execute_forgetful_tool("get_entity_relationships", {"entity_id": 1})',
                'execute_forgetful_tool("get_entity_relationships", {"entity_id": 1, "direction": "outgoing"})',
            ],
            "tags": ["entity", "relationship", "query"],
        },
        {
            "name": "update_entity_relationship",
            "mutates": True,
            "description": "Update entity relationship (PATCH semantics - only provided fields changed)",
            "parameters": [
                {"name": "relationship_id", "type": "int", "description": "Relationship ID", "required": True, "example": 1},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
                {"name": "relationship_type", "type": "Optional[str]", "description": "New relationship type", "required": False, "default": None, "example": "collaborates_with"},
                {"name": "strength", "type": "Optional[float]", "description": "New strength", "required": False, "default": None, "example": 0.8},
                {"name": "confidence", "type": "Optional[float]", "description": "New confidence", "required": False, "default": None, "example": 0.9},
                {"name": "metadata", "type": "Optional[Dict[str, Any]]", "description": "New metadata", "required": False, "default": None, "example": {"updated": "2024"}},
                {"name": "source_repo", "type": "Optional[str]", "description": "Repository/project source for provenance tracking", "required": False, "default": None, "example": "owner/repo"},
                {"name": "source_files", "type": "Optional[List[str]]", "description": "Files that informed this for provenance tracking", "required": False, "default": None, "example": ["src/main.py"]},
                {"name": "source_url", "type": "Optional[str]", "description": "URL to source material for provenance tracking", "required": False, "default": None, "example": "https://example.com"},
                {"name": "encoding_agent", "type": "Optional[str]", "description": "Software running the agent for provenance tracking", "required": False, "default": None, "example": "OpenCode"},
                {"name": "encoding_version", "type": "Optional[str]", "description": "Version of encoding software for provenance tracking", "required": False, "default": None, "example": "1.0.0"},
                {"name": "agent_id", "type": "Optional[str]", "description": "Agent identity for provenance tracking", "required": False, "default": None, "example": "CodeAgentUltra"},
                {"name": "agent_version", "type": "Optional[str]", "description": "Agent version for provenance tracking", "required": False, "default": None, "example": "1.0"},
                {"name": "agent_model", "type": "Optional[str]", "description": "LLM model used for provenance tracking", "required": False, "default": None, "example": "claude-sonnet-4-6"},
            ],
            "returns": "Updated EntityRelationship object",
            "examples": [
                'execute_forgetful_tool("update_entity_relationship", {"relationship_id": 1, "strength": 0.95})',
            ],
            "tags": ["entity", "relationship", "update"],
        },
        {
            "name": "delete_entity_relationship",
            "mutates": True,
            "description": "Delete entity relationship (removes knowledge graph edge)",
            "parameters": [
                {"name": "relationship_id", "type": "int", "description": "Relationship ID to delete", "required": True, "example": 1},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
            ],
            "returns": "Dictionary with deletion confirmation",
            "examples": [
                'execute_forgetful_tool("delete_entity_relationship", {"relationship_id": 1})',
            ],
            "tags": ["entity", "relationship", "delete"],
        },
        {
            "name": "get_entity_memories",
            "description": "Get all memories linked to a specific entity (useful for entity deduplication and auditing)",
            "parameters": [
                {"name": "entity_id", "type": "int", "description": "ID of the entity to get memories for", "required": True, "example": 42},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
            ],
            "returns": "Dictionary with memory_ids (list of int), count (int), and memories "
                       "(list of {id, title} objects, same order as memory_ids)",
            "examples": [
                'execute_forgetful_tool("get_entity_memories", {"entity_id": 42})',
                (
                    '# Returns: {"memory_ids": [3, 7], "count": 2, "memories": '
                    '[{"id": 3, "title": "Q3 Roadmap"}, '
                    '{"id": 7, "title": "Onboarding Notes"}]}'
                ),
            ],
            "tags": ["entity", "memory", "query", "linking"],
        },
        {
            "name": "get_memory_entities",
            "description": "Get all entities directly linked to a specific memory (reverse of get_entity_memories)",
            "parameters": [
                {"name": "memory_id", "type": "int", "description": "ID of the memory to get entities for", "required": True, "example": 156},
                {"name": "ctx", "type": "Context", "description": "FastMCP Context (automatically injected)", "required": True},
            ],
            "returns": "Dictionary with entity_ids (list of int), count (int), and entities "
                       "(list of {id, name, entity_type} objects, same order as entity_ids). "
                       "Always the complete set; not paginated",
            "examples": [
                'execute_forgetful_tool("get_memory_entities", {"memory_id": 156})',
                (
                    '# Returns: {"entity_ids": [42], "count": 1, "entities": '
                    '[{"id": 42, "name": "Sarah Chen", "entity_type": "Individual"}]}'
                ),
            ],
            "tags": ["entity", "memory", "query", "linking"],
        },
    ]

    for tool_def in tools:
        register_simplified_tool(
            registry=registry,
            name=tool_def["name"],
            category=ToolCategory.ENTITY,
            description=tool_def["description"],
            parameters=tool_def["parameters"],
            returns=tool_def["returns"],
            implementation=adapters[tool_def["name"]],
            examples=tool_def.get("examples", []),
            tags=tool_def.get("tags", []),
            mutates=tool_def.get("mutates", False),
        )

    logger.info(f"Registered {len(tools)} entity tools")
