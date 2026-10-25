"""Contract of the three MCP meta-tools: what a client gets from a call and what an operator finds in the log.

Each tool is called through the mounted FastMCP server, with stubbed tool implementations behind a real
``ToolRegistry``. The payloads and error texts are what an agent reasons from; the log lines are what an
operator searches.
"""
import logging

import pytest
from fastmcp import Client, Context
from fastmcp.exceptions import ToolError

from app.models.tool_registry_models import ToolCategory
from app.routes.mcp.meta_tools import build_discovery_payload
from app.routes.mcp.tool_errors import GENERIC_FAILURE
from app.routes.mcp.tool_registry import ToolRegistry
from tests.mcp_meta_tools_support import (
    CALLS,
    MEMORY_ID,
    META_LOGGER,
    build_registry,
    logged_errors,
    meta_records,
    mounted_server,
    payload_names,
)

# ---------------------------------------------------------------------------
# discover_axmem_tools
# ---------------------------------------------------------------------------


async def test_discover_returns_the_payload_and_logs_the_request_and_the_count(caplog):
    caplog.set_level(logging.INFO, logger=META_LOGGER)

    async with Client(mounted_server(build_registry())) as client:
        result = await client.call_tool("discover_axmem_tools", {})

    assert result.structured_content == build_discovery_payload(build_registry(), {"get_memory", "create_memory", "link_memories"})
    assert meta_records(caplog) == [
        (META_LOGGER, logging.INFO, "discover_axmem_tools: category=None"),
        (META_LOGGER, logging.INFO, "discover_axmem_tools: returned 3 tools"),
    ]


async def test_discover_filters_by_category_and_logs_it(caplog):
    caplog.set_level(logging.INFO, logger=META_LOGGER)

    async with Client(mounted_server(build_registry())) as client:
        result = await client.call_tool("discover_axmem_tools", {"category": "linking"})

    assert payload_names(result.structured_content) == ["link_memories"]
    assert result.structured_content["filtered_by"] == "linking"
    assert meta_records(caplog) == [
        (META_LOGGER, logging.INFO, "discover_axmem_tools: category='linking'"),
        (META_LOGGER, logging.INFO, "discover_axmem_tools: returned 1 tools"),
    ]


async def test_discover_shows_only_the_tools_the_scopes_permit():
    server = mounted_server(build_registry(), permitted={"get_memory"}, scopes=frozenset({"read"}))

    async with Client(server) as client:
        result = await client.call_tool("discover_axmem_tools", {})

    assert payload_names(result.structured_content) == ["get_memory"]
    assert result.structured_content["total_count"] == 1


async def test_discover_refuses_an_unknown_category_with_the_list_of_valid_ones():
    async with Client(mounted_server(build_registry())) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool("discover_axmem_tools", {"category": "nothing"})

    assert str(caught.value) == "Invalid category 'nothing'. Available categories: memory, linking"


async def test_discover_refuses_a_category_longer_than_one_hundred_characters():
    async with Client(mounted_server(build_registry())) as client:
        accepted = await client.call_tool("discover_axmem_tools", {"category": "memory"})
        with pytest.raises(ToolError) as caught:
            await client.call_tool("discover_axmem_tools", {"category": "x" * 101})

    assert accepted.structured_content["filtered_by"] == "memory"
    assert str(caught.value) == "category must be at most 100 characters"


@pytest.mark.parametrize(
    ("error", "detail"),
    [(ValueError("authored text"), "authored text"), (RuntimeError("secret internals"), GENERIC_FAILURE)],
    ids=["authored", "internal"],
)
async def test_discover_reports_a_failure_with_only_the_client_safe_detail_and_logs_the_cause(caplog, monkeypatch, error, detail):
    registry = build_registry()

    def _broken(_permitted):
        raise error

    monkeypatch.setattr(registry, "get_permitted_tools", _broken)
    caplog.set_level(logging.INFO, logger=META_LOGGER)

    async with Client(mounted_server(registry)) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool("discover_axmem_tools", {})

    assert str(caught.value) == f"Failed to discover tools: {detail}"
    failure = logged_errors(caplog)
    assert [record.getMessage() for record in failure] == [f"discover_axmem_tools failed: {error!r}"]
    assert failure[0].exc_info is not None
    assert failure[0].exc_info[1] is error


# ---------------------------------------------------------------------------
# how_to_use_axmem_tool
# ---------------------------------------------------------------------------


async def test_how_to_use_returns_the_documentation_and_logs_the_request(caplog):
    caplog.set_level(logging.INFO, logger=META_LOGGER)
    registry = build_registry()

    async with Client(mounted_server(registry)) as client:
        result = await client.call_tool("how_to_use_axmem_tool", {"tool_name": "get_memory"})

    assert result.structured_content == registry.get_tool("get_memory").metadata.to_detailed_dict()
    assert meta_records(caplog) == [
        (META_LOGGER, logging.INFO, "how_to_use_axmem_tool: tool_name='get_memory'"),
        (META_LOGGER, logging.INFO, "how_to_use_axmem_tool: returned docs for 'get_memory'"),
    ]


async def test_how_to_use_refuses_a_tool_the_scopes_do_not_permit():
    server = mounted_server(build_registry(), permitted={"get_memory"}, scopes=frozenset({"read"}))

    async with Client(server) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool("how_to_use_axmem_tool", {"tool_name": "create_memory"})

    assert str(caught.value) == "Tool 'create_memory' is not permitted under current scopes. Required scope: 'write:memories'"


async def test_how_to_use_refuses_an_unknown_tool_and_a_name_over_one_hundred_characters():
    async with Client(mounted_server(build_registry())) as client:
        with pytest.raises(ToolError) as unknown:
            await client.call_tool("how_to_use_axmem_tool", {"tool_name": "missing"})
        with pytest.raises(ToolError) as too_long:
            await client.call_tool("how_to_use_axmem_tool", {"tool_name": "x" * 101})

    assert str(unknown.value) == (
        "Tool 'missing' not found in registry. Available tools (first 10): get_memory, create_memory, link_memories"
    )
    assert str(too_long.value) == "tool_name must be at most 100 characters"


@pytest.mark.parametrize(
    ("error", "detail"),
    [(ValueError("authored text"), "authored text"), (RuntimeError("secret internals"), GENERIC_FAILURE)],
    ids=["authored", "internal"],
)
async def test_how_to_use_reports_a_failure_with_only_the_client_safe_detail_and_logs_the_cause(caplog, monkeypatch, error, detail):
    registry = build_registry()

    def _broken(_name):
        raise error

    monkeypatch.setattr(registry, "get_tool", _broken)
    caplog.set_level(logging.INFO, logger=META_LOGGER)

    async with Client(mounted_server(registry)) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool("how_to_use_axmem_tool", {"tool_name": "get_memory"})

    assert str(caught.value) == f"Failed to get documentation for 'get_memory': {detail}"
    failure = logged_errors(caplog)
    assert [record.getMessage() for record in failure] == [f"how_to_use_axmem_tool failed for 'get_memory': {error!r}"]
    assert failure[0].exc_info is not None
    assert failure[0].exc_info[1] is error


# ---------------------------------------------------------------------------
# execute_axmem_tool
# ---------------------------------------------------------------------------


async def test_execute_runs_the_tool_with_the_arguments_and_the_request_context_and_logs_it(caplog):
    CALLS.clear()
    caplog.set_level(logging.INFO, logger=META_LOGGER)

    async with Client(mounted_server(build_registry())) as client:
        result = await client.call_tool(
            "execute_axmem_tool", {"tool_name": "get_memory", "arguments": {"memory_id": MEMORY_ID}},
        )

    assert result.structured_content == {"called_with": ["ctx", "memory_id"]}
    assert len(CALLS) == 1
    assert CALLS[0]["memory_id"] == MEMORY_ID
    assert isinstance(CALLS[0]["ctx"], Context)
    assert meta_records(caplog) == [
        (META_LOGGER, logging.INFO, "execute_axmem_tool: 'get_memory', args=['memory_id']"),
        (META_LOGGER, logging.INFO, "execute_axmem_tool: 'get_memory' executed successfully"),
    ]


async def test_execute_hands_the_tool_name_to_the_registry_by_keyword(monkeypatch):
    seen = []
    registry = build_registry()
    original = registry.execute

    async def _spy(*args, **kwargs):
        seen.append((args, sorted(kwargs)))
        return await original(*args, **kwargs)

    monkeypatch.setattr(registry, "execute", _spy)

    async with Client(mounted_server(registry)) as client:
        await client.call_tool("execute_axmem_tool", {"tool_name": "get_memory", "arguments": {}})

    assert seen == [((), ["arguments", "name"])]


async def test_execute_refuses_a_tool_outside_the_scopes_without_running_it():
    CALLS.clear()
    server = mounted_server(build_registry(), permitted={"get_memory"}, scopes=frozenset({"read"}))

    async with Client(server) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool("execute_axmem_tool", {"tool_name": "create_memory", "arguments": {"title": "t"}})

    assert str(caught.value) == "Tool 'create_memory' is not permitted under current scopes. Required scope: 'write:memories'"
    assert CALLS == []


async def test_execute_refuses_an_unknown_tool_a_long_name_and_an_argument_name_that_is_not_an_identifier():
    CALLS.clear()
    async with Client(mounted_server(build_registry())) as client:
        with pytest.raises(ToolError) as unknown:
            await client.call_tool("execute_axmem_tool", {"tool_name": "missing", "arguments": {}})
        with pytest.raises(ToolError) as too_long:
            await client.call_tool("execute_axmem_tool", {"tool_name": "x" * 101, "arguments": {}})
        with pytest.raises(ToolError) as bad_name:
            await client.call_tool("execute_axmem_tool", {"tool_name": "get_memory", "arguments": {"a b": 1}})

    assert str(unknown.value) == (
        "Tool 'missing' not found in registry. Available tools (first 10): get_memory, create_memory, link_memories"
    )
    assert str(too_long.value) == "tool_name must be at most 100 characters"
    assert str(bad_name.value) == (
        "Invalid argument name: argument names must be identifiers "
        "(letters, digits and underscores, not starting with a digit)"
    )
    assert CALLS == []


async def test_execute_logs_which_tool_was_called_with_an_argument_name_it_refused(caplog):
    caplog.set_level(logging.INFO, logger=META_LOGGER)

    async with Client(mounted_server(build_registry())) as client:
        with pytest.raises(ToolError):
            await client.call_tool("execute_axmem_tool", {"tool_name": "get_memory", "arguments": {"a b": 1}})

    assert meta_records(caplog) == [
        (META_LOGGER, logging.WARNING, "execute_axmem_tool: 'get_memory' rejected, invalid argument name"),
    ]


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (ValueError("memory_id must be a UUID, got int"), "memory_id must be a UUID, got int"),
        (RuntimeError("pool exhausted for tenant 7"), GENERIC_FAILURE),
    ],
    ids=["authored", "internal"],
)
async def test_execute_reports_a_tool_failure_with_only_the_client_safe_detail_and_logs_the_cause(caplog, error, detail):
    registry = ToolRegistry()

    async def _fails(**_kwargs):
        raise error

    registry.register(
        name="get_memory", category=ToolCategory.MEMORY, description="d", parameters=[], returns="r", implementation=_fails,
    )
    caplog.set_level(logging.INFO, logger=META_LOGGER)

    async with Client(mounted_server(registry)) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool("execute_axmem_tool", {"tool_name": "get_memory", "arguments": {}})

    assert str(caught.value) == f"Failed to execute 'get_memory': {detail}"
    failure = logged_errors(caplog)
    assert [record.getMessage() for record in failure] == [f"execute_axmem_tool failed for 'get_memory': {error!r}"]
    assert failure[0].exc_info is not None
    assert failure[0].exc_info[1] is error


async def test_execute_keeps_the_message_of_a_tool_error_the_tool_raised():
    registry = ToolRegistry()

    async def _refuses(**_kwargs):
        raise ToolError("importance_min must be an integer between 1 and 10")

    registry.register(
        name="get_recent_memories", category=ToolCategory.MEMORY, description="d", parameters=[], returns="r", implementation=_refuses,
    )

    async with Client(mounted_server(registry)) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool("execute_axmem_tool", {"tool_name": "get_recent_memories", "arguments": {}})

    assert str(caught.value) == "importance_min must be an integer between 1 and 10"
