"""E2E tests for link_memories tool with auto-linking disabled.

These tests verify manual linking functionality in isolation by disabling
the auto-linking feature (MEMORY_NUM_AUTO_LINK=0) to ensure we're testing
manual linking, not auto-linking side effects.
"""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

SETTINGS_OVERRIDE = {"MEMORY_NUM_AUTO_LINK": 0}


# ---------------------------------------------------------------------------
# Shared step helpers
# ---------------------------------------------------------------------------

async def _call_tool(mcp_client, tool_name, arguments):
    """Invoke a Forgetful tool through the execute_forgetful_tool meta-tool."""
    return await mcp_client.call_tool("execute_forgetful_tool", {
        "tool_name": tool_name,
        "arguments": arguments,
    })


async def _create_memory(
    mcp_client, title, content, context, keywords, tags, importance,
):
    """Create a memory through the meta-tool and return the raw tool result."""
    return await _call_tool(mcp_client, "create_memory", {
        "title": title,
        "content": content,
        "context": context,
        "keywords": keywords,
        "tags": tags,
        "importance": importance,
    })


async def _link_memories(mcp_client, memory_id, related_ids):
    """Link memory_id to related_ids and return the raw tool result."""
    return await _call_tool(mcp_client, "link_memories", {
        "memory_id": memory_id,
        "related_ids": related_ids,
    })


async def _find_in_query(mcp_client, query, query_context, memory_id):
    """Run query_memory and return the primary memory with memory_id, or None."""
    result = await _call_tool(mcp_client, "query_memory", {
        "query": query,
        "query_context": query_context,
        "k": 10,
        "include_links": False,
    })
    return next(
        (m for m in result.data["primary_memories"] if m["id"] == memory_id),
        None,
    )


# Step helpers for test_link_memories_batch_e2e

def _assert_batch_link_result(link_result, expected_ids):
    """The batch link call reports exactly the requested targets as a list."""
    assert link_result.data is not None
    assert isinstance(link_result.data["linked_memory_ids"], list)
    assert set(link_result.data["linked_memory_ids"]) == expected_ids, (
        f'Expected all 3 IDs, got {link_result.data["linked_memory_ids"]}'
    )


# Step helpers for test_unlink_memories_basic_e2e

async def _assert_link_exists_before_unlink(mcp_client, memory1_id, memory2_id):
    """The link created before unlinking is visible from memory1."""
    # Verify link exists in memory1
    found_memory1 = await _find_in_query(
        mcp_client,
        "Functional Programming Concepts",
        "verifying link exists before unlink",
        memory1_id,
    )
    assert found_memory1 is not None
    assert memory2_id in found_memory1["linked_memory_ids"], "Link should exist before unlink"


async def _assert_link_removed_both_ways(mcp_client, memory1_id, memory2_id):
    """After unlinking, neither memory lists the other (bidirectional removal)."""
    # Verify link is removed from memory1
    found_memory1_after = await _find_in_query(
        mcp_client,
        "Functional Programming Concepts",
        "verifying link removed after unlink",
        memory1_id,
    )
    assert found_memory1_after is not None
    assert memory2_id not in found_memory1_after["linked_memory_ids"], "Link should be removed from memory1"

    # Verify link is also removed from memory2 (bidirectional)
    found_memory2_after = await _find_in_query(
        mcp_client,
        "Mediterranean Cooking Tips",
        "verifying bidirectional link removal",
        memory2_id,
    )
    assert found_memory2_after is not None
    assert memory1_id not in found_memory2_after["linked_memory_ids"], "Link should be removed from memory2"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.e2e
async def test_link_memories_basic_e2e(mcp_client):
    """Test basic manual linking between two dissimilar memories"""
    result1 = await _create_memory(
        mcp_client,
        title="Machine Learning Basics",
        content="Machine learning is a subset of AI focused on pattern recognition",
        context="Testing manual linking between dissimilar memories",
        keywords=["machine-learning", "ai", "patterns"],
        tags=["ml", "basics"],
        importance=7,
    )
    assert result1.data is not None
    memory1_id = result1.data["id"]
    result2 = await _create_memory(
        mcp_client,
        title="CSS Grid Layout",
        content="CSS Grid provides a two-dimensional layout system for web design",
        context="Testing manual linking - dissimilar to ML memory",
        keywords=["css", "web", "layout"],
        tags=["frontend", "css"],
        importance=7,
    )
    assert result2.data is not None
    memory2_id = result2.data["id"]
    link_result = await _link_memories(mcp_client, memory1_id, [memory2_id])
    assert link_result.data is not None
    assert isinstance(link_result.data["linked_memory_ids"], list)
    assert memory2_id in link_result.data["linked_memory_ids"], f'Expected [{memory2_id}] but got {link_result.data["linked_memory_ids"]}'
    found_memory1 = await _find_in_query(
        mcp_client,
        "Machine Learning Basics",
        "verifying bidirectional link from memory1",
        memory1_id,
    )
    assert found_memory1 is not None
    assert memory2_id in found_memory1["linked_memory_ids"]
    found_memory2 = await _find_in_query(
        mcp_client,
        "CSS Grid Layout",
        "verifying bidirectional link from memory2",
        memory2_id,
    )
    assert found_memory2 is not None
    assert memory1_id in found_memory2["linked_memory_ids"]


@pytest.mark.e2e
async def test_link_memories_batch_e2e(mcp_client):
    """Test linking one memory to multiple targets in single call"""
    source_result = await _create_memory(
        mcp_client,
        title="Database Indexing Strategies",
        content="Indexes improve query performance but add write overhead",
        context="Testing batch linking - source memory",
        keywords=["database", "indexing", "performance"],
        tags=["database", "optimization"],
        importance=8,
    )
    assert source_result.data is not None
    source_id = source_result.data["id"]
    target1_result = await _create_memory(
        mcp_client,
        title="Observer Pattern",
        content="Observer pattern defines one-to-many dependency between objects",
        context="Testing batch linking - target 1",
        keywords=["design-patterns", "observer", "behavioral"],
        tags=["patterns", "oop"],
        importance=7,
    )
    assert target1_result.data is not None
    target1_id = target1_result.data["id"]
    target2_result = await _create_memory(
        mcp_client,
        title="CI/CD Pipeline Best Practices",
        content="Continuous integration and deployment automate software delivery",
        context="Testing batch linking - target 2",
        keywords=["cicd", "devops", "automation"],
        tags=["devops", "deployment"],
        importance=7,
    )
    assert target2_result.data is not None
    target2_id = target2_result.data["id"]
    target3_result = await _create_memory(
        mcp_client,
        title="OAuth 2.0 Flow",
        content="OAuth 2.0 provides delegated authorization framework",
        context="Testing batch linking - target 3",
        keywords=["oauth", "security", "authentication"],
        tags=["security", "auth"],
        importance=8,
    )
    assert target3_result.data is not None
    target3_id = target3_result.data["id"]
    link_result = await _link_memories(
        mcp_client, source_id, [target1_id, target2_id, target3_id],
    )
    _assert_batch_link_result(link_result, {target1_id, target2_id, target3_id})
    found_source = await _find_in_query(
        mcp_client,
        "Database Indexing Strategies",
        "verifying batch links from source",
        source_id,
    )
    assert found_source is not None
    assert target1_id in found_source["linked_memory_ids"]
    assert target2_id in found_source["linked_memory_ids"]
    assert target3_id in found_source["linked_memory_ids"]


@pytest.mark.e2e
async def test_link_memories_persistence_e2e(mcp_client):
    """Test that manual links persist to database across operations"""
    memory1_result = await _create_memory(
        mcp_client,
        title="Graph Algorithms",
        content="Dijkstra's algorithm finds shortest paths in weighted graphs",
        context="Testing link persistence - memory 1",
        keywords=["algorithms", "graphs", "dijkstra"],
        tags=["algorithms", "graphs"],
        importance=8,
    )
    assert memory1_result.data is not None
    memory1_id = memory1_result.data["id"]
    memory2_result = await _create_memory(
        mcp_client,
        title="Microservices Architecture",
        content="Microservices decompose applications into loosely coupled services",
        context="Testing link persistence - memory 2",
        keywords=["microservices", "architecture", "distributed"],
        tags=["architecture", "services"],
        importance=8,
    )
    assert memory2_result.data is not None
    memory2_id = memory2_result.data["id"]
    link_result = await _link_memories(mcp_client, memory1_id, [memory2_id])
    assert link_result.data is not None
    assert memory2_id in link_result.data["linked_memory_ids"], f'Expected [{memory2_id}] but got {link_result.data["linked_memory_ids"]}'
    found_memory1 = await _find_in_query(
        mcp_client,
        "Graph Algorithms",
        "verifying persistence of links",
        memory1_id,
    )
    assert found_memory1 is not None
    assert memory2_id in found_memory1["linked_memory_ids"], "Link should persist in memory1"
    found_memory2 = await _find_in_query(
        mcp_client,
        "Microservices Architecture",
        "verifying bidirectional persistence",
        memory2_id,
    )
    assert found_memory2 is not None
    assert memory1_id in found_memory2["linked_memory_ids"], "Bidirectional link should persist in memory2"


@pytest.mark.e2e
async def test_link_memories_partial_failure_e2e(mcp_client):
    """Test partial success when some target IDs are invalid"""
    source_result = await _create_memory(
        mcp_client,
        title="REST API Design",
        content="RESTful APIs use HTTP methods for CRUD operations",
        context="Testing partial failure handling - source",
        keywords=["rest", "api", "http"],
        tags=["api", "rest"],
        importance=7,
    )
    assert source_result.data is not None
    source_id = source_result.data["id"]
    target_result = await _create_memory(
        mcp_client,
        title="GraphQL Query Language",
        content="GraphQL provides a flexible query language for APIs",
        context="Testing partial failure handling - valid target",
        keywords=["graphql", "api", "queries"],
        tags=["api", "graphql"],
        importance=7,
    )
    assert target_result.data is not None
    valid_target_id = target_result.data["id"]
    link_result = await _link_memories(
        mcp_client, source_id, [valid_target_id, 999999],
    )
    assert link_result.data is not None
    assert isinstance(link_result.data["linked_memory_ids"], list)
    assert valid_target_id in link_result.data["linked_memory_ids"], f"Expected valid ID {valid_target_id} in result"
    assert 999999 not in link_result.data["linked_memory_ids"], "Invalid ID should be skipped"
    found_source = await _find_in_query(
        mcp_client,
        "REST API Design",
        "verifying partial success",
        source_id,
    )
    assert found_source is not None
    assert valid_target_id in found_source["linked_memory_ids"]
    assert 999999 not in found_source["linked_memory_ids"]


@pytest.mark.e2e
async def test_link_memories_duplicate_prevention_e2e(mcp_client):
    """Test that duplicate link attempts are handled gracefully"""
    memory1_result = await _create_memory(
        mcp_client,
        title="Binary Search Trees",
        content="BST is a data structure with ordered node arrangement",
        context="Testing duplicate prevention - memory 1",
        keywords=["bst", "trees", "data-structures"],
        tags=["data-structures", "trees"],
        importance=7,
    )
    assert memory1_result.data is not None
    memory1_id = memory1_result.data["id"]
    memory2_result = await _create_memory(
        mcp_client,
        title="Kubernetes Deployments",
        content="Kubernetes manages containerized application deployments",
        context="Testing duplicate prevention - memory 2",
        keywords=["kubernetes", "containers", "orchestration"],
        tags=["k8s", "devops"],
        importance=8,
    )
    assert memory2_result.data is not None
    memory2_id = memory2_result.data["id"]
    first_link_result = await _link_memories(mcp_client, memory1_id, [memory2_id])
    assert first_link_result.data is not None
    assert memory2_id in first_link_result.data["linked_memory_ids"], f'Expected [{memory2_id}] but got {first_link_result.data["linked_memory_ids"]}'
    second_link_result = await _link_memories(mcp_client, memory1_id, [memory2_id])
    assert second_link_result.data is not None
    assert isinstance(second_link_result.data["linked_memory_ids"], list)
    assert len(second_link_result.data["linked_memory_ids"],
        ) == 0, f'Expected empty list for duplicate, got {second_link_result.data["linked_memory_ids"]}'
    found_memory = await _find_in_query(
        mcp_client,
        "Binary Search Trees",
        "verifying no duplicate links",
        memory1_id,
    )
    assert found_memory is not None
    link_count = found_memory["linked_memory_ids"].count(memory2_id)
    assert link_count == 1, "Should have exactly one link, no duplicates"


@pytest.mark.e2e
async def test_link_memories_invalid_source_id_e2e(mcp_client):
    """Test error handling when source memory doesn't exist"""
    target_result = await mcp_client.call_tool("execute_forgetful_tool", {
        "tool_name": "create_memory", "arguments": {"title":
        "Test Target Memory", "content":
        "This is a valid target memory for error testing", "context":
        "Testing error handling with invalid source", "keywords": [
        "test", "error", "handling"], "tags": ["test"], "importance": 7}})
    assert target_result.data is not None
    target_id = target_result.data["id"]
    try:
        await mcp_client.call_tool("execute_forgetful_tool", {"tool_name":
            "link_memories", "arguments": {"memory_id": 999999,
            "related_ids": [target_id]}})
        assert False, "Expected ToolError for invalid source memory_id"
    except Exception as e:
        error_message = str(e)
        assert "not found" in error_message.lower(
            ) or "validation_error" in error_message.lower()


@pytest.mark.e2e
async def test_unlink_memories_basic_e2e(mcp_client):
    """Test unlinking memories removes bidirectional links"""
    # Create two dissimilar memories
    result1 = await _create_memory(
        mcp_client,
        title="Functional Programming Concepts",
        content="Pure functions and immutability are core FP principles",
        context="Testing unlink - dissimilar memory 1",
        keywords=["functional", "immutability", "pure"],
        tags=["programming", "fp"],
        importance=7,
    )
    assert result1.data is not None
    memory1_id = result1.data["id"]

    result2 = await _create_memory(
        mcp_client,
        title="Mediterranean Cooking Tips",
        content="Olive oil and fresh herbs are essential ingredients",
        context="Testing unlink - dissimilar memory 2",
        keywords=["cooking", "mediterranean", "herbs"],
        tags=["food", "recipes"],
        importance=7,
    )
    assert result2.data is not None
    memory2_id = result2.data["id"]

    # Link them
    link_result = await _link_memories(mcp_client, memory1_id, [memory2_id])
    assert link_result.data is not None
    assert memory2_id in link_result.data["linked_memory_ids"]

    await _assert_link_exists_before_unlink(mcp_client, memory1_id, memory2_id)

    # Unlink the memories
    unlink_result = await _call_tool(mcp_client, "unlink_memories", {
        "source_id": memory1_id, "target_id": memory2_id,
    })
    assert unlink_result.data is not None
    assert unlink_result.data["success"] is True, "Unlink should return success=True for existing link"

    await _assert_link_removed_both_ways(mcp_client, memory1_id, memory2_id)


@pytest.mark.e2e
async def test_unlink_memories_not_found_e2e(mcp_client):
    """Test unlinking non-existent link returns False"""
    # Create one memory
    result = await mcp_client.call_tool("execute_forgetful_tool", {
        "tool_name": "create_memory", "arguments": {"title":
        "Solo Memory For Unlink Test", "content":
        "This memory has no links to remove",
        "context": "Testing unlink not found",
        "keywords": ["solo", "test"], "tags": ["test"], "importance": 7}})
    assert result.data is not None
    memory_id = result.data["id"]

    # Try to unlink from non-existent memory
    unlink_result = await mcp_client.call_tool("execute_forgetful_tool", {
        "tool_name": "unlink_memories", "arguments": {"source_id":
        memory_id, "target_id": 999999}})
    assert unlink_result.data is not None
    assert unlink_result.data["success"] is False, "Unlink should return success=False for non-existent link"
