"""End-to-end tests for MCP meta-tools via HTTP

Requires:
- PostgreSQL running in Docker
- MCP server running on configured port
- Registry initialized with tools

Tests the complete meta-tools pattern:
HTTP → FastMCP Client → MCP Protocol → Meta-tools → Registry → Adapters → Services
"""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


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


@pytest.mark.e2e
async def test_discover_forgetful_tools_all_e2e(mcp_client):
    """Test discovering all tools without category filter"""
    result = await mcp_client.call_tool("discover_forgetful_tools", {})

    assert result.data is not None
    _assert_discovery_payload_keys(result.data)

    # Should have at least user and memory categories
    assert "user" in result.data["categories_available"]
    assert "memory" in result.data["categories_available"]

    # Should have tools in each category
    assert len(result.data["tools_by_category"]["user"]) >= 2
    assert len(result.data["tools_by_category"]["memory"]) >= 6

    # Total count should match sum of all categories
    total = sum(len(tools) for tools in result.data["tools_by_category"].values())
    assert result.data["total_count"] == total


@pytest.mark.e2e
async def test_discover_forgetful_tools_by_category_e2e(mcp_client):
    """Test discovering tools filtered by category"""
    result = await mcp_client.call_tool(
        "discover_forgetful_tools",
        {"category": "user"},
    )

    assert result.data is not None
    assert result.data["filtered_by"] == "user"
    assert "user" in result.data["tools_by_category"]

    # Should only contain user category
    user_tools = result.data["tools_by_category"]["user"]
    assert len(user_tools) >= 2

    # Verify tool structure
    for tool in user_tools:
        _assert_tool_entry(tool, "user")


@pytest.mark.e2e
async def test_discover_tools_invalid_category_e2e(mcp_client):
    """Test discovering tools with invalid category raises error"""
    with pytest.raises(Exception) as exc_info:
        await mcp_client.call_tool(
            "discover_forgetful_tools",
            {"category": "invalid_category"},
        )

    # Should mention valid categories in error
    assert "Invalid category" in str(exc_info.value) or "invalid_category" in str(exc_info.value)


@pytest.mark.e2e
async def test_how_to_use_forgetful_tool_e2e(mcp_client):
    """Test getting detailed documentation for a specific tool"""
    result = await mcp_client.call_tool(
        "how_to_use_forgetful_tool",
        {"tool_name": "get_current_user"},
    )

    assert result.data is not None
    assert result.data["name"] == "get_current_user"
    assert result.data["category"] == "user"
    _assert_tool_doc_fields(result.data)

    # Verify JSON schema structure
    schema = result.data["json_schema"]
    assert "type" in schema
    assert schema["type"] == "object"
    assert "properties" in schema


@pytest.mark.e2e
async def test_how_to_use_nonexistent_tool_e2e(mcp_client):
    """Test getting documentation for nonexistent tool raises error"""
    with pytest.raises(Exception) as exc_info:
        await mcp_client.call_tool(
            "how_to_use_forgetful_tool",
            {"tool_name": "nonexistent_tool"},
        )

    # Should mention tool not found
    assert "not found" in str(exc_info.value).lower()


@pytest.mark.e2e
async def test_execute_forgetful_tool_user_e2e(mcp_client):
    """Test executing a user tool through meta-tools"""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "get_current_user",
            "arguments": {},
        },
    )

    assert result.data is not None
    # Result should be a UserResponse
    assert _has_field(result.data, "name")
    assert _has_field(result.data, "created_at")


@pytest.mark.e2e
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


@pytest.mark.e2e
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


@pytest.mark.e2e
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


@pytest.mark.e2e
async def test_execute_tool_preserves_user_context_e2e(mcp_client):
    """Test that user context is preserved through execute_forgetful_tool across multiple calls"""
    # First call via meta-tool
    result1 = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "get_current_user",
            "arguments": {},
        },
    )
    user1 = result1.data

    # Second call via meta-tool
    result2 = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "get_current_user",
            "arguments": {},
        },
    )
    user2 = result2.data

    # Both should return the same user (proving context is preserved)
    user1_name = user1.name if hasattr(user1, "name") else user1["name"]
    user2_name = user2.name if hasattr(user2, "name") else user2["name"]
    assert user1_name == user2_name


@pytest.mark.e2e
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
# Project Tools via Meta-Tools
# ============================================================================

@pytest.mark.e2e
async def test_execute_create_project_via_meta_tools_e2e(mcp_client):
    """Test creating a project through meta-tools"""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_project",
            "arguments": {
                "name": "Test Project",
                "description": "A test project created via meta-tools",
                "project_type": "development",
            },
        },
    )

    assert result.data is not None
    assert _has_field(result.data, "id")
    assert _has_field(result.data, "name")


@pytest.mark.e2e
async def test_execute_list_projects_via_meta_tools_e2e(mcp_client):
    """Test listing projects through meta-tools"""
    # First create a project
    create_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_project",
            "arguments": {
                "name": "List Test Project",
                "description": "Project for list test",
                "project_type": "work",
            },
        },
    )
    assert create_result.data is not None

    # Now list projects
    list_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "list_projects",
            "arguments": {},
        },
    )

    assert list_result.data is not None
    assert _has_field(list_result.data, "projects")
    assert _has_field(list_result.data, "count")


# ============================================================================
# Entity Tools via Meta-Tools
# ============================================================================

@pytest.mark.e2e
async def test_execute_create_entity_via_meta_tools_e2e(mcp_client):
    """Test creating an entity through meta-tools"""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_entity",
            "arguments": {
                "name": "Test Organization",
                "entity_type": "Organization",
                "notes": "A test organization created via meta-tools",
            },
        },
    )

    assert result.data is not None
    assert _has_field(result.data, "id")
    assert _has_field(result.data, "name")


@pytest.mark.e2e
async def test_execute_entity_workflow_via_meta_tools_e2e(mcp_client):
    """Test complete entity workflow: create → get → update → list"""
    # Create entity
    create_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_entity",
            "arguments": {
                "name": "Anthropic",
                "entity_type": "Organization",
                "tags": ["ai", "research"],
            },
        },
    )
    assert create_result.data is not None
    entity_id = create_result.data.id if hasattr(create_result.data, "id") else create_result.data["id"]

    # Get entity
    get_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "get_entity",
            "arguments": {"entity_id": entity_id},
        },
    )
    assert get_result.data is not None

    # Update entity
    update_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "update_entity",
            "arguments": {
                "entity_id": entity_id,
                "notes": "Updated via meta-tools",
            },
        },
    )
    assert update_result.data is not None

    # List entities
    list_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "list_entities",
            "arguments": {},
        },
    )
    assert list_result.data is not None


@pytest.mark.e2e
async def test_discover_all_categories_e2e(mcp_client):
    """Test that all tool categories are discoverable"""
    result = await mcp_client.call_tool("discover_forgetful_tools", {})

    assert result.data is not None
    categories = result.data["categories_available"]

    # Should have all 4 categories
    assert "user" in categories
    assert "memory" in categories
    assert "project" in categories
    assert "entity" in categories

    # Should have significant number of total tools
    assert result.data["total_count"] >= 33  # At least 33 tools total
