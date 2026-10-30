# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""Integration tests for curated verb flag -> tool-argument mapping (phase 5)

Verbs are exercised through the real argparse tree against a recording executor, so
these tests pin the exact registry arguments each flag combination produces -
including the project scope string, which is passed through as typed.
"""
import pytest

from app.routes.cli import verbs
from app.routes.cli.parser import build_parser


class RecordingExecutor:
    """ToolExecutor double that records execute() calls and returns canned payloads."""

    def __init__(self, responses=None):
        self.calls = []
        self._responses = dict(responses or {})

    async def execute(self, tool_name, arguments):
        self.calls.append((tool_name, dict(arguments)))
        return self._responses.get(tool_name, {})

    async def close(self):
        pass


def _args(argv):
    return build_parser().parse_args(argv)


QUERY_RESULT = {"query": "q", "primary_memories": [], "linked_memories": [], "total_count": 0, "token_count": 0}


async def test_search_maps_flags_onto_query_memory_arguments():
    executor = RecordingExecutor({"query_memory": QUERY_RESULT})

    await verbs.run(executor, _args(
        ["memory", "search", "dns fix", "-n", "5", "-c", "debugging wsl", "-p", "homelab"],
    ))

    assert executor.calls == [
        ("query_memory", {"query": "dns fix", "query_context": "debugging wsl", "k": 5, "project": "homelab"}),
    ]


async def test_search_omits_optional_flags_when_only_context_given():
    executor = RecordingExecutor({"query_memory": QUERY_RESULT})

    await verbs.run(executor, _args(["memory", "search", "dns fix", "-c", "debugging wsl"]))

    assert executor.calls == [(
        "query_memory",
        {"query": "dns fix", "query_context": "debugging wsl"},
    )]


def test_search_without_context_flag_exits_with_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        _args(["memory", "search", "dns fix"])

    assert excinfo.value.code == 2


async def test_save_maps_flags_onto_create_memory_arguments():
    executor = RecordingExecutor({"create_memory": {"id": 9, "title": "T"}})

    await verbs.run(executor, _args(
        ["memory", "save", "some content", "--title", "T", "--importance", "8", "-p", "homelab"],
    ))

    tool_name, arguments = executor.calls[-1]
    assert tool_name == "create_memory"
    assert arguments["title"] == "T"
    assert arguments["content"] == "some content"
    assert arguments["importance"] == 8
    assert arguments["project"] == "homelab"
    assert arguments["keywords"] == []
    assert arguments["tags"] == []
    assert arguments["context"]  # a non-empty default context is supplied


MEMORY_ID = "018f2c3e-0000-7000-8000-000000000012"


async def test_get_and_recent_map_arguments():
    executor = RecordingExecutor({
        "get_memory": {"id": MEMORY_ID, "title": "T", "importance": 5},
        "get_recent_memories": {"memories": [], "total_count": 0},
    })

    await verbs.run(executor, _args(["memory", "get", MEMORY_ID]))
    await verbs.run(executor, _args(["memory", "recent", "-n", "3"]))

    assert executor.calls[0] == ("get_memory", {"memory_id": MEMORY_ID})
    assert executor.calls[1] == ("get_recent_memories", {"limit": 3})


def test_get_refuses_an_id_that_is_not_a_uuid(capsys):
    with pytest.raises(SystemExit) as excinfo:
        _args(["memory", "get", "12"])

    assert excinfo.value.code == 2
    assert "ids are UUIDs" in capsys.readouterr().err


def test_project_command_no_longer_exists():
    with pytest.raises(SystemExit) as excinfo:
        _args(["project", "list"])

    assert excinfo.value.code == 2
