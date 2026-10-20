"""End-to-end tests for MCP memory tools: update_memory and get_memory

Covers PATCH semantics, embedding refresh, list-field replacement, error handling
for unknown IDs and basic retrieval by ID.
"""
import pytest
from rest_helpers import MISSING_ID

from tests.e2e.memory_tools_helpers_e2e import (
    call_tool,
    create_memory,
    find_by_id,
    get_memory,
    query_memory,
    update_memory,
)


def _assert_get_memory_fields(data, memory_id):
    """Scalar and list fields returned by get_memory for the retrieval test."""
    assert data["id"] == memory_id
    assert data["title"] == "Get Memory E2E Test"
    assert data["content"] == "Testing get_memory retrieval by ID in E2E environment"
    assert data["context"] == "Validating get_memory tool functionality"
    assert data["keywords"] == ["get", "retrieval", "testing"]
    assert data["tags"] == ["test", "get-memory"]
    assert data["importance"] == 8


def _assert_get_memory_link_lists(data):
    """Link collections must always be lists."""
    assert isinstance(data["linked_memory_ids"], list)
    assert data["project"] is None


@pytest.mark.asyncio
async def test_update_memory_single_field_e2e(mcp_client):
    """Test updating a single field preserves other fields (PATCH semantics)"""
    create_result = await create_memory(
        mcp_client,
        title="Original Title",
        content="Original content about Python asyncio",
        context="Original context for testing",
        keywords=["python", "asyncio", "original"],
        tags=["test", "original"],
        importance=7,
    )
    assert create_result.data is not None
    memory_id = create_result.data["id"]
    update_result = await update_memory(mcp_client, memory_id, title="Updated Title")
    assert update_result.data is not None
    assert update_result.data["id"] == memory_id
    assert update_result.data["title"] == "Updated Title"
    assert update_result.data["content"] == "Original content about Python asyncio"
    assert update_result.data["context"] == "Original context for testing"
    assert update_result.data["keywords"] == ["python", "asyncio", "original"]
    assert update_result.data["tags"] == ["test", "original"]
    assert update_result.data["importance"] == 7


@pytest.mark.asyncio
async def test_update_memory_multiple_fields_e2e(mcp_client):
    """Test updating multiple fields simultaneously (PATCH semantics)"""
    create_result = await create_memory(
        mcp_client,
        title="Original Multi-Field Title",
        content="Original content for multi-field test",
        context="Original context",
        keywords=["original"],
        tags=["test"],
        importance=6,
    )
    assert create_result.data is not None
    memory_id = create_result.data["id"]
    update_result = await update_memory(
        mcp_client,
        memory_id,
        title="Updated Multi-Field Title",
        content="Updated content for multi-field test",
        importance=9,
    )
    assert update_result.data is not None
    assert update_result.data["title"] == "Updated Multi-Field Title"
    assert update_result.data["content"] == "Updated content for multi-field test"
    assert update_result.data["importance"] == 9
    assert update_result.data["context"] == "Original context"
    assert update_result.data["keywords"] == ["original"]
    assert update_result.data["tags"] == ["test"]


@pytest.mark.asyncio
async def test_update_memory_content_triggers_embedding_refresh_e2e(mcp_client):
    """Test that updating content regenerates embeddings for semantic search"""
    create_result = await create_memory(
        mcp_client,
        title="Async Programming Patterns",
        content="Python asyncio enables concurrent I/O operations with async/await syntax",
        context="Testing embedding refresh on content update",
        keywords=["python", "asyncio"],
        tags=["programming"],
        importance=8,
    )
    assert create_result.data is not None
    memory_id = create_result.data["id"]
    update_result = await update_memory(
        mcp_client,
        memory_id,
        content="Rust async enables concurrent operations using futures and tokio runtime",
    )
    assert update_result.data is not None
    query_result = await query_memory(
        mcp_client,
        "Rust async programming",
        "testing embedding refresh after content update",
        k=10,
        include_links=False,
    )
    assert query_result.data is not None
    found_ids = [m["id"] for m in query_result.data["primary_memories"]]
    assert memory_id in found_ids, "Updated memory should be found by semantic search for new content"


@pytest.mark.asyncio
async def test_update_memory_persistence_e2e(mcp_client):
    """Test that memory updates persist to database"""
    create_result = await create_memory(
        mcp_client,
        title="Persistence Test Original",
        content="Testing database persistence of updates",
        context="E2E persistence validation",
        keywords=["persistence", "database"],
        tags=["test"],
        importance=7,
    )
    assert create_result.data is not None
    memory_id = create_result.data["id"]
    update_result = await update_memory(
        mcp_client, memory_id, title="Persistence Test Updated", importance=9,
    )
    assert update_result.data is not None
    query_result = await query_memory(
        mcp_client,
        "Persistence Test Updated",
        "verifying update persistence",
        k=5,
        include_links=False,
    )
    found_memory = find_by_id(query_result.data["primary_memories"], memory_id)
    assert found_memory is not None, "Updated memory should be found in database"
    assert found_memory["title"] == "Persistence Test Updated"
    assert found_memory["importance"] == 9


@pytest.mark.asyncio
async def test_update_memory_keywords_tags_replacement_e2e(mcp_client):
    """Test that updating keywords/tags replaces (not appends) existing values"""
    create_result = await create_memory(
        mcp_client,
        title="Keywords Tags Replacement Test",
        content="Testing REPLACE semantics for list fields",
        context="Validating list field behavior",
        keywords=["python", "testing", "original"],
        tags=["test", "automation", "original"],
        importance=7,
    )
    assert create_result.data is not None
    memory_id = create_result.data["id"]
    update_result = await update_memory(
        mcp_client,
        memory_id,
        keywords=["javascript", "unit-test", "jest"],
        tags=["ci", "jest", "integration"],
    )
    assert update_result.data is not None
    assert update_result.data["keywords"] == ["javascript", "unit-test", "jest"]
    assert update_result.data["tags"] == ["ci", "jest", "integration"]
    assert "python" not in update_result.data["keywords"]
    assert "original" not in update_result.data["keywords"]
    assert "automation" not in update_result.data["tags"]


@pytest.mark.asyncio
async def test_update_memory_invalid_id_e2e(mcp_client):
    """Test error handling when updating non-existent memory"""
    try:
        await call_tool(
            mcp_client, "update_memory", memory_id=MISSING_ID, title="This Should Fail",
        )
        assert False, "Expected ToolError for invalid memory_id"
    except Exception as e:
        error_message = str(e)
        assert (
            "not found" in error_message.lower()
            or "validation_error" in error_message.lower()
        )


@pytest.mark.asyncio
async def test_get_memory_basic_retrieval_e2e(mcp_client):
    """Test basic memory retrieval by ID with get_memory tool"""
    create_result = await create_memory(
        mcp_client,
        title="Get Memory E2E Test",
        content="Testing get_memory retrieval by ID in E2E environment",
        context="Validating get_memory tool functionality",
        keywords=["get", "retrieval", "testing"],
        tags=["test", "get-memory"],
        importance=8,
    )
    assert create_result.data is not None
    memory_id = create_result.data["id"]
    get_result = await get_memory(mcp_client, memory_id)
    assert get_result.data is not None
    _assert_get_memory_fields(get_result.data, memory_id)
    _assert_get_memory_link_lists(get_result.data)


@pytest.mark.asyncio
async def test_get_memory_invalid_id_e2e(mcp_client):
    """Test error handling when retrieving non-existent memory"""
    try:
        await get_memory(mcp_client, MISSING_ID)
        assert False, "Expected ToolError for invalid memory_id"
    except Exception as e:
        error_message = str(e)
        assert (
            "not found" in error_message.lower()
            or "validation_error" in error_message.lower()
        )
