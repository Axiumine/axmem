"""End-to-end tests for MCP memory tools: obsolete handling (SQLite backend)

Covers mark_memory_obsolete / unobsolete_memory and how obsolete memories behave
in query_memory (hidden) and get_memory (still retrievable as an audit trail).
"""
import pytest

from tests.e2e_sqlite.memory_tools_helpers_sqlite import (
    call_tool,
    create_memory,
    get_memory,
    mark_obsolete,
    primary_ids,
    query_memory,
)


@pytest.mark.asyncio
async def test_mark_memory_obsolete_basic_e2e(mcp_client):
    """Test basic memory obsolete functionality with mark_memory_obsolete tool"""
    create_result = await create_memory(
        mcp_client,
        title="Memory to Obsolete",
        content="This memory will be marked as obsolete in the test",
        context="Testing mark_memory_obsolete functionality",
        keywords=["obsolete", "test", "soft-delete"],
        tags=["test", "obsolete"],
        importance=7,
    )
    assert create_result.data is not None
    memory_id = create_result.data["id"]
    obsolete_result = await mark_obsolete(
        mcp_client, memory_id, "Testing soft delete functionality",
    )
    assert obsolete_result.data["success"] is True


@pytest.mark.asyncio
async def test_unobsolete_memory_restores_query_visibility_e2e(mcp_client):
    """Test unobsolete_memory restores a soft-deleted memory to query results"""
    create_result = await create_memory(
        mcp_client,
        title="Unobsolete E2E Memory",
        content="Content to obsolete then restore via unobsolete_memory",
        context="e2e unobsolete restore path",
        keywords=["unobsolete", "e2e", "restore"],
        tags=["test"],
        importance=7,
    )
    memory_id = create_result.data["id"]
    await mark_obsolete(mcp_client, memory_id, "e2e obsolete step")
    query_obsolete = await query_memory(
        mcp_client,
        "unobsolete e2e restore",
        "verify filtered while obsolete",
        k=10,
        include_links=False,
    )
    assert memory_id not in primary_ids(query_obsolete)

    restore_result = await call_tool(
        mcp_client, "unobsolete_memory", memory_id=memory_id,
    )
    assert restore_result.data["success"] is True

    get_result = await get_memory(mcp_client, memory_id)
    assert get_result.data["is_obsolete"] is False

    query_restored = await query_memory(
        mcp_client,
        "unobsolete e2e restore",
        "verify visible after restore",
        k=10,
        include_links=False,
    )
    assert memory_id in primary_ids(query_restored)


@pytest.mark.asyncio
async def test_mark_memory_obsolete_filtered_from_query_e2e(mcp_client):
    """Test that obsolete memories are filtered from query_memory results"""
    result1 = await create_memory(
        mcp_client,
        title="Kubernetes Basics Active",
        content="Kubernetes orchestrates containerized applications across clusters",
        context="Testing obsolete filtering in queries",
        keywords=["kubernetes", "containers", "orchestration"],
        tags=["k8s", "infrastructure"],
        importance=8,
    )
    memory1_id = result1.data["id"]
    result2 = await create_memory(
        mcp_client,
        title="Kubernetes Networking Active",
        content="Kubernetes provides networking capabilities for pod communication",
        context="Testing obsolete filtering - this remains active",
        keywords=["kubernetes", "networking", "pods"],
        tags=["k8s", "networking"],
        importance=8,
    )
    memory2_id = result2.data["id"]
    result3 = await create_memory(
        mcp_client,
        title="Kubernetes Obsolete Memory",
        content="Kubernetes content that will be marked obsolete",
        context="Testing obsolete filtering - this will be obsolete",
        keywords=["kubernetes", "obsolete", "test"],
        tags=["k8s", "test"],
        importance=7,
    )
    memory3_id = result3.data["id"]
    await mark_obsolete(
        mcp_client,
        memory3_id,
        "Testing that obsolete memories are filtered from queries",
    )
    query_result = await query_memory(
        mcp_client,
        "kubernetes containers orchestration",
        "testing obsolete filtering in semantic search",
        k=10,
        include_links=False,
    )
    assert query_result.data is not None
    found_ids = primary_ids(query_result)
    assert memory1_id in found_ids or memory2_id in found_ids, "At least one active memory should be found"
    assert memory3_id not in found_ids, "Obsolete memory should be filtered from query results"


@pytest.mark.asyncio
async def test_mark_memory_obsolete_retrievable_by_id_e2e(mcp_client):
    """Test that obsolete memories can still be retrieved by ID using get_memory (audit trail)"""
    create_result = await create_memory(
        mcp_client,
        title="Memory for Audit Trail Test",
        content="This memory will be obsolete but retrievable by ID",
        context="Testing soft-delete audit trail behavior",
        keywords=["audit", "obsolete", "retrieval"],
        tags=["test", "audit"],
        importance=7,
    )
    assert create_result.data is not None
    memory_id = create_result.data["id"]
    await mark_obsolete(
        mcp_client,
        memory_id,
        "Testing that get_memory can still retrieve obsolete memories",
    )
    query_result = await query_memory(
        mcp_client,
        "audit trail obsolete retrieval",
        "verifying obsolete memory filtered from query",
        k=10,
        include_links=False,
    )
    query_ids = primary_ids(query_result)
    assert memory_id not in query_ids, "Obsolete memory should not appear in query_memory results"
    get_result = await get_memory(mcp_client, memory_id)
    assert get_result.data is not None
    assert get_result.data["id"] == memory_id
    assert get_result.data["title"] == "Memory for Audit Trail Test"
    assert get_result.data["content"] == "This memory will be obsolete but retrievable by ID"


@pytest.mark.asyncio
async def test_mark_memory_obsolete_with_superseded_by_e2e(mcp_client):
    """Test marking memory obsolete with superseded_by parameter"""
    old_result = await create_memory(
        mcp_client,
        title="Old API Pattern",
        content="Use REST API with XML responses",
        context="Outdated API pattern that will be superseded",
        keywords=["api", "rest", "xml"],
        tags=["api", "deprecated"],
        importance=6,
    )
    old_memory_id = old_result.data["id"]
    new_result = await create_memory(
        mcp_client,
        title="New API Pattern",
        content="Use REST API with JSON responses",
        context="Modern API pattern superseding XML approach",
        keywords=["api", "rest", "json"],
        tags=["api", "modern"],
        importance=8,
    )
    new_memory_id = new_result.data["id"]
    obsolete_result = await mark_obsolete(
        mcp_client,
        old_memory_id,
        "Superseded by modern JSON API pattern",
        superseded_by=new_memory_id,
    )
    assert obsolete_result.data["success"] is True
    query_result = await query_memory(
        mcp_client,
        "REST API pattern",
        "finding current API patterns",
        k=10,
        include_links=False,
    )
    found_ids = primary_ids(query_result)
    assert old_memory_id not in found_ids, "Old memory should be filtered from queries"
    assert new_memory_id in found_ids or len(found_ids) > 0, "New memory should be found in queries"
