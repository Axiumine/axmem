"""End-to-end tests for scoped permissions via meta-tools.

Tests that FORGETFUL_SCOPES correctly restricts tool discovery and execution.
Uses the scoped_mcp_client fixture from conftest.py which parameterizes over
different scope configurations.
"""
import pytest

from app.routes.mcp.scope_resolver import parse_scopes, resolve_permitted_tools

# Tools discoverable with the default wildcard scope: the floor a restricted scope must stay below.
ALL_TOOLS_COUNT = 10

# ============================================================================
# Default wildcard scope - regression (all tools accessible)
# ============================================================================

@pytest.mark.asyncio
async def test_default_wildcard_all_tools_discoverable(mcp_client):
    """With default * scope, all tools should be discoverable (regression test)."""
    result = await mcp_client.call_tool("discover_forgetful_tools", {})
    assert result.data is not None
    assert result.data["total_count"] >= ALL_TOOLS_COUNT


@pytest.mark.asyncio
async def test_default_wildcard_execute_read_tool(mcp_client):
    """With default * scope, read tools should work."""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_recent_memories", "arguments": {}},
    )
    assert result.data is not None


@pytest.mark.asyncio
async def test_default_wildcard_execute_write_tool(mcp_client):
    """With default * scope, write tools should work."""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": "Scope test memory",
                "content": "Testing write access with wildcard scope",
                "context": "E2E scope test",
                "keywords": ["test"],
                "tags": ["test"],
                "importance": 5,
            },
        },
    )
    assert result.data is not None


# ============================================================================
# Read-only scope
# ============================================================================

@pytest.mark.asyncio
async def test_read_only_scope_discover_only_reads(mcp_client, qdrant_app):
    """With read scope, only non-mutating tools should be discoverable."""
    # Override scopes to read-only
    instance_scopes = parse_scopes("read")
    qdrant_app._instance_permitted_tools = resolve_permitted_tools(instance_scopes, qdrant_app.registry)
    qdrant_app._instance_scopes = instance_scopes

    result = await mcp_client.call_tool("discover_forgetful_tools", {})
    assert result.data is not None

    # All discovered tools should be non-mutating
    for cat_tools in result.data["tools_by_category"].values():
        for tool in cat_tools:
            assert tool["mutates"] is False, f"Tool {tool['name']} is mutating but was discovered with read scope"

    # Should have fewer tools than wildcard
    assert result.data["total_count"] < ALL_TOOLS_COUNT


@pytest.mark.asyncio
async def test_read_only_scope_execute_read_succeeds(mcp_client, qdrant_app):
    """With read scope, executing a read tool should succeed."""
    instance_scopes = parse_scopes("read")
    qdrant_app._instance_permitted_tools = resolve_permitted_tools(instance_scopes, qdrant_app.registry)
    qdrant_app._instance_scopes = instance_scopes

    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_recent_memories", "arguments": {}},
    )
    assert result.data is not None


@pytest.mark.asyncio
async def test_read_only_scope_execute_write_denied(mcp_client, qdrant_app):
    """With read scope, executing a write tool should be denied."""
    instance_scopes = parse_scopes("read")
    qdrant_app._instance_permitted_tools = resolve_permitted_tools(instance_scopes, qdrant_app.registry)
    qdrant_app._instance_scopes = instance_scopes

    with pytest.raises(Exception) as exc_info:
        await mcp_client.call_tool(
            "execute_forgetful_tool",
            {
                "tool_name": "create_memory",
                "arguments": {
                    "title": "Should fail",
                    "content": "Should not be created",
                    "context": "Test",
                    "keywords": ["test"],
                    "tags": ["test"],
                    "importance": 5,
                },
            },
        )
    assert "not permitted" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_read_only_scope_how_to_use_write_denied(mcp_client, qdrant_app):
    """With read scope, how_to_use on a write tool should be denied."""
    instance_scopes = parse_scopes("read")
    qdrant_app._instance_permitted_tools = resolve_permitted_tools(instance_scopes, qdrant_app.registry)
    qdrant_app._instance_scopes = instance_scopes

    with pytest.raises(Exception) as exc_info:
        await mcp_client.call_tool(
            "how_to_use_forgetful_tool",
            {"tool_name": "create_memory"},
        )
    assert "not permitted" in str(exc_info.value).lower()


# ============================================================================
# Category-scoped: read:memories
# ============================================================================

@pytest.mark.asyncio
async def test_read_memories_scope_discover(mcp_client, qdrant_app):
    """With read:memories scope, only memory read tools should be visible."""
    instance_scopes = parse_scopes("read:memories")
    qdrant_app._instance_permitted_tools = resolve_permitted_tools(instance_scopes, qdrant_app.registry)
    qdrant_app._instance_scopes = instance_scopes

    result = await mcp_client.call_tool("discover_forgetful_tools", {})
    assert result.data is not None

    # Only the memory category exists, and it is present
    assert result.data["categories_available"] == ["memory"]

    # All discovered tools should be memory reads
    all_tools = []
    for cat_tools in result.data["tools_by_category"].values():
        all_tools.extend(cat_tools)

    for tool in all_tools:
        assert tool["category"] == "memory"
        assert tool["mutates"] is False


@pytest.mark.asyncio
async def test_read_memories_scope_execute_memory_read(mcp_client, qdrant_app):
    """With read:memories, executing get_memory-like reads should work (not raise ToolError)."""
    instance_scopes = parse_scopes("read:memories")
    qdrant_app._instance_permitted_tools = resolve_permitted_tools(instance_scopes, qdrant_app.registry)
    qdrant_app._instance_scopes = instance_scopes

    # get_recent_memories should succeed (no permission error)
    # Result may be an empty envelope (no memories exist), so we just check no exception
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_recent_memories", "arguments": {"limit": 5}},
    )
    assert result.is_error is False


@pytest.mark.asyncio
async def test_read_memories_scope_execute_memory_write_denied(mcp_client, qdrant_app):
    """With read:memories, executing a memory write tool should be denied."""
    instance_scopes = parse_scopes("read:memories")
    qdrant_app._instance_permitted_tools = resolve_permitted_tools(instance_scopes, qdrant_app.registry)
    qdrant_app._instance_scopes = instance_scopes

    with pytest.raises(Exception) as exc_info:
        await mcp_client.call_tool(
            "execute_forgetful_tool",
            {"tool_name": "mark_memory_obsolete", "arguments": {"memory_id": 1, "reason": "r"}},
        )
    assert "not permitted" in str(exc_info.value).lower()


# ============================================================================
# Mixed scopes: read,write:memories
# ============================================================================

@pytest.mark.asyncio
async def test_mixed_scopes_all_reads_plus_memory_writes(mcp_client, qdrant_app):
    """With read,write:memories, all reads + memory writes should be accessible."""
    instance_scopes = parse_scopes("read,write:memories")
    qdrant_app._instance_permitted_tools = resolve_permitted_tools(instance_scopes, qdrant_app.registry)
    qdrant_app._instance_scopes = instance_scopes

    result = await mcp_client.call_tool("discover_forgetful_tools", {})
    assert result.data is not None

    assert result.data["categories_available"] == ["memory"]

    # Check that memory category has both reads and writes
    memory_result = await mcp_client.call_tool(
        "discover_forgetful_tools", {"category": "memory"},
    )
    memory_tools = memory_result.data["tools_by_category"]["memory"]
    memory_names = {t["name"] for t in memory_tools}

    # Memory reads should be present
    assert "query_memory" in memory_names
    assert "get_memory" in memory_names
    # Memory writes should be present
    assert "create_memory" in memory_names
    assert "update_memory" in memory_names


@pytest.mark.asyncio
async def test_mixed_scopes_execute_memory_write(mcp_client, qdrant_app):
    """With read,write:memories, memory writes should work."""
    instance_scopes = parse_scopes("read,write:memories")
    qdrant_app._instance_permitted_tools = resolve_permitted_tools(instance_scopes, qdrant_app.registry)
    qdrant_app._instance_scopes = instance_scopes

    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": "Mixed scope test",
                "content": "Testing write with mixed scopes",
                "context": "Scope test",
                "keywords": ["test"],
                "tags": ["test"],
                "importance": 5,
            },
        },
    )
    assert result.data is not None


@pytest.mark.asyncio
async def test_write_only_scope_denies_reads(mcp_client, qdrant_app):
    """With write:memories alone, read tools should be denied."""
    instance_scopes = parse_scopes("write:memories")
    qdrant_app._instance_permitted_tools = resolve_permitted_tools(instance_scopes, qdrant_app.registry)
    qdrant_app._instance_scopes = instance_scopes

    with pytest.raises(Exception) as exc_info:
        await mcp_client.call_tool(
            "execute_forgetful_tool",
            {"tool_name": "get_recent_memories", "arguments": {}},
        )
    assert "not permitted" in str(exc_info.value).lower()


# ============================================================================
# Discovery dict includes mutates field
# ============================================================================

@pytest.mark.asyncio
async def test_discovery_includes_mutates_field(mcp_client):
    """Verify that tool discovery output includes the mutates field."""
    result = await mcp_client.call_tool(
        "discover_forgetful_tools",
        {"category": "memory"},
    )
    assert result.data is not None
    memory_tools = result.data["tools_by_category"]["memory"]
    for tool in memory_tools:
        assert "mutates" in tool, f"Tool {tool['name']} missing mutates field"
        assert isinstance(tool["mutates"], bool)

    # Check specific tools
    tool_map = {t["name"]: t for t in memory_tools}
    assert tool_map["query_memory"]["mutates"] is False
    assert tool_map["create_memory"]["mutates"] is True
