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
from app.routes.mcp.tool_adapters import create_memory_adapters
from app.routes.mcp.tool_registry import ToolRegistry
from app.services.memory_service import MemoryService

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
                    "name": "project",
                    "type": "Optional[str]",
                    "description": "Optional free-form project scope label (max 500 characters); created on first use",
                    "required": False,
                    "default": None,
                    "example": "forgetful",
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
                    "name": "project",
                    "type": "Optional[str]",
                    "description": "Only search memories in this project scope",
                    "required": False,
                    "default": None,
                    "example": "forgetful",
                },
                {
                    "name": "strict_project_filter",
                    "type": "bool",
                    "description": "If True, linked memories must also be in the specified project scope",
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
                    "type": "str",
                    "description": "ID of the memory to update",
                    "required": True,
                    "example": "018f2c3e-0000-7000-8000-000000000042",
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
                    "name": "project",
                    "type": "Optional[str]",
                    "description": "New project scope label - replaces the existing one; an empty string clears it (optional)",
                    "required": False,
                    "default": None,
                    "example": "forgetful",
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
                'execute_forgetful_tool("update_memory", {"memory_id": "018f2c3e-0000-7000-8000-000000000042", "importance": 9})',
                'execute_forgetful_tool("update_memory", {"memory_id": "018f2c3e-0000-7000-8000-000000000042", "content": "Updated content", "tags": ["revised", "important"]})',
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
                    "type": "str",
                    "description": "Source memory ID (aliases: source_id, from_id, id)",
                    "required": True,
                    "example": "018f2c3e-0000-7000-8000-000000000042",
                },
                {
                    "name": "related_ids",
                    "type": "List[str]",
                    "description": "List of target memory IDs to link (or a single id; aliases: target_id, target_ids, related_id)",
                    "required": True,
                    "example": ["018f2c3e-0000-7000-8000-000000000010", "018f2c3e-0000-7000-8000-000000000015", "018f2c3e-0000-7000-8000-000000000020"],
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
                'execute_forgetful_tool("link_memories", {"memory_id": "018f2c3e-0000-7000-8000-000000000042", "related_ids": ["018f2c3e-0000-7000-8000-000000000010", "018f2c3e-0000-7000-8000-000000000015"]})',
                'execute_forgetful_tool("link_memories", {"source_id": "018f2c3e-0000-7000-8000-000000000042", "target_id": "018f2c3e-0000-7000-8000-000000000057"})',
                'execute_forgetful_tool("link_memories", {"memory_ids": ["018f2c3e-0000-7000-8000-000000000042", "018f2c3e-0000-7000-8000-000000000057"]})',
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
                    "type": "str",
                    "description": "Source memory ID (aliases: memory_id, from_id, id)",
                    "required": True,
                    "example": "018f2c3e-0000-7000-8000-000000000042",
                },
                {
                    "name": "target_id",
                    "type": "str",
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
                'execute_forgetful_tool("unlink_memories", {"source_id": "018f2c3e-0000-7000-8000-000000000042", "target_id": "018f2c3e-0000-7000-8000-000000000057"})',
                'execute_forgetful_tool("unlink_memories", {"memory_id": "018f2c3e-0000-7000-8000-000000000042", "related_id": "018f2c3e-0000-7000-8000-000000000057"})',
                'execute_forgetful_tool("unlink_memories", {"memory_id": "018f2c3e-0000-7000-8000-000000000042", "related_ids": ["018f2c3e-0000-7000-8000-000000000057", "018f2c3e-0000-7000-8000-000000000058"]})',
                'execute_forgetful_tool("unlink_memories", {"memory_ids": ["018f2c3e-0000-7000-8000-000000000042", "018f2c3e-0000-7000-8000-000000000057", "018f2c3e-0000-7000-8000-000000000058"]})',
            ],
            "tags": ["memory", "unlink", "graph", "linking"],
        },
        {
            "name": "get_memory",
            "description": "Retrieve complete memory details by ID",
            "parameters": [
                {
                    "name": "memory_id",
                    "type": "str",
                    "description": "ID of the memory to retrieve",
                    "required": True,
                    "example": "018f2c3e-0000-7000-8000-000000000042",
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
                'execute_forgetful_tool("get_memory", {"memory_id": "018f2c3e-0000-7000-8000-000000000042"})',
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
                    "type": "str",
                    "description": "ID of the memory to mark as obsolete",
                    "required": True,
                    "example": "018f2c3e-0000-7000-8000-000000000042",
                },
                {
                    "name": "reason",
                    "type": "str",
                    "description": "Explanation for why this memory is obsolete",
                    "required": True,
                    "example": "Superseded by a newer decision",
                },
                {
                    "name": "ctx",
                    "type": "Context",
                    "description": "FastMCP Context (automatically injected)",
                    "required": True,
                },
                {
                    "name": "superseded_by",
                    "type": "Optional[str]",
                    "description": "Optional ID of the replacement memory",
                    "required": False,
                    "default": None,
                    "example": "018f2c3e-0000-7000-8000-000000000100",
                },
            ],
            "returns": "Boolean indicating success",
            "examples": [
                'execute_forgetful_tool("mark_memory_obsolete", {"memory_id": "018f2c3e-0000-7000-8000-000000000042", "reason": "Outdated approach", "superseded_by": "018f2c3e-0000-7000-8000-000000000100"})',
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
                    "type": "str",
                    "description": "ID of the obsolete memory to restore",
                    "required": True,
                    "example": "018f2c3e-0000-7000-8000-000000000042",
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
                'execute_forgetful_tool("unobsolete_memory", {"memory_id": "018f2c3e-0000-7000-8000-000000000042"})',
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
                    "name": "project",
                    "type": "Optional[str]",
                    "description": "Optional filter to one project scope",
                    "required": False,
                    "default": None,
                    "example": "forgetful",
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
                'execute_forgetful_tool("get_recent_memories", {"limit": 10, "project": "forgetful"})',
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
                "Rebuild embeddings for a tenant-scoped subset of memories without resetting "
                "global vector storage. Targets explicit memory ids, a single project scope, or "
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
                    "type": "Optional[List[str]]",
                    "description": "Explicit memory ids owned by the caller; leave null to use project or the whole tenant",
                    "required": False,
                    "default": None,
                    "example": ["018f2c3e-0000-7000-8000-000000000123", "018f2c3e-0000-7000-8000-000000000124"],
                },
                {
                    "name": "project",
                    "type": "Optional[str]",
                    "description": "Restrict the rebuild to memories in this project scope of the caller",
                    "required": False,
                    "default": None,
                    "example": "forgetful",
                },
            ],
            "returns": (
                "Dict with rebuilt_ids (List[str]), skipped_ids (List[str]; not owned, or edited "
                "while the rebuild ran so a newer embedding was kept), "
                "failed (List[Dict[memory_id, reason]]; reason is a code such as embedding_failed, "
                "persistence_failed or not_found_or_not_owned). "
                "An unknown project scope is rejected with VALIDATION_ERROR"
            ),
            "examples": [
                'execute_forgetful_tool("rebuild_embeddings", {"memory_ids": ["018f2c3e-0000-7000-8000-000000000123", "018f2c3e-0000-7000-8000-000000000124"]})',
                'execute_forgetful_tool("rebuild_embeddings", {"project": "forgetful"})',
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
    memory_service: MemoryService,
    re_embedding_service,
):
    """Register all tool metadata and implementations

    Args:
        registry: ToolRegistry instance to register tools with
        memory_service: MemoryService instance
        re_embedding_service: ReEmbeddingService instance created at startup
    """
    logger.info("Starting tool registration")

    memory_adapters = create_memory_adapters(memory_service, re_embedding_service)

    register_memory_tools_metadata(registry, memory_adapters)

    # Log summary
    categories = registry.list_categories()
    total = sum(categories.values())
    logger.info(f"Tool registration complete: {total} tools across {len(categories)} categories")
    logger.info(f"Categories: {categories}")
