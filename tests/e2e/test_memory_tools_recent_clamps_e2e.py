"""End-to-end tests for MCP memory tools: get_recent_memories clamping (PostgreSQL backend)

Covers pagination clamping on the adapter path: negative offsets, zero and
oversized limits and the hard 100-row cap.
"""
import pytest

from tests.e2e.memory_tools_helpers_e2e import (
    create_numbered_memories,
    recent_envelope,
    recent_memories,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.e2e
async def test_get_recent_memories_clamps_negative_offset_e2e(mcp_client):
    """Test that negative offset is clamped to 0 on the adapter path"""
    tag = "e2e-clamp-offset"
    await create_numbered_memories(
        mcp_client, 3, "E2E clamp offset {i}",
        "Memory {i} for negative offset clamp test", "Testing offset clamp",
        ["e2e", "clamp", "offset"], tag,
    )

    result = await recent_memories(mcp_client, offset=-1, limit=10, tags=[tag])

    assert result.is_error is False
    envelope = recent_envelope(result)
    memories = envelope["memories"]
    assert len(memories) == 3


@pytest.mark.e2e
async def test_get_recent_memories_clamps_over_limit_e2e(mcp_client):
    """Smoke-tests that an absurdly large limit does not error or hang the adapter path.

    Does not assert the 100-row cap directly (fixture volume is intentionally small);
    the numeric clamp itself is exercised by the negative-offset and zero-limit cases
    via the same clamp_list_pagination call.
    """
    tag = "e2e-clamp-limit"
    await create_numbered_memories(
        mcp_client, 2, "E2E clamp limit {i}",
        "Memory {i} for over-limit clamp test", "Testing limit clamp",
        ["e2e", "clamp", "limit"], tag,
    )

    result = await recent_memories(mcp_client, limit=1_000_000, tags=[tag])

    assert result.is_error is False
    envelope = recent_envelope(result)
    memories = envelope["memories"]
    assert len(memories) == 2
    assert len(memories) <= 100
    assert envelope["total_count"] >= len(memories)


@pytest.mark.e2e
async def test_get_recent_memories_clamps_zero_limit_e2e(mcp_client):
    """Test that limit=0 is clamped to 1 on the adapter path"""
    tag = "e2e-clamp-zero"
    await create_numbered_memories(
        mcp_client, 3, "E2E clamp zero {i}",
        "Memory {i} for zero limit clamp test", "Testing zero limit clamp",
        ["e2e", "clamp", "zero"], tag,
    )

    result = await recent_memories(mcp_client, limit=0, tags=[tag])

    assert result.is_error is False
    envelope = recent_envelope(result)
    memories = envelope["memories"]
    assert len(memories) == 1


@pytest.mark.e2e
async def test_get_recent_memories_clamps_hundred_cap_e2e(mcp_client):
    """Test that an over-limit request returns at most 100 rows when more exist"""
    tag = "e2e-clamp-cap"
    await create_numbered_memories(
        mcp_client, 101, "E2E clamp cap {i}",
        "Memory {i} for hundred cap test", "Testing 100-row cap",
        ["e2e", "clamp", "cap"], tag,
    )

    result = await recent_memories(mcp_client, limit=1_000_000, tags=[tag])

    assert result.is_error is False
    envelope = recent_envelope(result)
    memories = envelope["memories"]
    assert len(memories) == 100
    assert envelope["total_count"] >= 101
