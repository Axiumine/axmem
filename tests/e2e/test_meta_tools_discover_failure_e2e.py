"""E2E tests for how ``discover_axmem_tools`` reports a failure.

A domain or validation error keeps its own message for the agent; anything else is
reduced to a generic sentence, and the full detail goes to the server log.
"""
import logging

import pytest
from fastmcp.exceptions import ToolError

from app.routes.mcp import meta_tools
from app.routes.mcp.tool_errors import GENERIC_FAILURE

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("failure", "expected_detail"),
    [
        (ValueError("category index is being rebuilt"), "category index is being rebuilt"),
        (RuntimeError("SELECT secret FROM internal_table"), GENERIC_FAILURE),
    ],
    ids=["client-safe-error-keeps-its-message", "unexpected-error-is-generic"],
)
async def test_unexpected_failure_while_discovering_tools_becomes_a_tool_error(
    mcp_client, monkeypatch, caplog, failure, expected_detail,
):
    def broken_discovery(registry, permitted, category):
        raise failure

    monkeypatch.setattr(meta_tools, "build_discovery_payload", broken_discovery)

    with caplog.at_level(logging.ERROR, logger=meta_tools.logger.name):
        with pytest.raises(ToolError) as caught:
            await mcp_client.call_tool("discover_axmem_tools", {})

    assert str(caught.value) == f"Failed to discover tools: {expected_detail}"
    logged = [r for r in caplog.records if r.getMessage().startswith("discover_axmem_tools failed")]
    assert len(logged) == 1
    assert repr(failure) in logged[0].getMessage()
