"""End-to-end test for health on real Qdrant

Validates that the FastMCP app initializes correctly against Qdrant.
"""
import pytest


@pytest.mark.asyncio
async def test_health_endpoint_accessible(mcp_client):
    """Test that the MCP client is connected and functional"""
    # The fact that we have a connected mcp_client proves the app initialized successfully
    # against Qdrant. We can verify by calling a simple tool.
    result = await mcp_client.call_tool(
        "execute_axmem_tool",
        {
            "tool_name": "get_recent_memories",
            "arguments": {},
        },
    )

    # If we get here without errors, the app is healthy
    assert result.data is not None
