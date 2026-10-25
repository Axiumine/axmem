"""Integration tests for what the tool commands print, send and return: tools, call and the memory verbs.

The commands run end to end from the parsed arguments through the production RemoteExecutor; the one external
boundary, the fastmcp client that talks HTTP to the server, is replaced with a recording stub. Every assertion is on
what an operator or a script reads (stdout, stderr, the exit code) and on what the server is sent.
"""
import argparse
import json

import pytest

from app.config.settings import settings
from app.routes.cli import operator_commands, remote_executor, render
from app.routes.cli.parser import (
    _run_auth_command,
    _run_tool_command,
    build_parser,
    dispatch,
)

SERVER = "http://remote:8020"
MEMORY_ID = "0b0a5a3e-6f4f-4a36-9d0e-5f3bd6c1a001"
TOKEN_ERROR = "AXMEM_TOKEN contains control or non-ASCII characters; fix the value or unset it"


class StubResult:
    def __init__(self, data):
        self.data = data
        self.content = []


class StubClient:
    """Stands in for fastmcp.Client: records the tool calls, answers with a canned payload or raises."""

    def __init__(self, response=None, error=None):
        self.calls = []
        self.closed = False
        self._response = response
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self._error:
            raise self._error
        return StubResult(self._response)

    async def close(self):
        self.closed = True


@pytest.fixture
def remote(monkeypatch):
    """Install a client for the next RemoteExecutor; the test sets its answer."""
    monkeypatch.delenv("AXMEM_TOKEN", raising=False)
    holder = {"client": StubClient()}
    monkeypatch.setattr(remote_executor, "_default_client_factory", lambda url, token: holder["client"])

    def answer(response=None, error=None):
        holder["client"] = StubClient(response, error)
        return holder["client"]

    return answer


def _argv(*words):
    return [*words, "--server", SERVER]


TOOLS_PAYLOAD = {"tools_by_category": {"memory": ["query_memory"]}, "total_count": 1}
MEMORY_LIST = [{"id": 4, "title": "First", "importance": 8}, {"id": 5, "title": "Second"}]
MEMORY_LINES = "#1  [8]  First  (memory 4)\n#2  [?]  Second  (memory 5)"


TOOL_COMMAND_CASES = [
    (["tools", "list"], "discover_axmem_tools", {}, TOOLS_PAYLOAD, json.dumps(TOOLS_PAYLOAD, indent=2)),
    (
        ["tools", "list", "--category", "memory"], "discover_axmem_tools", {"category": "memory"}, TOOLS_PAYLOAD,
        json.dumps(TOOLS_PAYLOAD, indent=2),
    ),
    (
        ["tools", "info", "query_memory"], "how_to_use_axmem_tool", {"tool_name": "query_memory"}, {"name": "query_memory"},
        '{\n  "name": "query_memory"\n}',
    ),
    (
        ["call", "list_projects", "--args", '{"limit": 2}'], "execute_axmem_tool",
        {"tool_name": "list_projects", "arguments": {"limit": 2}}, {"projects": []}, '{\n  "projects": []\n}',
    ),
    (
        ["call", "list_projects"], "execute_axmem_tool", {"tool_name": "list_projects", "arguments": {}}, {"projects": []},
        '{\n  "projects": []\n}',
    ),
    (
        ["memory", "search", "needle", "-c", "why", "-n", "3", "-p", "alpha"], "execute_axmem_tool",
        {
            "tool_name": "query_memory",
            "arguments": {"query": "needle", "query_context": "why", "k": 3, "project": "alpha"},
        },
        {"primary_memories": MEMORY_LIST}, MEMORY_LINES,
    ),
    (
        ["memory", "search", "needle", "-c", "why"], "execute_axmem_tool",
        {"tool_name": "query_memory", "arguments": {"query": "needle", "query_context": "why"}},
        {"primary_memories": []}, "No memories found.",
    ),
    (
        ["memory", "save", "the body", "--title", "A title", "--importance", "7", "-p", "alpha"], "execute_axmem_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": "A title", "content": "the body", "context": "Saved from the Axmem CLI", "keywords": [],
                "tags": [], "importance": 7, "project": "alpha",
            },
        },
        {"id": 9, "title": "A title"}, "Created memory 9: A title",
    ),
    (
        ["memory", "get", MEMORY_ID], "execute_axmem_tool", {"tool_name": "get_memory", "arguments": {"memory_id": MEMORY_ID}},
        {"id": 6, "title": "Found", "importance": 3, "tags": ["a", "b"], "context": "ctx", "content": "body"},
        "Memory 6: Found\nImportance: 3    Tags: a, b\nContext: ctx\n\nbody",
    ),
    (
        ["memory", "recent"], "execute_axmem_tool",
        {"tool_name": "get_recent_memories", "arguments": {"limit": 10}}, {"memories": MEMORY_LIST}, MEMORY_LINES,
    ),
    (
        ["memory", "recent", "-n", "2", "-p", "alpha"], "execute_axmem_tool",
        {"tool_name": "get_recent_memories", "arguments": {"limit": 2, "project": "alpha"}}, {"memories": []},
        "No memories found.",
    ),
]


JSON_COMMAND_CASES = [case[:4] for case in TOOL_COMMAND_CASES]


def _case_id(value):
    return " ".join(value) if isinstance(value, list) and all(isinstance(word, str) for word in value) else None


@pytest.mark.parametrize(
    ("argv", "meta_tool", "sent", "response", "human"), TOOL_COMMAND_CASES, ids=_case_id,
)
async def test_a_tool_command_sends_its_request_and_prints_the_answer(remote, capsys, argv, meta_tool, sent, response, human):
    client = remote(response)

    code = await _run_tool_command(build_parser().parse_args(_argv(*argv)))

    captured = capsys.readouterr()
    assert code == 0
    assert client.calls == [(meta_tool, sent)]
    assert captured.out == human + "\n"
    assert captured.err == ""
    assert client.closed


@pytest.mark.parametrize(("argv", "meta_tool", "sent", "response"), JSON_COMMAND_CASES, ids=_case_id)
async def test_a_tool_command_with_json_sends_the_same_request_and_prints_the_answer_as_one_json_line(
    remote, capsys, argv, meta_tool, sent, response,
):
    client = remote(response)

    code = await _run_tool_command(build_parser().parse_args(_argv(*argv, "--json")))

    captured = capsys.readouterr()
    assert code == 0
    assert client.calls == [(meta_tool, sent)]
    assert captured.out == json.dumps(response) + "\n"
    assert captured.err == ""
    assert client.closed


@pytest.mark.parametrize(
    ("argv", "failing_call"),
    [
        (["tools", "list"], "list"),
        (["tools", "info", "query_memory"], "info"),
        (["call", "list_projects"], "call"),
        (["memory", "recent"], "verb"),
    ],
)
@pytest.mark.parametrize(("flags", "shown"), [([], "Error: boom\n"), (["--json"], '{"error": "boom"}\n')])
async def test_a_failing_tool_command_prints_the_error_on_stderr_and_exits_1(
    remote, capsys, argv, failing_call, flags, shown,
):
    client = remote(error=RuntimeError("boom"))

    code = await _run_tool_command(build_parser().parse_args(_argv(*argv, *flags)))

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err == shown
    assert client.closed, f"{failing_call} leaves the connection open"


@pytest.mark.parametrize(("flags", "shown"), [([], f"Error: {TOKEN_ERROR}\n"), (["--json"], json.dumps({"error": TOKEN_ERROR}) + "\n")])
async def test_a_token_that_cannot_be_sent_ends_the_command_with_the_message_in_the_requested_form(
    remote, monkeypatch, capsys, flags, shown,
):
    client = remote({})
    monkeypatch.setenv("AXMEM_TOKEN", "bad\ntoken")

    code = await _run_tool_command(build_parser().parse_args(_argv("tools", "list", *flags)))

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err == shown
    assert client.calls == []


def _watch_output_form(monkeypatch, name):
    """Record the ``as_json`` that the command asks the named renderer for, and let the renderer run."""
    asked = []
    real = getattr(render, name)

    def watching(value, as_json):
        asked.append(as_json)
        return real(value, as_json)

    monkeypatch.setattr(render, name, watching)
    return asked


async def test_arguments_without_output_options_print_human_output_to_the_configured_server(remote, monkeypatch, capsys):
    """A namespace that carries none of --json, --local and --server is the plain command on AXMEM_SERVER."""
    monkeypatch.setattr(settings, "AXMEM_SERVER", SERVER)
    client = remote({"projects": []})
    asked = _watch_output_form(monkeypatch, "render_result")

    code = await _run_tool_command(argparse.Namespace(command="call", tool="list_projects", args={}))

    assert code == 0
    assert client.calls == [("execute_axmem_tool", {"tool_name": "list_projects", "arguments": {}})]
    assert capsys.readouterr().out == '{\n  "projects": []\n}\n'
    assert asked == [False]


async def test_a_failure_of_arguments_without_output_options_is_reported_in_the_human_form(remote, monkeypatch, capsys):
    monkeypatch.setattr(settings, "AXMEM_SERVER", SERVER)
    remote(error=RuntimeError("boom"))
    asked = _watch_output_form(monkeypatch, "emit_error")

    code = await _run_tool_command(argparse.Namespace(command="call", tool="list_projects", args={}))

    assert code == 1
    assert capsys.readouterr().err == "Error: boom\n"
    assert asked == [False]


@pytest.mark.parametrize(
    ("argv", "response", "expected"),
    [
        (["tools", "list"], TOOLS_PAYLOAD, json.dumps(TOOLS_PAYLOAD, indent=2)),
        (["call", "list_projects"], {"projects": []}, '{\n  "projects": []\n}'),
        (["memory", "recent"], {"memories": MEMORY_LIST}, MEMORY_LINES),
    ],
)
def test_dispatch_runs_the_tool_commands(remote, capsys, argv, response, expected):
    remote(response)

    assert dispatch(_argv(*argv), serve_runner=None, reembed_runner=None) == 0

    assert capsys.readouterr().out == expected + "\n"


@pytest.mark.parametrize(
    "argv", [["backup"], ["restore", "set-1"], ["export", "out.jsonl"], ["import", "in.jsonl"], ["qdrant-check"]],
    ids=lambda argv: argv[0],
)
def test_dispatch_hands_every_operator_command_to_the_operator_runner(monkeypatch, argv):
    seen = []

    async def runner(args):
        seen.append(args.command)
        return 7

    monkeypatch.setattr(operator_commands, "run_operator_command", runner)

    assert dispatch(argv, serve_runner=None, reembed_runner=None) == 7

    assert seen == [argv[0]]


@pytest.mark.parametrize("server", ["ftp://files.example", "gopher://old.example"])
async def test_a_server_url_that_is_refused_is_one_plain_error_line_and_exit_1(capsys, monkeypatch, server):
    asked = _watch_output_form(monkeypatch, "emit_error")

    code = await _run_auth_command(build_parser().parse_args(["auth", "login", "--server", server]))

    captured = capsys.readouterr()
    assert asked == [False]
    assert code == 1
    assert captured.out == ""
    assert captured.err == f"Error: Unsupported server URL scheme: '{server}'\n"


async def test_a_settings_file_that_cannot_be_written_is_one_plain_error_line_naming_the_file_and_exit_1(
    monkeypatch, capsys,
):
    async def login(server):
        raise PermissionError(13, "Permission denied", "/home/ops/.config/axmem/.env")

    monkeypatch.setattr("app.routes.cli.auth_commands.login", login)
    asked = _watch_output_form(monkeypatch, "emit_error")

    code = await _run_auth_command(build_parser().parse_args(["auth", "login", "--server", SERVER]))

    captured = capsys.readouterr()
    assert asked == [False]
    assert code == 1
    assert captured.out == ""
    assert captured.err == "Error: Permission denied: /home/ops/.config/axmem/.env\n"


async def test_an_os_error_without_a_file_name_is_reported_by_its_reason_alone(monkeypatch, capsys):
    async def status():
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("app.routes.cli.auth_commands.status", status)

    code = await _run_auth_command(build_parser().parse_args(["auth", "status"]))

    assert code == 1
    assert capsys.readouterr().err == "Error: No space left on device\n"


async def test_the_server_given_to_login_reaches_the_login_command(monkeypatch):
    seen = []

    async def login(server):
        seen.append(server)
        return 0

    monkeypatch.setattr("app.routes.cli.auth_commands.login", login)

    assert await _run_auth_command(build_parser().parse_args(["auth", "login", "--server", SERVER])) == 0

    assert seen == [SERVER]
