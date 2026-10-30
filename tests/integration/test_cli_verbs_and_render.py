# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the CLI project scope and human-output rendering

Covers the -p scope string (passed to the tool as typed, never resolved or dropped),
terminal-safe rendering of stored text, the obsolete marker in `memory get` and stdout
encoding safety. Verbs run against a recording executor, so no database or embedding
model is involved.
"""
import io
import re

import pytest

from app.routes.cli import render, verbs
from app.routes.cli.parser import build_parser

RAW_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")
EVIL = "evil\x1b[2J\x1b]0;pwned\x07\n#99  [10]  Fake injected row"


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


# -- project scope --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "tool"),
    [
        (["memory", "search", "q", "-c", "ctx", "-p", "My Project"], "query_memory"),
        (["memory", "save", "body", "--title", "T", "-p", "My Project"], "create_memory"),
        (["memory", "recent", "-p", "My Project"], "get_recent_memories"),
    ],
)
async def test_project_scope_is_passed_to_the_tool_as_typed(argv, tool):
    executor = RecordingExecutor({tool: {"id": 1, "title": "T"}})

    await verbs.run(executor, _args(argv))

    assert executor.calls[-1][0] == tool
    assert executor.calls[-1][1]["project"] == "My Project"
    assert [name for name, _ in executor.calls] == [tool], "no lookup call resolves the scope"


@pytest.mark.parametrize(
    ("argv", "tool"),
    [
        (["memory", "search", "q", "-c", "ctx"], "query_memory"),
        (["memory", "save", "body", "--title", "T"], "create_memory"),
        (["memory", "recent"], "get_recent_memories"),
    ],
)
async def test_no_project_flag_sends_no_scope(argv, tool):
    executor = RecordingExecutor({tool: {"id": 1, "title": "T"}})

    await verbs.run(executor, _args(argv))

    assert "project" not in executor.calls[-1][1]


@pytest.mark.parametrize(
    ("argv", "tool"),
    [
        (["memory", "search", "q", "-c", "ctx", "-p", ""], "query_memory"),
        (["memory", "save", "body", "--title", "T", "-p", ""], "create_memory"),
        (["memory", "recent", "-p", ""], "get_recent_memories"),
    ],
)
async def test_empty_project_value_is_sent_for_the_server_to_reject_not_dropped(argv, tool):
    executor = RecordingExecutor({tool: {"id": 1, "title": "T"}})

    await verbs.run(executor, _args(argv))

    assert executor.calls[-1][1]["project"] == ""


# -- terminal-safe rendering ----------------------------------------------------------


def test_memory_lines_cannot_forge_extra_rows_or_emit_control_bytes():
    output = render.render_memory_lines([{"id": 1, "title": EVIL, "importance": 5}])

    assert not RAW_CONTROL.search(output)
    assert len(output.splitlines()) == 1
    assert output.startswith("#1  [5]  evil")
    assert output.endswith("(memory 1)")
    assert "\\x1b[2J" in output  # the attempt stays visible, just inert


def test_memory_detail_escapes_control_characters_in_every_field():
    memory = {
        "id": 3,
        "title": EVIL,
        "importance": 5,
        "tags": ["ok", "bad\x1b[31mtag\n"],
        "context": "ctx\x1b[2J\r\nmore",
        "content": "line one\n\tindented\r\nline three\x1b[31mred\x9b\u2028end\rboom",
    }

    output = render.render_memory_detail(memory)

    assert not RAW_CONTROL.search(output.replace("\n", "").replace("\t", ""))
    assert [ln for ln in output.splitlines() if ln.startswith("#")] == []
    assert "Memory 3: evil" in output
    assert "bad\\x1b[31mtag\\n" in output
    # content keeps its own line structure (newline, tab, CRLF folded to LF)
    assert "line one\n\tindented\nline three" in output
    assert "\\x1b[31mred\\x9b\\u2028end\\rboom" in output


async def test_save_confirmation_line_escapes_the_title():
    executor = RecordingExecutor({"create_memory": {"id": 9, "title": EVIL}})

    _, human = await verbs.run(executor, _args(["memory", "save", "body", "--title", EVIL]))

    assert human.startswith("Created memory 9: evil")
    assert not RAW_CONTROL.search(human)
    assert len(human.splitlines()) == 1


# -- obsolete marker ------------------------------------------------------------------


def test_memory_detail_flags_obsolete_memory_with_reason_and_replacement():
    output = render.render_memory_detail({
        "id": 1,
        "title": "R1",
        "importance": 5,
        "content": "stale",
        "is_obsolete": True,
        "obsolete_reason": "wrong, replaced",
        "superseded_by": 7,
    })

    lines = output.splitlines()
    assert lines[0] == "Memory 1: R1"
    assert lines[1] == "OBSOLETE: wrong, replaced (superseded by memory 7)"


def test_memory_detail_flags_obsolete_memory_without_reason_or_replacement():
    output = render.render_memory_detail(
        {"id": 1, "title": "R1", "is_obsolete": True, "obsolete_reason": None, "superseded_by": None},
    )

    assert output.splitlines()[1] == "OBSOLETE: no reason recorded"


def test_memory_detail_does_not_flag_active_memories():
    output = render.render_memory_detail(
        {"id": 1, "title": "R1", "is_obsolete": False, "obsolete_reason": None},
    )

    assert "OBSOLETE" not in output
    assert "OBSOLETE" not in render.render_memory_detail({"id": 2, "title": "No flag"})


def test_obsolete_reason_is_escaped_too():
    output = render.render_memory_detail(
        {"id": 1, "title": "R1", "is_obsolete": True, "obsolete_reason": "bad\x1b[2J\nreason"},
    )

    assert output.splitlines()[1] == "OBSOLETE: bad\\x1b[2J\\nreason"


# -- stdout encoding safety -----------------------------------------------------------


def _strict_stdout(encoding="cp1252", errors="strict"):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding=encoding, errors=errors, newline="\n", write_through=True)
    return stream, raw


def test_ensure_safe_stdout_makes_strict_streams_replace_unencodable_text(monkeypatch):
    stream, raw = _strict_stdout()
    monkeypatch.setattr("sys.stdout", stream)

    render.ensure_safe_stdout()
    print("Arrow \u2192 done")

    assert raw.getvalue() == b"Arrow ? done\n"


def test_ensure_safe_stdout_keeps_an_explicit_error_handler(monkeypatch):
    stream, raw = _strict_stdout(errors="backslashreplace")
    monkeypatch.setattr("sys.stdout", stream)

    render.ensure_safe_stdout()
    print("\u2192")

    assert raw.getvalue() == b"\\u2192\n"


def test_ensure_safe_stdout_tolerates_streams_without_reconfigure(monkeypatch):
    class StrictWithoutReconfigure:
        errors = "strict"

    monkeypatch.setattr("sys.stdout", StrictWithoutReconfigure())
    render.ensure_safe_stdout()  # must not raise

    monkeypatch.setattr("sys.stdout", io.StringIO())
    render.ensure_safe_stdout()  # must not raise
