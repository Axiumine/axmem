# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests (real Qdrant node) for the argument errors of the memory tools.

Drives ``execute_axmem_tool`` through the real stack and checks the message an
agent gets back when it omits an id, passes ids in a shape the alias resolution cannot
use, or sends a ``created_since`` that is not a timestamp string.
"""
import pytest
from fastmcp.exceptions import ToolError

pytestmark = pytest.mark.asyncio

FIRST = "01a157bf-5d0f-77ae-9308-9af4ac2edb17"
SECOND = "01a157bf-5d1e-7536-bac1-4b5ea6f5f392"


async def _exec(client, tool_name, arguments):
    return await client.call_tool(
        "execute_axmem_tool",
        {"tool_name": tool_name, "arguments": arguments},
    )


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("get_memory", {}),
        ("update_memory", {"title": "Renamed"}),
        ("mark_memory_obsolete", {"reason": "superseded"}),
        ("unobsolete_memory", {}),
    ],
)
async def test_memory_tool_without_any_memory_id_names_the_missing_argument(
    mcp_client, tool_name, arguments,
):
    with pytest.raises(ToolError) as caught:
        await _exec(mcp_client, tool_name, arguments)

    assert str(caught.value) == f"Failed to execute '{tool_name}': {tool_name} requires memory_id (or id)"


@pytest.mark.parametrize("created_since", [20260801, True, ["2026-08-01T00:00:00Z"], {"at": "2026-08-01"}])
async def test_recent_memories_rejects_a_created_since_that_is_not_a_string(mcp_client, created_since):
    with pytest.raises(ToolError) as caught:
        await _exec(mcp_client, "get_recent_memories", {"created_since": created_since})

    assert str(caught.value) == "created_since must be an ISO 8601 timestamp with a timezone"


@pytest.mark.parametrize("created_since", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
async def test_recent_memories_rejects_a_created_since_with_no_instant_in_utc(mcp_client, created_since):
    with pytest.raises(ToolError) as caught:
        await _exec(mcp_client, "get_recent_memories", {"created_since": created_since})

    assert str(caught.value) == "created_since must be an ISO 8601 timestamp with a timezone"


@pytest.mark.parametrize("tool_name", ["link_memories", "unlink_memories"])
@pytest.mark.parametrize("id_argument", ["memory_ids", "ids"])
@pytest.mark.parametrize("ids", [[7], 7, "7,8", {"first": 7, "second": 8}])
async def test_id_sequence_argument_needs_a_source_and_at_least_one_target(
    mcp_client, tool_name, id_argument, ids,
):
    with pytest.raises(ToolError) as caught:
        await _exec(mcp_client, tool_name, {id_argument: ids})

    assert str(caught.value) == (
        f"Failed to execute '{tool_name}': "
        f"{tool_name} {id_argument} must contain at least 2 IDs [source, target, ...]"
    )


@pytest.mark.parametrize(
    ("tool_name", "arguments", "message"),
    [
        ("link_memories", {"related_ids": [SECOND]}, "link_memories requires memory_id (or source_id)"),
        ("link_memories", {"memory_id": FIRST}, "related_ids cannot be empty"),
        ("link_memories", {"memory_id": FIRST, "related_ids": [FIRST, FIRST]}, "Cannot link memory to itself"),
        ("unlink_memories", {"target_id": SECOND}, "unlink_memories requires source_id (or memory_id)"),
        ("unlink_memories", {"source_id": FIRST}, "unlink_memories requires target_id (or related_ids)"),
        (
            "unlink_memories",
            {"source_id": FIRST, "target_id": FIRST},
            "Cannot unlink memory from itself or no valid target IDs provided",
        ),
    ],
    ids=[
        "link-without-source",
        "link-without-targets",
        "link-to-itself",
        "unlink-without-source",
        "unlink-without-target",
        "unlink-from-itself",
    ],
)
async def test_link_tools_report_which_part_of_the_pair_is_missing(
    mcp_client, tool_name, arguments, message,
):
    with pytest.raises(ToolError) as caught:
        await _exec(mcp_client, tool_name, arguments)

    assert str(caught.value) == f"Failed to execute '{tool_name}': {message}"
