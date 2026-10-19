"""Integration tests for scope parsing and resolution edge cases.

A scope string without any real token is rejected, and a scope token this server does not
know grants nothing (the ceiling fails closed instead of widening).
"""
import pytest

from app.models.tool_registry_models import ToolCategory, ToolParameter
from app.routes.mcp.scope_resolver import parse_scopes, resolve_permitted_tools
from app.routes.mcp.tool_registry import ToolRegistry


@pytest.fixture
def registry():
    """One read tool and one write tool in the memory category."""
    registry = ToolRegistry()
    params = [ToolParameter(name="ctx", type="Context", description="ctx", required=True)]

    async def noop(**kwargs):
        return {}

    registry.register(
        name="get_memory", category=ToolCategory.MEMORY, description="Get memory",
        parameters=params, returns="Memory", implementation=noop, mutates=False,
    )
    registry.register(
        name="create_memory", category=ToolCategory.MEMORY, description="Create memory",
        parameters=params, returns="Memory", implementation=noop, mutates=True,
    )
    return registry


@pytest.mark.parametrize("scope_string", [",", " , ", ",,,", ", ,\t,"])
def test_scope_string_made_only_of_separators_is_rejected_as_empty(scope_string):
    with pytest.raises(ValueError, match="Scope string cannot be empty"):
        parse_scopes(scope_string)


@pytest.mark.parametrize("unknown_token", ["admin", "superuser", "delete", "READ"])
def test_unknown_scope_token_grants_no_tool(registry, unknown_token):
    assert resolve_permitted_tools(frozenset({unknown_token}), registry) == set()


def test_unknown_scope_token_does_not_change_what_the_known_ones_grant(registry):
    permitted = resolve_permitted_tools(frozenset({"admin", "read:memories"}), registry)

    assert permitted == {"get_memory"}
