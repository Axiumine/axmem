"""E2E tests: INFO logging must not break requests.

A log call that passes a key reserved by ``logging.LogRecord`` in ``extra`` makes
``Logger.makeRecord`` raise ``KeyError``, so the request crashes whenever INFO is enabled
for the module logger.

Logging configuration applied while the application starts can disable the app loggers that already
exist and reset the root level, which hides the crash in the default test setup. The fixtures below
enable INFO on every app logger after the app has started, which is the state the affected code runs
in once those loggers are live.
"""

import contextlib
import logging

import pytest


def _app_loggers():
    """Every logger of the application that exists now."""
    return [name for name in logging.root.manager.loggerDict if name == "app" or name.startswith("app.")]


@contextlib.contextmanager
def _info_enabled(caplog):
    saved = []
    for name in _app_loggers():
        module_logger = logging.getLogger(name)
        saved.append((module_logger, module_logger.level, module_logger.disabled))
        module_logger.disabled = False
        module_logger.setLevel(logging.INFO)
    caplog.set_level(logging.INFO)
    try:
        yield caplog
    finally:
        for module_logger, level, disabled in saved:
            module_logger.setLevel(level)
            module_logger.disabled = disabled


@pytest.fixture
def info_logging_http(http_client, caplog):
    """INFO enabled once the app behind ``http_client`` has finished starting up."""
    with _info_enabled(caplog) as captured:
        yield captured


@pytest.fixture
def info_logging_mcp(mcp_client, caplog):
    """INFO enabled once the app behind ``mcp_client`` has finished starting up."""
    with _info_enabled(caplog) as captured:
        yield captured


def _messages(caplog, logger_name: str) -> set[str]:
    return {r.getMessage() for r in caplog.records if r.name == logger_name}


MEMORY = {
    "title": "info-logging-memory",
    "content": "Created and listed while INFO logging is enabled",
    "context": "reserved logrecord keys",
    "keywords": ["logging"],
    "tags": ["e2e"],
    "importance": 5,
    "project": "info-logging-project",
}


@pytest.mark.asyncio
async def test_memory_requests_via_api_with_info_logging(http_client, info_logging_http):
    created = await http_client.post("/api/v1/memories", json=MEMORY)
    assert created.status_code == 201, created.text

    response = await http_client.get("/api/v1/memories", params={"project": MEMORY["project"]})

    assert response.status_code == 200, response.text
    assert [m["title"] for m in response.json()["memories"]] == [MEMORY["title"]]

    searched = await http_client.post(
        "/api/v1/memories/search", json={"query": "info logging", "query_context": "logging", "k": 3},
    )

    assert searched.status_code == 200, searched.text
    assert "querying primary memories" in _messages(info_logging_http, "app.services.memory_service")


@pytest.mark.asyncio
async def test_memory_tools_via_mcp_with_info_logging(mcp_client, info_logging_mcp):
    await mcp_client.call_tool(
        "execute_axmem_tool", {"tool_name": "create_memory", "arguments": MEMORY},
    )

    result = await mcp_client.call_tool(
        "execute_axmem_tool",
        {"tool_name": "get_recent_memories", "arguments": {"project": MEMORY["project"]}},
    )

    assert [m["title"] for m in result.data["memories"]] == [MEMORY["title"]]

    await mcp_client.call_tool(
        "execute_axmem_tool",
        {"tool_name": "query_memory", "arguments": {"query": "info logging", "query_context": "logging", "k": 3}},
    )
    record = next(
        r for r in info_logging_mcp.records
        if r.name == "app.services.memory_service" and r.getMessage() == "querying primary memories"
    )
    assert record.query_length == len("info logging")  # the length, not the text of the query
