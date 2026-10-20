"""E2E tests for MCP tool-surface argument validation with in-memory SQLite

Covers the memory id and project scope arguments reached through
``execute_forgetful_tool``.
"""
import pytest
from fastmcp.exceptions import ToolError


async def call(client, tool_name, **arguments):
    return await client.call_tool(
        "execute_forgetful_tool", {"tool_name": tool_name, "arguments": arguments},
    )


async def make_memory(client):
    result = await call(
        client, "create_memory", title="m", content="c", context="ctx",
        keywords=["k"], tags=["t"], importance=7,
    )
    return result.data["id"]


# (tool, extra arguments) pairs that take a memory id
ID_TOOLS = [
    ("get_memory", {}),
    ("update_memory", {"title": "changed"}),
    ("mark_memory_obsolete", {"reason": "r"}),
    ("unobsolete_memory", {}),
]

INVALID_IDS = [True, 1.9, "not-an-id", [1], {"id": 1}, None]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_id", INVALID_IDS, ids=repr)
async def test_tools_reject_invalid_ids_and_leave_the_memory_intact(mcp_client, invalid_id):
    """Invalid ids give a parameter error and change nothing."""
    memory_id = await make_memory(mcp_client)
    assert memory_id == 1

    for tool_name, extra in ID_TOOLS:
        with pytest.raises(ToolError, match="memory_id") as excinfo:
            await call(mcp_client, tool_name, memory_id=invalid_id, **extra)
        message = str(excinfo.value)
        assert "[SQL:" not in message
        assert "[parameters:" not in message

    survivor = (await call(mcp_client, "get_memory", memory_id=memory_id)).data
    assert survivor["id"] == memory_id
    assert survivor["title"] == "m"
    assert survivor["is_obsolete"] is False


@pytest.mark.asyncio
async def test_numeric_string_and_integral_float_ids_are_accepted(mcp_client):
    """'1' and 1.0 identify memory 1."""
    memory_id = await make_memory(mcp_client)

    assert (await call(mcp_client, "get_memory", memory_id=str(memory_id))).data["id"] == 1
    assert (await call(mcp_client, "get_memory", memory_id=float(memory_id))).data["id"] == 1
    assert (await call(mcp_client, "get_memory", memory_id=f" {memory_id} ")).data["id"] == 1


@pytest.mark.asyncio
async def test_create_memory_rejects_a_boolean_project(mcp_client):
    """project=True must not be stored as the scope 'True'."""
    with pytest.raises(ToolError, match="project"):
        await call(
            mcp_client, "create_memory", title="m", content="c", context="ctx",
            keywords=["k"], tags=["t"], importance=7, project=True,
        )


@pytest.mark.asyncio
async def test_update_memory_rejects_a_boolean_project(mcp_client):
    memory_id = await make_memory(mcp_client)

    with pytest.raises(ToolError, match="project"):
        await call(mcp_client, "update_memory", memory_id=memory_id, project=True)

    assert (await call(mcp_client, "get_memory", memory_id=memory_id)).data["project"] is None


@pytest.mark.asyncio
async def test_list_id_does_not_leak_sql_or_user_id(mcp_client):
    """A list id is a parameter error, not a driver error echoing statement and user id."""
    await make_memory(mcp_client)

    with pytest.raises(ToolError) as excinfo:
        await call(mcp_client, "get_memory", memory_id=[1])

    message = str(excinfo.value)
    assert "memory_id" in message
    assert "SELECT" not in message
    assert "[parameters:" not in message


@pytest.mark.asyncio
async def test_update_memory_missing_memory_is_not_found_error(mcp_client):
    """Updating a memory that does not exist is an error, not a successful null result."""
    with pytest.raises(ToolError, match="99999"):
        await call(mcp_client, "update_memory", memory_id=99999, title="x")


@pytest.mark.asyncio
async def test_update_memory_existing_memory_still_updates(mcp_client):
    memory_id = await make_memory(mcp_client)

    updated = await call(mcp_client, "update_memory", memory_id=memory_id, title="renamed")

    assert updated.data["id"] == memory_id
    assert updated.data["title"] == "renamed"
