# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""End-to-end tests for MCP memory tools: create and query

Tests the complete stack: FastMCP Client -> MCP Protocol -> Service -> Repository
-> Qdrant + embeddings. Sibling modules cover update/get, obsolete
handling, project links, get_recent_memories and provenance.
"""
import pytest

from tests.e2e.memory_tools_helpers_e2e import (
    create_memory,
    find_by_id,
    query_memory,
)


def _assert_query_envelope(data):
    """Check the query_memory envelope fields (query echo, counts, types)."""
    assert data["query"] == "python testing practices"
    assert isinstance(data["primary_memories"], list)
    assert isinstance(data["linked_memories"], list)
    assert data["total_count"] > 0
    assert data["token_count"] > 0
    assert isinstance(data["truncated"], bool)


def _assert_scores_align_with_primaries(data):
    """Every score row must refer to the primary memory at the same position."""
    scores = data["scores"]
    primaries = data["primary_memories"]
    assert len(scores) == len(primaries)
    for idx, score in enumerate(scores):
        assert score["memory_id"] == primaries[idx]["id"]


@pytest.mark.asyncio
async def test_create_memory_basic_e2e(mcp_client):
    """Test basic memory creation with real embeddings and database"""
    result = await create_memory(
        mcp_client,
        title="Python AsyncIO E2E Test",
        content="AsyncIO enables concurrent I/O operations in Python using async/await syntax",
        context="Testing memory creation in E2E environment with real database",
        keywords=["python", "asyncio", "testing"],
        tags=["testing", "python"],
        importance=8,
    )
    assert result.data is not None
    assert result.data["id"] is not None
    assert result.data["title"] == "Python AsyncIO E2E Test"
    assert isinstance(result.data["linked_memory_ids"], list)
    assert isinstance(result.data["similar_memories"], list)
    assert result.data["project"] is None


@pytest.mark.asyncio
async def test_create_memory_auto_linking_e2e(mcp_client):
    """Test that create_memory auto-links to similar memories via embeddings"""
    result1 = await create_memory(
        mcp_client,
        title="Docker Container Basics",
        content="Docker containers provide isolated environments for applications",
        context="Testing auto-linking behavior in E2E",
        keywords=["docker", "containers", "devops"],
        tags=["docker", "infrastructure"],
        importance=7,
    )
    assert result1.data is not None
    memory1_id = result1.data["id"]
    result2 = await create_memory(
        mcp_client,
        title="Docker Networking",
        content="Docker provides networking capabilities to connect containers",
        context="Testing auto-linking with semantic similarity",
        keywords=["docker", "networking", "containers"],
        tags=["docker", "networking"],
        importance=7,
    )
    assert result2.data is not None
    assert len(result2.data["similar_memories"]) > 0
    similar_ids = [m["id"] for m in result2.data["similar_memories"]]
    assert memory1_id in similar_ids


@pytest.mark.asyncio
async def test_query_memory_e2e(mcp_client):
    """Test semantic memory search against a real Qdrant"""
    create_result = await create_memory(
        mcp_client,
        title="Python Testing Best Practices",
        content="Use pytest for testing Python applications with fixtures and parametrization",
        context="Testing semantic search in E2E environment",
        keywords=["python", "pytest", "testing"],
        tags=["testing", "best-practices"],
        importance=8,
    )
    assert create_result.data is not None
    query_result = await query_memory(
        mcp_client,
        "python testing practices",
        "looking for testing information",
        k=5,
        include_links=False,
    )
    assert query_result.data is not None
    _assert_query_envelope(query_result.data)
    _assert_scores_align_with_primaries(query_result.data)
    found_titles = [m["title"] for m in query_result.data["primary_memories"]]
    assert "Python Testing Best Practices" in found_titles


@pytest.mark.asyncio
async def test_create_and_query_persistence_e2e(mcp_client):
    """Test that memories persist in database and can be queried"""
    unique_title = "FastAPI Development Patterns E2E"
    create_result = await create_memory(
        mcp_client,
        title=unique_title,
        content="FastAPI is a modern Python web framework with automatic API documentation",
        context="Testing database persistence across operations",
        keywords=["fastapi", "python", "web"],
        tags=["framework", "python"],
        importance=7,
    )
    assert create_result.data is not None
    created_id = create_result.data["id"]
    query_result = await query_memory(
        mcp_client,
        "FastAPI web framework",
        "verifying persistence of created memory",
        k=10,
        include_links=False,
    )
    assert query_result.data is not None
    found_memory = find_by_id(query_result.data["primary_memories"], created_id)
    assert found_memory is not None
    assert found_memory["title"] == unique_title
    assert found_memory["importance"] == 7


@pytest.mark.asyncio
async def test_query_memory_with_linked_memories_e2e(mcp_client):
    """Test query with include_links to retrieve 1-hop neighbor memories"""
    result1 = await create_memory(
        mcp_client,
        title="PostgreSQL Database",
        content="PostgreSQL is a powerful open-source relational database",
        context="Testing linked memory retrieval",
        keywords=["postgresql", "database", "sql"],
        tags=["database"],
        importance=8,
    )
    assert result1.data is not None
    result2 = await create_memory(
        mcp_client,
        title="PostgreSQL Indexing",
        content="PostgreSQL supports various index types including B-tree and GIN",
        context="Testing linked memory retrieval with auto-linking",
        keywords=["postgresql", "indexing", "performance"],
        tags=["database", "performance"],
        importance=7,
    )
    assert len(result2.data["similar_memories"]) > 0
    query_result = await query_memory(
        mcp_client,
        "postgresql database",
        "testing linked memory retrieval",
        k=5,
        include_links=True,
        max_links_per_primary=5,
    )
    assert query_result.data is not None
    assert len(query_result.data["primary_memories"]) > 0
    assert isinstance(query_result.data["linked_memories"], list)
    total = (
        len(query_result.data["primary_memories"])
        + len(query_result.data["linked_memories"])
    )
    assert query_result.data["total_count"] == total
