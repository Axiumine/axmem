"""End-to-end tests for MCP memory tools: get_recent_memories filters (PostgreSQL backend)

Covers the default newest-first listing, link ID parity with get_memory and the
project, obsolete and tag filters. Ordering, pagination and clamping live in the
sibling ``recent_ordering`` and ``recent_clamps`` modules.
"""
import uuid

import pytest

from tests.e2e.memory_tools_helpers_e2e import (
    call_tool,
    create_memory,
    get_memory,
    mark_obsolete,
    recent_envelope,
    recent_memories,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.e2e
async def test_get_recent_memories_basic_e2e(mcp_client):
    """Test getting recent memories sorted by creation timestamp"""
    # Create a unique project to isolate this test's memories
    project_name = "Recent Memories Basic Test Project"

    # Create several memories in this project
    memory_ids = []
    for i in range(5):
        result = await create_memory(
            mcp_client,
            title=f"Recent Memory Test {i}",
            content=f"Testing recent memories retrieval {i}",
            context=f"E2E test for get_recent_memories {i}",
            keywords=["recent", "test", f"memory{i}"],
            tags=["recent-basic-e2e-test"],
            importance=7,
            project=project_name,
        )
        memory_ids.append(result.data["id"])

    # Get recent memories filtered by project (should return newest first)
    recent_result = await recent_memories(
        mcp_client, limit=3, project=project_name,
    )

    assert recent_result.content is not None
    envelope = recent_envelope(recent_result)
    assert isinstance(envelope, dict)
    memories = envelope["memories"]
    assert len(memories) == 3
    assert envelope["total_count"] >= len(memories)

    # Check that most recent memories are returned (newest first)
    recent_ids = [m["id"] for m in memories]
    # The last 3 created memories should be the results (in reverse order)
    assert memory_ids[-1] in recent_ids
    assert memory_ids[-2] in recent_ids
    assert memory_ids[-3] in recent_ids


@pytest.mark.e2e
async def test_get_recent_memories_linked_ids_match_get_memory_e2e(mcp_client):
    """get_recent_memories linked_memory_ids must match get_memory for both link ends."""
    project_name = "recent-linked-ids-match-test-postgres"

    memory_a_result = await create_memory(
        mcp_client,
        title="Machine Learning Basics",
        content="Machine learning is a subset of AI focused on pattern recognition",
        context="Testing manual linking between dissimilar memories",
        keywords=["machine-learning", "ai", "patterns"],
        tags=["ml", "basics"],
        importance=7,
        project=project_name,
    )
    memory_a_id = memory_a_result.data["id"]

    memory_b_result = await create_memory(
        mcp_client,
        title="CSS Grid Layout",
        content="CSS Grid provides a two-dimensional layout system for web design",
        context="Testing manual linking - dissimilar to ML memory",
        keywords=["css", "web", "layout"],
        tags=["frontend", "css"],
        importance=7,
        project=project_name,
    )
    memory_b_id = memory_b_result.data["id"]

    await call_tool(
        mcp_client, "link_memories", memory_id=memory_a_id, related_ids=[memory_b_id],
    )

    get_a_result = await get_memory(mcp_client, memory_a_id)
    get_b_result = await get_memory(mcp_client, memory_b_id)
    assert memory_b_id in get_a_result.data["linked_memory_ids"]
    assert memory_a_id in get_b_result.data["linked_memory_ids"]

    recent_result = await recent_memories(
        mcp_client, limit=20, project=project_name,
    )
    envelope = recent_envelope(recent_result)
    memories = envelope["memories"]
    assert envelope["total_count"] >= len(memories)

    recent_by_id = {m["id"]: m for m in memories}
    recent_a = recent_by_id[memory_a_id]
    recent_b = recent_by_id[memory_b_id]

    assert set(recent_a["linked_memory_ids"]) == set(get_a_result.data["linked_memory_ids"])
    assert set(recent_b["linked_memory_ids"]) == set(get_b_result.data["linked_memory_ids"])
    assert memory_b_id in recent_a["linked_memory_ids"]
    assert memory_a_id in recent_b["linked_memory_ids"]


@pytest.mark.e2e
async def test_get_recent_memories_with_project_filter_e2e(mcp_client):
    """Test getting recent memories filtered by project"""
    # Create a project
    project_name = "recent-memories-project-filter-test"

    # Create memories with and without the project
    with_project_result = await create_memory(
        mcp_client,
        title="Memory With Project",
        content="This memory belongs to the test project",
        context="Testing project filtering in get_recent_memories",
        keywords=["project", "filter", "test"],
        tags=["test"],
        importance=7,
        project=project_name,
    )
    memory_with_project_id = with_project_result.data["id"]

    without_project_result = await create_memory(
        mcp_client,
        title="Memory Without Project",
        content="This memory does not belong to any project",
        context="Testing project filtering exclusion",
        keywords=["no-project", "test"],
        tags=["test"],
        importance=7,
    )
    memory_without_project_id = without_project_result.data["id"]

    # Get recent memories filtered by project
    recent_result = await recent_memories(
        mcp_client, limit=10, project=project_name,
    )

    assert recent_result.content is not None
    envelope = recent_envelope(recent_result)
    memories = envelope["memories"]
    recent_ids = [m["id"] for m in memories]

    # Should include memory with project
    assert memory_with_project_id in recent_ids
    # Should NOT include memory without project
    assert memory_without_project_id not in recent_ids


@pytest.mark.e2e
async def test_get_recent_memories_excludes_obsolete_e2e(mcp_client):
    """Test that get_recent_memories excludes obsolete memories"""
    # Create an active memory
    active_result = await create_memory(
        mcp_client,
        title="Active Memory for Recent Test",
        content="This memory should appear in recent results",
        context="Testing obsolete exclusion",
        keywords=["active", "recent", "test"],
        tags=["test", "active"],
        importance=7,
    )
    active_memory_id = active_result.data["id"]

    # Create a memory to be marked obsolete
    obsolete_result = await create_memory(
        mcp_client,
        title="Memory To Be Obsolete",
        content="This memory will be marked obsolete",
        context="Testing obsolete filtering",
        keywords=["obsolete", "test"],
        tags=["test", "obsolete"],
        importance=7,
    )
    obsolete_memory_id = obsolete_result.data["id"]

    # Mark the second memory as obsolete
    await mark_obsolete(
        mcp_client,
        obsolete_memory_id,
        "Testing obsolete filtering in get_recent_memories",
    )

    # Get recent memories - should exclude obsolete
    recent_result = await recent_memories(mcp_client, limit=10)

    assert recent_result.content is not None
    envelope = recent_envelope(recent_result)
    memories = envelope["memories"]
    recent_ids = [m["id"] for m in memories]

    # Should include active memory
    assert active_memory_id in recent_ids
    # Should NOT include obsolete memory
    assert obsolete_memory_id not in recent_ids


@pytest.mark.e2e
async def test_get_recent_memories_with_tags_filter_e2e(mcp_client):
    """Test get_recent_memories filters by tags (OR semantics)"""
    tag_a = "e2e-recent-tag-a"
    tag_b = "e2e-recent-tag-b"

    tagged_a = await create_memory(
        mcp_client,
        title="E2E recent tag filter A",
        content="Memory with tag A only",
        context="Testing tag filter on get_recent_memories",
        keywords=["e2e", "tag-filter"],
        tags=[tag_a, "test"],
        importance=7,
    )
    tagged_b = await create_memory(
        mcp_client,
        title="E2E recent tag filter B",
        content="Memory with tag B only",
        context="Testing tag filter on get_recent_memories",
        keywords=["e2e", "tag-filter"],
        tags=[tag_b, "test"],
        importance=7,
    )
    untagged = await create_memory(
        mcp_client,
        title="E2E recent tag filter none",
        content="Memory without target tags",
        context="Testing tag filter on get_recent_memories",
        keywords=["e2e", "tag-filter"],
        tags=["test"],
        importance=7,
    )

    id_a = tagged_a.data["id"]
    id_b = tagged_b.data["id"]
    id_none = untagged.data["id"]

    single_tag_result = await recent_memories(mcp_client, tags=[tag_a], limit=50)
    single_ids = {m["id"] for m in recent_envelope(single_tag_result)["memories"]}
    assert id_a in single_ids
    assert id_b not in single_ids
    assert id_none not in single_ids

    multi_tag_result = await recent_memories(mcp_client, tags=[tag_a, tag_b], limit=50)
    multi_ids = {m["id"] for m in recent_envelope(multi_tag_result)["memories"]}
    assert id_a in multi_ids
    assert id_b in multi_ids
    assert id_none not in multi_ids


@pytest.mark.e2e
async def test_get_recent_memories_tags_with_project_e2e(mcp_client):
    """Test combined tags + project filter on get_recent_memories"""
    project_name = f"e2e-recent-tags-project-{uuid.uuid4().hex[:8]}"
    tag = f"e2e-recent-project-tag-{uuid.uuid4().hex[:8]}"

    in_scope = await create_memory(
        mcp_client,
        title="E2E recent project+tag in scope",
        content="Matches project and tag",
        context="Testing combined filters",
        keywords=["e2e", "project-tag"],
        tags=[tag, "test"],
        importance=7,
        project=project_name,
    )
    wrong_project = await create_memory(
        mcp_client,
        title="E2E recent project+tag wrong project",
        content="Has tag but different project",
        context="Testing combined filters",
        keywords=["e2e", "project-tag"],
        tags=[tag, "test"],
        importance=7,
    )

    in_scope_id = in_scope.data["id"]
    wrong_project_id = wrong_project.data["id"]

    filtered = await recent_memories(
        mcp_client, tags=[tag], project=project_name, limit=50,
    )
    filtered_ids = {m["id"] for m in recent_envelope(filtered)["memories"]}
    assert in_scope_id in filtered_ids
    assert wrong_project_id not in filtered_ids
