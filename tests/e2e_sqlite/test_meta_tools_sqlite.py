"""End-to-end tests for MCP meta-tools via HTTP

Requires:
- PostgreSQL running in Docker
- MCP server running on configured port
- Registry initialized with tools

Tests the complete meta-tools pattern:
HTTP → FastMCP Client → MCP Protocol → Meta-tools → Registry → Adapters → Services
"""
import pytest


def _has_field(data, name):
    """True when a tool payload exposes name as an attribute or as a key."""
    return hasattr(data, name) or name in data


def _find_tool(tools, name):
    """Return the tool entry called name from a discovery listing, or None."""
    return next((tool for tool in tools if tool["name"] == name), None)


def _assert_discovery_payload_keys(data):
    """A discovery response carries the listing, the total and the categories."""
    assert "tools_by_category" in data
    assert "total_count" in data
    assert "categories_available" in data


def _assert_tool_entry(tool, category):
    """A discovery entry exposes the full tool structure for the category."""
    assert "name" in tool
    assert "category" in tool
    assert "description" in tool
    assert "parameters" in tool
    assert "returns" in tool
    assert tool["category"] == category


def _assert_tool_doc_fields(data):
    """A how_to_use response carries the full documentation fields."""
    assert "description" in data
    assert "parameters" in data
    assert "returns" in data
    assert "examples" in data
    assert "tags" in data
    assert "json_schema" in data


@pytest.mark.asyncio
async def test_discover_forgetful_tools_all_e2e(mcp_client):
    """Test discovering all tools without category filter"""
    result = await mcp_client.call_tool("discover_forgetful_tools", {})

    assert result.data is not None
    _assert_discovery_payload_keys(result.data)

    # The memory category is the only one left
    assert result.data["categories_available"] == ["memory"]
    assert len(result.data["tools_by_category"]["memory"]) >= 6

    # Total count should match sum of all categories
    total = sum(len(tools) for tools in result.data["tools_by_category"].values())
    assert result.data["total_count"] == total


@pytest.mark.asyncio
async def test_discover_forgetful_tools_by_category_e2e(mcp_client):
    """Test discovering tools filtered by category"""
    result = await mcp_client.call_tool(
        "discover_forgetful_tools",
        {"category": "memory"},
    )

    assert result.data is not None
    assert result.data["filtered_by"] == "memory"
    assert list(result.data["tools_by_category"]) == ["memory"]

    memory_tools = result.data["tools_by_category"]["memory"]
    assert len(memory_tools) >= 6

    # Verify tool structure
    for tool in memory_tools:
        _assert_tool_entry(tool, "memory")


@pytest.mark.asyncio
@pytest.mark.parametrize("removed_category", ["user", "project"])
async def test_removed_categories_are_not_discoverable_e2e(mcp_client, removed_category):
    with pytest.raises(Exception) as exc_info:
        await mcp_client.call_tool("discover_forgetful_tools", {"category": removed_category})

    assert removed_category in str(exc_info.value)


@pytest.mark.asyncio
async def test_discover_recent_memories_filters_e2e(mcp_client):
    """Clients can discover the two new recent-memory filters."""
    result = await mcp_client.call_tool("discover_forgetful_tools", {"category": "memory"})
    recent = next(
        tool for tool in result.data["tools_by_category"]["memory"]
        if tool["name"] == "get_recent_memories"
    )

    parameters = set(recent["parameters"])
    assert {"importance_min", "created_since"} <= parameters


@pytest.mark.asyncio
async def test_discover_tools_invalid_category_e2e(mcp_client):
    """Test discovering tools with invalid category raises error"""
    with pytest.raises(Exception) as exc_info:
        await mcp_client.call_tool(
            "discover_forgetful_tools",
            {"category": "invalid_category"},
        )

    # Should mention valid categories in error
    assert "Invalid category" in str(exc_info.value) or "invalid_category" in str(exc_info.value)


@pytest.mark.asyncio
async def test_how_to_use_forgetful_tool_e2e(mcp_client):
    """Test getting detailed documentation for a specific tool"""
    result = await mcp_client.call_tool(
        "how_to_use_forgetful_tool",
        {"tool_name": "query_memory"},
    )

    assert result.data is not None
    assert result.data["name"] == "query_memory"
    assert result.data["category"] == "memory"
    _assert_tool_doc_fields(result.data)

    # Verify JSON schema structure
    schema = result.data["json_schema"]
    assert "type" in schema
    assert schema["type"] == "object"
    assert "properties" in schema


@pytest.mark.asyncio
async def test_how_to_use_nonexistent_tool_e2e(mcp_client):
    """Test getting documentation for nonexistent tool raises error"""
    with pytest.raises(Exception) as exc_info:
        await mcp_client.call_tool(
            "how_to_use_forgetful_tool",
            {"tool_name": "nonexistent_tool"},
        )

    # Should mention tool not found
    assert "not found" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_execute_forgetful_tool_read_e2e(mcp_client):
    """Test executing a read tool through meta-tools"""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "get_recent_memories",
            "arguments": {},
        },
    )

    assert result.data is not None
    assert _has_field(result.data, "memories")
    assert _has_field(result.data, "total_count")


@pytest.mark.asyncio
async def test_execute_forgetful_tool_memory_create_e2e(mcp_client):
    """Test executing create_memory tool through meta-tools"""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": "Meta-tool test memory",
                "content": "Testing memory creation through execute_forgetful_tool",
                "context": "E2E test for meta-tools pattern",
                "keywords": ["test", "meta-tools"],
                "tags": ["test", "e2e"],
                "importance": 7,
            },
        },
    )

    assert result.data is not None
    # Result should be a MemoryCreateResponse
    assert _has_field(result.data, "id")
    assert _has_field(result.data, "title")

    memory_id = result.data.id if hasattr(result.data, "id") else result.data["id"]
    assert memory_id is not None


@pytest.mark.asyncio
async def test_execute_forgetful_tool_memory_query_e2e(mcp_client):
    """Test executing query_memory tool through meta-tools"""
    # First create a memory
    create_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": "Query test memory",
                "content": "This memory should be findable via query",
                "context": "Testing query through meta-tools",
                "keywords": ["findable", "query-test"],
                "tags": ["test"],
                "importance": 8,
            },
        },
    )
    assert create_result.data is not None

    # Now query for it
    query_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "query_memory",
            "arguments": {
                "query": "findable query test",
                "query_context": "Looking for the test memory we just created",
                "k": 5,
            },
        },
    )

    assert query_result.data is not None
    assert _has_field(query_result.data, "primary_memories")
    assert _has_field(query_result.data, "total_count")


@pytest.mark.asyncio
async def test_execute_nonexistent_tool_e2e(mcp_client):
    """Test executing a nonexistent tool raises error"""
    with pytest.raises(Exception) as exc_info:
        await mcp_client.call_tool(
            "execute_forgetful_tool",
            {
                "tool_name": "nonexistent_tool",
                "arguments": {},
            },
        )

    # Should mention tool not found
    assert "not found" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_execute_tool_preserves_tenant_context_e2e(mcp_client):
    """Test that the tenant is preserved through execute_forgetful_tool across multiple calls"""
    created = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": "Tenant context memory",
                "content": "Written by the first call",
                "context": "Context preservation",
                "keywords": ["tenant"],
                "tags": ["e2e"],
                "importance": 5,
            },
        },
    )

    fetched = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_memory", "arguments": {"memory_id": created.data["id"]}},
    )

    # The second call reads what the first wrote, so both ran as the same tenant
    assert fetched.data["title"] == "Tenant context memory"


@pytest.mark.asyncio
async def test_meta_tools_workflow_e2e(mcp_client):
    """Test complete workflow: discover → how_to_use → execute"""
    # Step 1: Discover tools in memory category
    discover_result = await mcp_client.call_tool(
        "discover_forgetful_tools",
        {"category": "memory"},
    )
    assert discover_result.data is not None
    memory_tools = discover_result.data["tools_by_category"]["memory"]
    assert len(memory_tools) > 0

    # Find create_memory tool
    create_memory_tool = _find_tool(memory_tools, "create_memory")
    assert create_memory_tool is not None

    # Step 2: Get detailed docs for create_memory
    docs_result = await mcp_client.call_tool(
        "how_to_use_forgetful_tool",
        {"tool_name": "create_memory"},
    )
    assert docs_result.data is not None
    assert docs_result.data["name"] == "create_memory"
    assert "json_schema" in docs_result.data

    # Step 3: Execute create_memory
    execute_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": "Workflow test memory",
                "content": "Created via discover→docs→execute workflow",
                "context": "Testing complete meta-tools workflow",
                "keywords": ["workflow", "test"],
                "tags": ["test"],
                "importance": 7,
            },
        },
    )
    assert execute_result.data is not None
    assert _has_field(execute_result.data, "id")


# ============================================================================
# Project scope via Meta-Tools
# ============================================================================

@pytest.mark.asyncio
async def test_execute_create_memory_with_project_via_meta_tools_e2e(mcp_client):
    """Test scoping a memory to a project through meta-tools"""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": "Scoped via meta-tools",
                "content": "A memory scoped to a project created on first use",
                "context": "Project scope",
                "keywords": ["project"],
                "tags": ["e2e"],
                "importance": 5,
                "project": "meta-tools-project",
            },
        },
    )

    assert result.data is not None
    assert result.data["project"] == "meta-tools-project"

    listed = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_recent_memories", "arguments": {"project": "meta-tools-project"}},
    )
    assert [m["id"] for m in listed.data["memories"]] == [result.data["id"]]


@pytest.mark.asyncio
async def test_discover_all_categories_e2e(mcp_client):
    """Test that all tool categories are discoverable"""
    result = await mcp_client.call_tool("discover_forgetful_tools", {})

    assert result.data is not None
    categories = result.data["categories_available"]

    # The memory category is the only one left
    assert categories == ["memory"]

    # Should have significant number of total tools
    assert result.data["total_count"] >= 10  # At least 10 tools total
