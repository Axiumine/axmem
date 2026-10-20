"""End-to-end tests for MCP memory tools: the project scope

A project is an optional free-form scope string on a memory. It is created on first use,
can be changed or cleared by update_memory, and narrows query_memory and
get_recent_memories.
"""
import pytest
from fastmcp.exceptions import ToolError

from tests.e2e.memory_tools_helpers_e2e import (
    create_memory,
    get_memory,
    primary_ids,
    query_memory,
    recent_envelope,
    recent_memories,
    update_memory,
)


async def _scoped(client, title, project=None, **extra):
    if project is not None:
        extra["project"] = project
    return await create_memory(
        client,
        title=title,
        content=f"Content of {title}",
        context="Testing the project scope",
        keywords=["scope"],
        tags=["test"],
        importance=7,
        **extra,
    )


@pytest.mark.asyncio
async def test_memory_without_a_project_has_no_scope_e2e(mcp_client):
    created = await _scoped(mcp_client, "Unscoped memory")

    assert created.data["project"] is None
    assert (await get_memory(mcp_client, created.data["id"])).data["project"] is None


@pytest.mark.asyncio
async def test_create_memory_with_a_project_e2e(mcp_client):
    created = await _scoped(mcp_client, "Scoped memory", project="atlas")

    assert created.data["project"] == "atlas"
    assert (await get_memory(mcp_client, created.data["id"])).data["project"] == "atlas"


@pytest.mark.asyncio
async def test_project_is_created_on_first_use_and_reused_e2e(mcp_client):
    first = await _scoped(mcp_client, "First", project="atlas")
    second = await _scoped(mcp_client, "Second", project="atlas")

    recent = recent_envelope(await recent_memories(mcp_client, project="atlas"))

    assert sorted(m["id"] for m in recent["memories"]) == sorted([first.data["id"], second.data["id"]])


@pytest.mark.asyncio
async def test_update_memory_sets_changes_and_clears_the_project_e2e(mcp_client):
    created = await _scoped(mcp_client, "Movable memory")
    memory_id = created.data["id"]

    added = await update_memory(mcp_client, memory_id, project="atlas")
    assert added.data["project"] == "atlas"
    assert (await get_memory(mcp_client, memory_id)).data["project"] == "atlas"

    moved = await update_memory(mcp_client, memory_id, project="borealis")
    assert moved.data["project"] == "borealis"

    cleared = await update_memory(mcp_client, memory_id, project="")
    assert cleared.data["project"] is None
    assert (await get_memory(mcp_client, memory_id)).data["project"] is None


@pytest.mark.asyncio
async def test_query_memory_is_narrowed_by_project_e2e(mcp_client):
    in_atlas = await _scoped(mcp_client, "Atlas deployment notes", project="atlas")
    await _scoped(mcp_client, "Borealis deployment notes", project="borealis")
    unscoped = await _scoped(mcp_client, "Unscoped deployment notes")

    scoped = await query_memory(mcp_client, "deployment notes", "deploying", project="atlas")
    strict = await query_memory(
        mcp_client, "deployment notes", "deploying", project="atlas", strict_project_filter=True,
    )

    assert in_atlas.data["id"] in primary_ids(scoped)
    assert primary_ids(strict) == [in_atlas.data["id"]]
    assert unscoped.data["id"] not in primary_ids(strict)


@pytest.mark.asyncio
async def test_get_recent_memories_is_narrowed_by_project_e2e(mcp_client):
    in_atlas = await _scoped(mcp_client, "Atlas memory", project="atlas")
    await _scoped(mcp_client, "Borealis memory", project="borealis")
    await _scoped(mcp_client, "Unscoped memory")

    recent = recent_envelope(await recent_memories(mcp_client, project="atlas"))

    assert [m["id"] for m in recent["memories"]] == [in_atlas.data["id"]]


@pytest.mark.asyncio
async def test_project_scope_is_matched_exactly_e2e(mcp_client):
    await _scoped(mcp_client, "Scoped memory", project="Atlas")

    for other in ("atlas", "Atl", "Atlas "):
        recent = recent_envelope(await recent_memories(mcp_client, project=other))
        # surrounding whitespace is trimmed, so "Atlas " is the same scope; the others differ
        expected = 1 if other.strip() == "Atlas" else 0
        assert len(recent["memories"]) == expected, other


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_project", ["   ", "x" * 501])
async def test_invalid_project_is_rejected_e2e(mcp_client, bad_project):
    with pytest.raises(ToolError):
        await _scoped(mcp_client, "Bad scope", project=bad_project)


@pytest.mark.asyncio
async def test_project_tools_no_longer_exist_e2e(mcp_client):
    discovered = await mcp_client.call_tool("discover_forgetful_tools", {})
    names = {t["name"] for tools in discovered.data["tools_by_category"].values() for t in tools}

    assert names.isdisjoint({
        "create_project", "update_project", "delete_project", "list_projects", "get_project",
        "get_current_user", "update_user_notes",
    })
