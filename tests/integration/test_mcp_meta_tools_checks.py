"""Contract of the three MCP meta-tools: the discovery payload, the documentation and the checks before a call.

The payloads and error texts are what an agent reasons from, and the log lines are what an operator searches. Each
is pinned word for word over a real ``ToolRegistry`` with stubbed tool implementations.
"""
import logging

import pytest
from fastmcp.exceptions import ToolError

from app.routes.mcp.meta_tools import (
    NAME_MAX_LENGTH,
    build_discovery_payload,
    build_tool_documentation,
    ensure_name_length,
    ensure_tool_executable,
    reject_invalid_argument_names,
)
from tests.mcp_meta_tools_support import (
    GET_MEMORY_DISCOVERY,
    MEMORY_ID,
    META_LOGGER,
    build_registry,
    meta_records,
    payload_names,
)

# ---------------------------------------------------------------------------
# discovery payload
# ---------------------------------------------------------------------------


def test_discovery_lists_the_permitted_tools_by_category_with_their_counts():
    registry = build_registry()

    payload = build_discovery_payload(registry, {"get_memory", "create_memory", "link_memories"})

    assert list(payload) == ["tools_by_category", "total_count", "categories_available", "filtered_by"]
    assert list(payload["tools_by_category"]) == ["memory"]
    assert payload["tools_by_category"]["memory"][0] == GET_MEMORY_DISCOVERY
    assert payload_names(payload) == ["get_memory", "create_memory", "link_memories"]
    assert payload["total_count"] == 3
    assert payload["categories_available"] == ["memory"]
    assert payload["filtered_by"] is None


def test_discovery_leaves_out_the_tools_that_are_not_permitted():
    payload = build_discovery_payload(build_registry(), {"get_memory"})

    assert payload_names(payload) == ["get_memory"]
    assert payload["total_count"] == 1


def test_discovery_with_nothing_permitted_is_empty():
    payload = build_discovery_payload(build_registry(), set())

    assert payload == {"tools_by_category": {}, "total_count": 0, "categories_available": [], "filtered_by": None}


@pytest.mark.parametrize(
    ("category", "names"),
    [
        ("memory", ["get_memory", "create_memory", "link_memories"]),
        ("Memory", ["get_memory", "create_memory", "link_memories"]),
        ("linking", ["link_memories"]),
        ("LINKING", ["link_memories"]),
    ],
)
def test_discovery_by_category_ignores_case_and_reports_the_filter_as_typed(category, names):
    payload = build_discovery_payload(build_registry(), {"get_memory", "create_memory", "link_memories"}, category)

    assert payload_names(payload) == names
    assert payload["total_count"] == len(names)
    assert payload["filtered_by"] == category
    assert payload["categories_available"] == ["memory"]


def test_discovery_with_an_empty_category_is_not_filtered():
    payload = build_discovery_payload(build_registry(), {"get_memory", "create_memory", "link_memories"}, "")

    assert payload["total_count"] == 3
    assert payload["filtered_by"] is None


def test_discovery_of_an_unknown_category_lists_the_valid_ones():
    with pytest.raises(ToolError) as caught:
        build_discovery_payload(build_registry(), set(), "nothing")

    assert str(caught.value) == "Invalid category 'nothing'. Available categories: memory, linking"


# ---------------------------------------------------------------------------
# tool documentation and execution checks
# ---------------------------------------------------------------------------


def test_documentation_of_a_permitted_tool_is_its_detailed_metadata():
    registry = build_registry()

    detailed = build_tool_documentation(registry, {"get_memory"}, "get_memory")

    assert detailed == registry.get_tool("get_memory").metadata.to_detailed_dict()
    assert detailed["name"] == "get_memory"
    assert detailed["json_schema"] == {
        "type": "object",
        "properties": {
            "memory_id": {"description": "The memory", "type": "string", "example": MEMORY_ID},
        },
        "required": ["memory_id"],
    }


def test_documentation_of_an_unknown_tool_lists_the_first_ten_permitted_ones():
    registry = build_registry(extra_read_tools=12)
    permitted = {meta.name for meta in registry.list_all_tools()} - {"get_memory"}

    with pytest.raises(ToolError) as caught:
        build_tool_documentation(registry, permitted, "missing")

    assert str(caught.value) == (
        "Tool 'missing' not found in registry. Available tools (first 10): "
        "create_memory, link_memories, extra_tool_00, extra_tool_01, extra_tool_02, "
        "extra_tool_03, extra_tool_04, extra_tool_05, extra_tool_06, extra_tool_07"
    )


@pytest.mark.parametrize(
    ("tool_name", "required"),
    [("get_memory", "read:memories"), ("create_memory", "write:memories"), ("link_memories", "write:memories")],
)
def test_documentation_of_a_tool_outside_the_scopes_names_the_scope_it_needs(tool_name, required):
    with pytest.raises(ToolError) as caught:
        build_tool_documentation(build_registry(), set(), tool_name)

    assert str(caught.value) == (
        f"Tool '{tool_name}' is not permitted under current scopes. Required scope: '{required}'"
    )


def test_execution_of_a_permitted_tool_passes_the_check():
    assert ensure_tool_executable(build_registry(), {"get_memory"}, "get_memory") is None


def test_execution_of_an_unknown_tool_lists_the_first_ten_permitted_ones():
    registry = build_registry(extra_read_tools=12)
    permitted = {meta.name for meta in registry.list_all_tools()} | {"missing"}

    with pytest.raises(ToolError) as caught:
        ensure_tool_executable(registry, permitted, "missing")

    assert str(caught.value) == (
        "Tool 'missing' not found in registry. Available tools (first 10): "
        "get_memory, create_memory, link_memories, extra_tool_00, extra_tool_01, extra_tool_02, "
        "extra_tool_03, extra_tool_04, extra_tool_05, extra_tool_06"
    )


@pytest.mark.parametrize(
    ("tool_name", "required"),
    [("get_memory", "read:memories"), ("create_memory", "write:memories")],
)
def test_execution_of_a_tool_outside_the_scopes_names_the_scope_it_needs(tool_name, required):
    with pytest.raises(ToolError) as caught:
        ensure_tool_executable(build_registry(), {"link_memories"}, tool_name)

    assert str(caught.value) == (
        f"Tool '{tool_name}' is not permitted under current scopes. Required scope: '{required}'"
    )


def test_a_name_may_be_one_hundred_characters_and_no_more():
    assert NAME_MAX_LENGTH == 100
    assert ensure_name_length(None, "category") is None
    assert ensure_name_length("", "category") is None
    assert ensure_name_length("x" * 99, "category") is None
    assert ensure_name_length("x" * 100, "category") is None
    with pytest.raises(ToolError) as caught:
        ensure_name_length("x" * 101, "tool_name")
    assert str(caught.value) == "tool_name must be at most 100 characters"


def test_argument_names_that_are_identifiers_pass_and_leave_no_log_line(caplog):
    caplog.set_level(logging.DEBUG, logger=META_LOGGER)

    assert reject_invalid_argument_names("get_memory", {"memory_id": 1, "_x": 2, "ctx": 3}) is None
    assert reject_invalid_argument_names("get_memory", {}) is None

    assert meta_records(caplog) == []


def test_an_argument_name_that_is_not_an_identifier_is_refused_without_echo_and_logged_once(caplog):
    caplog.set_level(logging.DEBUG, logger=META_LOGGER)

    with pytest.raises(ToolError) as caught:
        reject_invalid_argument_names("get_memory", {"ok": 1, "bad\nname": 2})

    assert str(caught.value) == (
        "Invalid argument name: argument names must be identifiers "
        "(letters, digits and underscores, not starting with a digit)"
    )
    assert meta_records(caplog) == [
        (META_LOGGER, logging.WARNING, "execute_axmem_tool: 'get_memory' rejected, invalid argument name"),
    ]
