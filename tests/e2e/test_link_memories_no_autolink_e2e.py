"""E2E tests for link_memories tool with auto-linking disabled.

These tests verify manual linking functionality in isolation by disabling
the auto-linking feature (MEMORY_NUM_AUTO_LINK=0) to ensure we're testing
manual linking, not auto-linking side effects.
"""
import pytest
from rest_helpers import MISSING_ID

DOCKER_ENV_OVERRIDE = {"MEMORY_NUM_AUTO_LINK": "0"}


@pytest.fixture(autouse=True)
def disable_auto_linking():
    """Disable auto-linking for these tests"""
    from app.config.settings import settings
    original_value = settings.MEMORY_NUM_AUTO_LINK
    settings.MEMORY_NUM_AUTO_LINK = 0
    yield
    settings.MEMORY_NUM_AUTO_LINK = original_value


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


async def _hub_links(mcp_client, memory_id):
    """Return the linked_memory_ids reported by get_memory for memory_id."""
    result = await _call_tool(mcp_client, "get_memory", {"memory_id": memory_id})
    return result.data["linked_memory_ids"]


async def _unlink_succeeded(mcp_client, arguments):
    """Call unlink_memories and return its success flag."""
    result = await _call_tool(mcp_client, "unlink_memories", arguments)
    return result.data["success"]


async def _create_alias_memories(mcp_client):
    """Create the four memories used by the alias test and return their ids."""
    ordinals = ["First", "Second", "Third", "Fourth"]
    results = [
        await _create_memory(
            mcp_client,
            title=f"Alias Test Memory {number}",
            content=f"{ordinal} memory for testing parameter aliases",
            context="Testing FF-29 parameter resilience",
            keywords=["test", "alias", f"m{number}"],
            tags=["test"],
            importance=7,
        )
        for number, ordinal in enumerate(ordinals, start=1)
    ]
    return [result.data["id"] for result in results]


async def _create_unlink_memories(mcp_client):
    """Create the hub and three targets used by the multi-unlink test."""
    specs = [
        ("Multi-unlink Hub", "Hub memory linked to multiple targets", ["hub", "test"]),
        ("Target 1", "First target", ["t1", "test"]),
        ("Target 2", "Second target", ["t2", "test"]),
        ("Target 3", "Third target", ["t3", "test"]),
    ]
    results = [
        await _create_memory(
            mcp_client,
            title=title,
            content=content,
            context="Testing multi-target unlinking",
            keywords=keywords,
            tags=["test"],
            importance=7,
        )
        for title, content, keywords in specs
    ]
    return [result.data["id"] for result in results]


# Step helpers for test_link_memories_batch_e2e

def _assert_batch_link_result(link_result, expected_ids):
    """The batch link call reports exactly the requested targets as a list."""
    assert link_result.data is not None
    assert isinstance(link_result.data["linked_memory_ids"], list)
    assert set(link_result.data["linked_memory_ids"]) == expected_ids, (
        f'Expected all 3 IDs, got {link_result.data["linked_memory_ids"]}'
    )


# Step helpers for test_link_memories_aliases_e2e

async def _assert_link_alias_forms(mcp_client, ids):
    """Every accepted argument spelling of link_memories links the targets."""
    m1_id, m2_id, m3_id, m4_id = ids

    # 1. Test source_id + target_id (scalar int)
    link1 = await _call_tool(mcp_client, "link_memories", {
        "source_id": m1_id, "target_id": m2_id,
    })
    assert link1.data is not None
    assert m2_id in link1.data["linked_memory_ids"]

    # 2. Test memory_id + related_id (scalar int)
    link2 = await _call_tool(mcp_client, "link_memories", {
        "memory_id": m1_id, "related_id": m3_id,
    })
    assert link2.data is not None
    assert m3_id in link2.data["linked_memory_ids"]

    # 3. Test memory_ids list [src, tgt]
    link3 = await _call_tool(mcp_client, "link_memories", {
        "memory_ids": [m1_id, m4_id],
    })
    assert link3.data is not None
    assert m4_id in link3.data["linked_memory_ids"]

    # 4. Test source_id + target_ids list
    link4 = await _call_tool(mcp_client, "link_memories", {
        "source_id": m2_id, "target_ids": [m3_id, m4_id],
    })
    assert link4.data is not None
    assert set(link4.data["linked_memory_ids"]) == {m3_id, m4_id}


async def _assert_id_alias_reads_and_updates(mcp_client, ids):
    """get_memory and update_memory accept the "id" alias for memory_id."""
    m1_id, m2_id, m3_id, m4_id = ids

    # Verify links via get_memory using "id" alias
    get_m1 = await _call_tool(mcp_client, "get_memory", {"id": m1_id})
    assert get_m1.data is not None
    assert m2_id in get_m1.data["linked_memory_ids"]
    assert m3_id in get_m1.data["linked_memory_ids"]
    assert m4_id in get_m1.data["linked_memory_ids"]

    # Test update_memory using "id" alias
    up_m1 = await _call_tool(mcp_client, "update_memory", {
        "id": m1_id, "content": "Updated content via id alias",
    })
    assert up_m1.data is not None
    assert up_m1.data["content"] == "Updated content via id alias"


async def _assert_unlink_alias_forms(mcp_client, ids):
    """unlink_memories accepts its alias spellings and removes only those links."""
    m1_id, m2_id, m3_id, m4_id = ids

    # Test unlink_memories using memory_id + related_id
    unlink1 = await _call_tool(mcp_client, "unlink_memories", {
        "memory_id": m1_id, "related_id": m2_id,
    })
    assert unlink1.data is not None
    assert unlink1.data["success"] is True

    # Test unlink_memories using memory_ids list
    unlink2 = await _call_tool(mcp_client, "unlink_memories", {
        "memory_ids": [m1_id, m3_id],
    })
    assert unlink2.data is not None
    assert unlink2.data["success"] is True

    # Verify unlinking on m1
    get_m1_after = await _call_tool(mcp_client, "get_memory", {"memory_id": m1_id})
    assert m2_id not in get_m1_after.data["linked_memory_ids"]
    assert m3_id not in get_m1_after.data["linked_memory_ids"]
    assert m4_id in get_m1_after.data["linked_memory_ids"]


# Step helpers for test_unlink_memories_multi_target_e2e

async def _assert_unlink_pair_via_related_ids(mcp_client, ids):
    """Link a hub to three targets, then unlink two of them via related_ids."""
    m1, m2, m3, m4 = ids

    # Link m1 to [m2, m3, m4]
    link_res = await _call_tool(mcp_client, "link_memories", {
        "memory_id": m1, "related_ids": [m2, m3, m4],
    })
    assert set(link_res.data["linked_memory_ids"]) == {m2, m3, m4}

    # Verify all 3 are linked
    hub_state = await _hub_links(mcp_client, m1)
    assert set(hub_state) == {m2, m3, m4}

    # Unlink m2 AND m3 in a single call via related_ids=[m2, m3]
    assert await _unlink_succeeded(mcp_client, {
        "memory_id": m1, "related_ids": [m2, m3],
    }) is True

    # Verify: m2 and m3 are unlinked, m4 MUST remain linked (no partial silent discard)
    hub_state_after = await _hub_links(mcp_client, m1)
    assert m2 not in hub_state_after
    assert m3 not in hub_state_after
    assert m4 in hub_state_after


async def _assert_unlink_pair_via_target_ids_alias(mcp_client, ids):
    """Re-link with memory_ids, then unlink two targets via the target_ids alias."""
    m1, m2, m3, m4 = ids

    # Re-link m2 and m3 to test memory_ids list syntax with 3+ items [src, t1, t2, ...]
    await _call_tool(mcp_client, "link_memories", {"memory_ids": [m1, m2, m3]})
    hub_relinked = await _hub_links(mcp_client, m1)
    assert set(hub_relinked) == {m2, m3, m4}

    # Unlink m2 AND m3 through the target_ids alias, m4 must survive
    assert await _unlink_succeeded(mcp_client, {
        "memory_id": m1, "target_ids": [m2, m3],
    }) is True

    hub_after_alias = await _hub_links(mcp_client, m1)
    assert m2 not in hub_after_alias
    assert m3 not in hub_after_alias
    assert m4 in hub_after_alias


async def _assert_unlink_all_via_memory_ids(mcp_client, ids):
    """Re-link two targets, then unlink every link with one memory_ids call."""
    m1, m2, m3, m4 = ids

    # Re-link m2 and m3 before the final full unlink
    await _call_tool(mcp_client, "link_memories", {"memory_ids": [m1, m2, m3]})

    # Unlink all via memory_ids=[m1, m2, m3, m4]
    assert await _unlink_succeeded(mcp_client, {
        "memory_ids": [m1, m2, m3, m4],
    }) is True

    # Verify no links remain
    hub_final = await _hub_links(mcp_client, m1)
    assert hub_final == []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
        mcp_client, source_id, [valid_target_id, MISSING_ID],
    )
    assert link_result.data is not None
    assert isinstance(link_result.data["linked_memory_ids"], list)
    assert valid_target_id in link_result.data["linked_memory_ids"], f"Expected valid ID {valid_target_id} in result"
    assert MISSING_ID not in link_result.data["linked_memory_ids"], "Invalid ID should be skipped"
    found_source = await _find_in_query(
        mcp_client,
        "REST API Design",
        "verifying partial success",
        source_id,
    )
    assert found_source is not None
    assert valid_target_id in found_source["linked_memory_ids"]
    assert MISSING_ID not in found_source["linked_memory_ids"]


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
            "link_memories", "arguments": {"memory_id": MISSING_ID,
            "related_ids": [target_id]}})
        assert False, "Expected ToolError for invalid source memory_id"
    except Exception as e:
        error_message = str(e)
        assert "not found" in error_message.lower(
            ) or "validation_error" in error_message.lower()


@pytest.mark.asyncio
async def test_link_memories_aliases_e2e(mcp_client):
    """Test all alias variations for link_memories and unlink_memories (FF-29)."""
    ids = await _create_alias_memories(mcp_client)
    await _assert_link_alias_forms(mcp_client, ids)
    await _assert_id_alias_reads_and_updates(mcp_client, ids)
    await _assert_unlink_alias_forms(mcp_client, ids)


@pytest.mark.asyncio
async def test_reject_numeric_and_non_uuid_ids_e2e(mcp_client):
    """Regression test: numbers (whole or fractional) and booleans are not memory ids and must be rejected."""
    # Create an initial memory that should NOT be modified by fractional IDs
    create_res = await mcp_client.call_tool("execute_forgetful_tool", {
        "tool_name": "create_memory",
        "arguments": {
            "title": "Immutable Target Memory",
            "content": "Original untouched content",
            "context": "Testing fractional ID rejection",
            "keywords": ["test", "fractional", "id"],
            "tags": ["test"],
            "importance": 6,
        },
    })
    target_id = create_res.data["id"]
    fractional_id = 1.9

    # 1. update_memory with a numeric ID (e.g. 1.9) must fail and NOT modify target_id
    with pytest.raises(Exception) as exc_info:
        await mcp_client.call_tool("execute_forgetful_tool", {
            "tool_name": "update_memory",
            "arguments": {"memory_id": fractional_id, "content": "Tampered content"},
        })
    assert "uuid" in str(exc_info.value).lower()

    # Verify content was not modified
    get_res = await mcp_client.call_tool("execute_forgetful_tool", {
        "tool_name": "get_memory",
        "arguments": {"memory_id": target_id},
    })
    assert get_res.data["content"] == "Original untouched content"

    # 2. update_memory with boolean ID (True) must fail
    with pytest.raises(Exception):
        await mcp_client.call_tool("execute_forgetful_tool", {
            "tool_name": "update_memory",
            "arguments": {"memory_id": True, "content": "Tampered via bool"},
        })

    # 3. get_memory with fractional ID must fail
    with pytest.raises(Exception):
        await mcp_client.call_tool("execute_forgetful_tool", {
            "tool_name": "get_memory",
            "arguments": {"memory_id": fractional_id},
        })

    # 4. mark_memory_obsolete with fractional ID must fail
    with pytest.raises(Exception):
        await mcp_client.call_tool("execute_forgetful_tool", {
            "tool_name": "mark_memory_obsolete",
            "arguments": {"memory_id": fractional_id, "reason": "Testing rejection"},
        })

    # 5. link_memories with fractional source ID or target ID must fail
    with pytest.raises(Exception):
        await mcp_client.call_tool("execute_forgetful_tool", {
            "tool_name": "link_memories",
            "arguments": {"source_id": fractional_id, "target_id": target_id},
        })
    with pytest.raises(Exception):
        await mcp_client.call_tool("execute_forgetful_tool", {
            "tool_name": "link_memories",
            "arguments": {"source_id": target_id, "related_ids": [fractional_id]},
        })

    # 6. unlink_memories with fractional source ID or target ID must fail
    with pytest.raises(Exception):
        await mcp_client.call_tool("execute_forgetful_tool", {
            "tool_name": "unlink_memories",
            "arguments": {"source_id": fractional_id, "target_id": target_id},
        })
    with pytest.raises(Exception):
        await mcp_client.call_tool("execute_forgetful_tool", {
            "tool_name": "unlink_memories",
            "arguments": {"source_id": target_id, "target_id": fractional_id},
        })


@pytest.mark.asyncio
async def test_unlink_memories_multi_target_e2e(mcp_client):
    """Regression test: unlink_memories must process all requested targets (P2)."""
    ids = await _create_unlink_memories(mcp_client)
    await _assert_unlink_pair_via_related_ids(mcp_client, ids)
    await _assert_unlink_pair_via_target_ids_alias(mcp_client, ids)
    await _assert_unlink_all_via_memory_ids(mcp_client, ids)


@pytest.mark.asyncio
async def test_unlink_memories_reports_failure_when_one_target_was_never_linked_e2e(mcp_client):
    """A target without a link makes the whole call report failure, and the links that exist are still removed."""
    ids = await _create_unlink_memories(mcp_client)
    hub, linked, never_linked = ids[0], ids[1], ids[2]
    await _link_memories(mcp_client, hub, [linked])

    assert await _unlink_succeeded(mcp_client, {"source_id": hub, "target_ids": [linked, never_linked]}) is False

    assert await _hub_links(mcp_client, hub) == []
