# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for MCP tool-surface argument validation on a real Qdrant node

Covers the memory id and project scope arguments reached through
``execute_axmem_tool``.
"""
from uuid import UUID, uuid4

import pytest
from fastmcp.exceptions import ToolError


async def call(client, tool_name, **arguments):
    return await client.call_tool(
        "execute_axmem_tool", {"tool_name": tool_name, "arguments": arguments},
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

INVALID_IDS = [True, 1, 1.9, "not-an-id", "1", [1], {"id": 1}, None]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_id", INVALID_IDS, ids=repr)
async def test_tools_reject_invalid_ids_and_leave_the_memory_intact(mcp_client, invalid_id):
    """Invalid ids give a parameter error and change nothing."""
    memory_id = await make_memory(mcp_client)
    assert UUID(memory_id).version == 7

    for tool_name, extra in ID_TOOLS:
        with pytest.raises(ToolError, match="memory_id") as excinfo:
            await call(mcp_client, tool_name, memory_id=invalid_id, **extra)
        message = str(excinfo.value)
        assert "qdrant" not in message.lower()
        assert "/collections" not in message

    survivor = (await call(mcp_client, "get_memory", memory_id=memory_id)).data
    assert survivor["id"] == memory_id
    assert survivor["title"] == "m"
    assert survivor["is_obsolete"] is False


@pytest.mark.asyncio
async def test_other_spellings_of_the_same_uuid_are_accepted(mcp_client):
    """Upper case, without hyphens and with surrounding spaces all identify the same memory."""
    memory_id = await make_memory(mcp_client)

    for spelling in (memory_id.upper(), memory_id.replace("-", ""), f" {memory_id} "):
        assert (await call(mcp_client, "get_memory", memory_id=spelling)).data["id"] == memory_id


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
async def test_list_id_does_not_leak_store_details_or_tenant_id(mcp_client):
    """A list id is a parameter error, not a driver error echoing the request and the tenant."""
    await make_memory(mcp_client)

    with pytest.raises(ToolError) as excinfo:
        await call(mcp_client, "get_memory", memory_id=[1])

    message = str(excinfo.value)
    assert "memory_id" in message
    assert "tenant" not in message.lower()
    assert "/collections" not in message


@pytest.mark.asyncio
async def test_update_memory_missing_memory_is_not_found_error(mcp_client):
    """Updating a memory that does not exist is an error, not a successful null result."""
    missing = str(uuid4())
    with pytest.raises(ToolError, match=missing):
        await call(mcp_client, "update_memory", memory_id=missing, title="x")


@pytest.mark.asyncio
async def test_update_memory_existing_memory_still_updates(mcp_client):
    memory_id = await make_memory(mcp_client)

    updated = await call(mcp_client, "update_memory", memory_id=memory_id, title="renamed")

    assert updated.data["id"] == memory_id
    assert updated.data["title"] == "renamed"
