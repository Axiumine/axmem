"""Contract of the tool registry behind the meta-tools and the CLI executors.

The registry keeps the metadata a client discovers together with the callable behind it. What it lists, what it
counts per category, how it filters by permission and category, what it passes to a tool and what it writes to
the log when a tool is replaced or fails are pinned here.
"""

import logging

import pytest

from app.models.tool_registry_models import ToolCategory, ToolParameter
from app.routes.mcp.tool_registry import ToolRegistry

REGISTRY_LOGGER = "app.routes.mcp.tool_registry"


def _logged(caplog) -> list[tuple[int, str]]:
    """What the registry logged: the level and the text of each record."""
    return [(record.levelno, record.getMessage()) for record in caplog.records if record.name == REGISTRY_LOGGER]


def _implementation(label: str = "result"):
    """An async tool that reports the keyword arguments it was called with."""

    async def tool(**kwargs):
        return {label: kwargs}

    return tool


def _register(registry: ToolRegistry, name: str, category: ToolCategory = ToolCategory.MEMORY, **fields) -> None:
    registry.register(
        name=name,
        category=category,
        description=fields.pop("description", f"{name} description"),
        parameters=fields.pop("parameters", []),
        returns=fields.pop("returns", f"{name} returns"),
        implementation=fields.pop("implementation", _implementation()),
        **fields,
    )


@pytest.fixture
def registry() -> ToolRegistry:
    """Two memory tools (one writing), one tool tagged linking, and one registered under the linking category."""
    registry = ToolRegistry()
    _register(registry, "get_memory", tags=["memory", "query"])
    _register(registry, "create_memory", tags=["memory", "create"], mutates=True)
    _register(registry, "link_memories", tags=["memory", "linking"], mutates=True)
    _register(registry, "linking_only", ToolCategory.LINKING)
    return registry


def _names(tools) -> list[str]:
    return [tool.name for tool in tools]


# ---------------------------------------------------------------------------
# registering
# ---------------------------------------------------------------------------


def test_a_new_registry_holds_nothing():
    registry = ToolRegistry()

    assert registry.list_all_tools() == []
    assert registry.list_categories() == {}
    assert registry.get_permitted_tools({"anything"}) == []
    assert registry.get_permitted_categories({"anything"}) == {}
    assert registry.get_tool("anything") is None
    assert registry.tool_exists("anything") is False


@pytest.fixture
def described() -> tuple:
    """The tool 'get_memory' registered with every optional field set, with the implementation and parameters it got."""
    registry = ToolRegistry()
    implementation = _implementation()
    parameters = [ToolParameter(name="memory_id", type="str", description="The id", required=True)]
    registry.register(
        name="get_memory",
        category=ToolCategory.MEMORY,
        description="Fetch one memory",
        parameters=parameters,
        returns="The memory",
        implementation=implementation,
        examples=["get_memory({})"],
        tags=["memory", "query"],
        mutates=True,
    )
    return registry.get_tool("get_memory"), implementation, parameters


def test_a_registered_tool_keeps_its_implementation_and_what_names_it(described):
    tool, implementation, _ = described

    assert tool is not None
    assert tool.implementation is implementation
    assert tool.metadata.name == "get_memory"
    assert tool.metadata.category is ToolCategory.MEMORY
    assert tool.metadata.description == "Fetch one memory"
    assert tool.metadata.returns == "The memory"


def test_a_registered_tool_keeps_its_parameters_examples_tags_and_mutation(described):
    tool, _, parameters = described

    assert tool is not None
    assert tool.metadata.parameters == parameters
    assert tool.metadata.examples == ["get_memory({})"]
    assert tool.metadata.tags == ["memory", "query"]
    assert tool.metadata.mutates is True


def test_a_tool_registered_without_examples_tags_or_mutation_has_none_of_them():
    registry = ToolRegistry()

    _register(registry, "get_memory")

    metadata = registry.get_tool("get_memory").metadata
    assert metadata.examples == []
    assert metadata.tags == []
    assert metadata.mutates is False


def test_the_first_registration_is_logged_for_debugging_and_not_as_a_warning(caplog):
    caplog.set_level(logging.DEBUG, logger=REGISTRY_LOGGER)

    _register(ToolRegistry(), "get_memory")

    assert _logged(caplog) == [
        (logging.DEBUG, "Registered tool: get_memory (category: memory)"),
    ]


def test_registering_a_name_again_warns_and_replaces_the_tool_in_place(registry, caplog):
    caplog.set_level(logging.DEBUG, logger=REGISTRY_LOGGER)
    replacement = _implementation("replacement")

    _register(registry, "create_memory", ToolCategory.LINKING, description="Replaced", implementation=replacement)

    assert _logged(caplog) == [
        (logging.WARNING, "Tool 'create_memory' already registered, overwriting"),
        (logging.DEBUG, "Registered tool: create_memory (category: linking)"),
    ]
    assert _names(registry.list_all_tools()) == ["get_memory", "create_memory", "link_memories", "linking_only"]
    tool = registry.get_tool("create_memory")
    assert tool.implementation is replacement
    assert tool.metadata.description == "Replaced"
    assert tool.metadata.category is ToolCategory.LINKING
    assert tool.metadata.mutates is False


# ---------------------------------------------------------------------------
# listing and counting
# ---------------------------------------------------------------------------


def test_listing_gives_the_metadata_in_registration_order(registry):
    tools = registry.list_all_tools()

    assert _names(tools) == ["get_memory", "create_memory", "link_memories", "linking_only"]
    assert [tool.mutates for tool in tools] == [False, True, True, False]


def test_categories_are_counted_by_the_category_a_tool_was_registered_under(registry):
    assert registry.list_categories() == {"memory": 3, "linking": 1}
    assert list(registry.list_categories()) == ["memory", "linking"]


def test_a_tool_exists_only_under_the_exact_name_it_was_registered_with(registry):
    assert registry.tool_exists("get_memory") is True
    assert registry.tool_exists("GET_MEMORY") is False
    assert registry.tool_exists("get_memor") is False
    assert registry.tool_exists("") is False
    assert registry.get_tool("get_memory").metadata.name == "get_memory"
    assert registry.get_tool("missing") is None


# ---------------------------------------------------------------------------
# permission and category filters
# ---------------------------------------------------------------------------


def test_permitted_tools_are_the_registered_ones_in_the_permitted_set(registry):
    assert _names(registry.get_permitted_tools({"link_memories", "get_memory", "unregistered"})) == [
        "get_memory", "link_memories",
    ]
    assert registry.get_permitted_tools(set()) == []


def test_permitted_categories_count_only_the_permitted_tools(registry):
    assert registry.get_permitted_categories({"get_memory", "link_memories", "linking_only", "unregistered"}) == {
        "memory": 2, "linking": 1,
    }
    assert registry.get_permitted_categories({"create_memory"}) == {"memory": 1}
    assert registry.get_permitted_categories(set()) == {}


def test_a_tool_is_permitted_only_if_it_exists_and_is_in_the_set(registry):
    permitted = {"get_memory", "unregistered"}

    assert registry.is_permitted("get_memory", permitted) is True
    assert registry.is_permitted("create_memory", permitted) is False
    assert registry.is_permitted("unregistered", permitted) is False
    assert registry.is_permitted("get_memory", set()) is False


def test_a_category_filter_selects_by_the_category_of_registration_and_by_permission(registry):
    everything = {"get_memory", "create_memory", "link_memories", "linking_only"}

    assert _names(registry.get_permitted_by_category(ToolCategory.MEMORY, everything)) == [
        "get_memory", "create_memory", "link_memories",
    ]
    assert _names(registry.get_permitted_by_category(ToolCategory.MEMORY, {"create_memory", "linking_only"})) == [
        "create_memory",
    ]
    assert registry.get_permitted_by_category(ToolCategory.MEMORY, set()) == []


def test_the_linking_filter_selects_by_the_linking_tag_and_by_permission(registry):
    everything = {"get_memory", "create_memory", "link_memories", "linking_only"}

    assert _names(registry.get_permitted_by_category(ToolCategory.LINKING, everything)) == ["link_memories"]
    assert registry.get_permitted_by_category(ToolCategory.LINKING, {"get_memory", "linking_only"}) == []


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


async def test_execution_calls_the_tool_with_the_arguments_and_the_context_and_returns_its_result(registry, caplog):
    caplog.set_level(logging.DEBUG, logger=REGISTRY_LOGGER)

    result = await registry.execute("get_memory", {"memory_id": "abc", "limit": 3}, request="r-1", user="u-1")

    assert result == {"result": {"memory_id": "abc", "limit": 3, "request": "r-1", "user": "u-1"}}
    assert _logged(caplog) == [
        (logging.DEBUG, "Executing tool: 'get_memory' with args: ['memory_id', 'limit']"),
        (logging.DEBUG, "Tool 'get_memory' executed successfully"),
    ]


async def test_execution_accepts_the_name_and_the_arguments_by_keyword(registry):
    assert await registry.execute(name="get_memory", arguments={}) == {"result": {}}


async def test_execution_of_an_unknown_tool_is_a_value_error_that_names_it(registry):
    with pytest.raises(ValueError) as caught:
        await registry.execute("missing", {})

    assert str(caught.value) == "Tool 'missing' not found in registry"


async def test_a_failing_tool_is_logged_with_its_traceback_and_its_error_reaches_the_caller_unchanged(caplog):
    registry = ToolRegistry()
    error = RuntimeError("boom")

    async def failing(**_kwargs):
        raise error

    _register(registry, "get_memory", implementation=failing)
    caplog.set_level(logging.DEBUG, logger=REGISTRY_LOGGER)

    with pytest.raises(RuntimeError) as caught:
        await registry.execute("get_memory", {"memory_id": "abc"})

    assert caught.value is error
    assert _logged(caplog) == [
        (logging.DEBUG, "Executing tool: 'get_memory' with args: ['memory_id']"),
        (logging.ERROR, "Tool 'get_memory' execution failed: RuntimeError('boom')"),
    ]
    failure = [record for record in caplog.records if record.name == REGISTRY_LOGGER and record.levelno == logging.ERROR]
    assert failure[0].exc_info is not None
    assert failure[0].exc_info[1] is error
