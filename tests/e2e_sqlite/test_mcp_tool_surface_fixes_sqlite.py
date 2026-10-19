"""E2E tests for MCP tool-surface argument validation with in-memory SQLite

Covers the project and memory id arguments reached through
``execute_forgetful_tool``.
"""
import pytest
from fastmcp.exceptions import ToolError


async def call(client, tool_name, **arguments):
    return await client.call_tool(
        "execute_forgetful_tool", {"tool_name": tool_name, "arguments": arguments},
    )


async def make_project(client, name="proj"):
    result = await call(
        client, "create_project", name=name, description="d", project_type="development",
    )
    return result.data["id"]


async def make_memory(client):
    result = await call(
        client, "create_memory", title="m", content="c", context="ctx",
        keywords=["k"], tags=["t"], importance=7,
    )
    return result.data["id"]


# (create helper, get tool, id parameter, [(tool, extra arguments)])
ID_TOOL_CASES = [
    pytest.param(
        make_project, "get_project", "project_id",
        [("get_project", {}), ("update_project", {"name": "changed"}), ("delete_project", {})],
        id="project",
    ),
]

INVALID_IDS = [True, 1.9, "not-an-id", [1], {"id": 1}, None]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_id", INVALID_IDS, ids=repr)
@pytest.mark.parametrize(("create", "get_tool", "id_arg", "tools"), ID_TOOL_CASES)
async def test_tools_reject_invalid_ids_and_leave_object_intact(
    mcp_client, create, get_tool, id_arg, tools, invalid_id,
):
    """Invalid ids give a parameter error (like the project tools) and delete nothing."""
    object_id = await create(mcp_client)
    assert object_id == 1

    for tool_name, extra in tools:
        with pytest.raises(ToolError, match=f"{id_arg} (must|cannot)") as excinfo:
            await call(mcp_client, tool_name, **{id_arg: invalid_id}, **extra)
        message = str(excinfo.value)
        assert "[SQL:" not in message
        assert "[parameters:" not in message

    survivor = await call(mcp_client, get_tool, **{id_arg: object_id})
    assert survivor.data["id"] == object_id


@pytest.mark.asyncio
async def test_numeric_string_and_integral_float_ids_are_accepted(mcp_client):
    """'1' and 1.0 identify object 1."""
    project_id = await make_project(mcp_client)

    assert (await call(mcp_client, "get_project", project_id=str(project_id))).data["id"] == 1
    assert (await call(mcp_client, "get_project", project_id=float(project_id))).data["id"] == 1

    deleted = await call(mcp_client, "delete_project", project_id=f" {project_id} ")
    assert deleted.data["success"] is True


@pytest.mark.asyncio
async def test_create_memory_rejects_boolean_project_reference(mcp_client):
    """project_ids=[True] must not silently link the new memory to project 1."""
    await make_project(mcp_client)

    with pytest.raises(ToolError, match="project_ids (must|cannot)"):
        await call(
            mcp_client, "create_memory", title="m", content="c", context="ctx",
            keywords=["k"], tags=["t"], importance=7, project_ids=[True],
        )


@pytest.mark.asyncio
async def test_update_memory_rejects_boolean_project_reference(mcp_client):
    await make_project(mcp_client)
    memory_id = await make_memory(mcp_client)

    with pytest.raises(ToolError, match="project_ids (must|cannot)"):
        await call(mcp_client, "update_memory", memory_id=memory_id, project_ids=[True])


@pytest.mark.asyncio
async def test_list_id_does_not_leak_sql_or_user_id(mcp_client):
    """A list id is a parameter error, not a driver error echoing statement and user id."""
    await make_project(mcp_client)

    with pytest.raises(ToolError) as excinfo:
        await call(mcp_client, "get_project", project_id=[1])

    message = str(excinfo.value)
    assert "project_id" in message
    assert "SELECT" not in message
    assert "[parameters:" not in message


@pytest.mark.asyncio
async def test_update_project_missing_project_is_not_found_error(mcp_client):
    """Updating a project that does not exist is an error, not a successful null result."""
    with pytest.raises(ToolError, match="Project with id 99999 not found"):
        await call(mcp_client, "update_project", project_id=99999, name="x")


@pytest.mark.asyncio
async def test_update_project_existing_project_still_updates(mcp_client):
    project_id = await make_project(mcp_client)

    updated = await call(mcp_client, "update_project", project_id=project_id, name="renamed")

    assert updated.data["id"] == project_id
    assert updated.data["name"] == "renamed"
