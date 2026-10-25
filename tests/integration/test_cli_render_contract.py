"""Integration tests for the exact text and JSON the CLI prints: results, errors, memory listings and memory details.

Scripts parse the `--json` forms and people read the human ones, so the shapes are fixed here to the character. The
conversion of tool results to JSON-safe data is checked with a real pydantic model, as the local executor returns them.
"""
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import BaseModel

from app.routes.cli import render, verbs
from app.routes.cli.parser import build_parser

IDENTIFIER = UUID("12345678-1234-5678-1234-567812345678")


class Stored(BaseModel):
    id: UUID
    seen: datetime
    tags: list[str]


class AnswerExecutor:
    """ToolExecutor double that answers every call with one canned payload."""

    def __init__(self, payload):
        self._payload = payload

    async def execute(self, tool_name, arguments):
        return self._payload


def test_a_pydantic_result_becomes_json_text_not_python_objects():
    model = Stored(id=IDENTIFIER, seen=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), tags=["a"])

    converted = render.to_jsonable({"found": [model]})

    assert converted == {"found": [{"id": str(IDENTIFIER), "seen": "2026-01-02T03:04:05Z", "tags": ["a"]}]}


@pytest.mark.parametrize("scalar", ["text", 3, 2.5, True, False, None])
def test_scalars_and_none_are_kept_as_they_are(scalar):
    assert render.to_jsonable(scalar) is scalar
    assert render.to_jsonable([scalar, {"k": scalar}]) == [scalar, {"k": scalar}]


def test_tuples_and_sets_become_lists():
    assert render.to_jsonable({"t": (1, 2), "s": {7}}) == {"t": [1, 2], "s": [7]}


def test_a_result_is_indented_for_people_and_compact_for_scripts():
    result = {"id": IDENTIFIER, "tags": ["a", "b"]}

    assert render.render_result(result, as_json=False) == (
        '{\n  "id": "12345678-1234-5678-1234-567812345678",\n  "tags": [\n    "a",\n    "b"\n  ]\n}'
    )
    assert render.render_result(result, as_json=True) == '{"id": "12345678-1234-5678-1234-567812345678", "tags": ["a", "b"]}'


@pytest.mark.parametrize("as_json", [False, True])
def test_a_result_of_none_is_json_null_in_both_forms(as_json):
    assert render.render_result(None, as_json) == "null"


def test_an_error_is_one_line_on_stderr_for_people_and_a_json_object_for_scripts(capsys):
    render.emit_error("disk full", as_json=False)
    human = capsys.readouterr()
    render.emit_error("disk full", as_json=True)
    scripted = capsys.readouterr()

    assert (human.out, human.err) == ("", "Error: disk full\n")
    assert (scripted.out, scripted.err) == ("", '{"error": "disk full"}\n')


def test_an_error_hides_the_credentials_of_the_urls_in_it_in_both_forms(capsys):
    render.emit_error("failed for https://ops:hunter2@host:8020/mcp", as_json=False)
    human = capsys.readouterr().err
    render.emit_error("failed for https://ops:hunter2@host:8020/mcp", as_json=True)
    scripted = capsys.readouterr().err

    assert human == f"Error: failed for https://{render.REDACTED_USERINFO}@host:8020/mcp\n"
    assert json.loads(scripted) == {"error": f"failed for https://{render.REDACTED_USERINFO}@host:8020/mcp"}


def test_control_characters_become_visible_escapes_in_ascii():
    assert render.sanitize_line("a\x1b[2Jb\u2028c\x7f") == "a\\x1b[2Jb\\u2028c\\x7f"
    assert render.sanitize_text("a\x1b\tb\r\nc") == "a\\x1b\tb\nc"


def test_no_memories_is_said_in_words():
    assert render.render_memory_lines([]) == "No memories found."


def test_memories_are_listed_one_per_line_in_rank_order_with_a_question_mark_for_a_missing_importance():
    memories = [{"id": 11, "title": "First", "importance": 10}, {"id": 12, "title": "Second"}, {"id": 13, "title": "Third", "importance": 1}]

    assert render.render_memory_lines(memories) == (
        "#1  [10]  First  (memory 11)\n#2  [?]  Second  (memory 12)\n#3  [1]  Third  (memory 13)"
    )


def test_a_memory_with_every_field_is_shown_in_full():
    memory = {
        "id": 3, "title": "Title", "importance": 8, "tags": ["alpha", "beta"], "context": "Why it matters",
        "content": "Line one\nLine two",
    }

    assert render.render_memory_detail(memory) == (
        "Memory 3: Title\nImportance: 8    Tags: alpha, beta\nContext: Why it matters\n\nLine one\nLine two"
    )


@pytest.mark.parametrize("empty", [{}, {"tags": None, "context": None, "content": None}, {"tags": [], "context": "", "content": ""}])
def test_a_memory_without_optional_fields_shows_placeholders_and_an_empty_body(empty):
    assert render.render_memory_detail({"id": 4, "title": "Bare", **empty}) == (
        "Memory 4: Bare\nImportance: ?    Tags: -\nContext: -\n\n"
    )


def test_an_obsolete_memory_is_flagged_between_its_title_and_its_fields():
    memory = {"id": 5, "title": "Old", "importance": 2, "is_obsolete": True, "obsolete_reason": "replaced", "content": "x"}

    assert render.render_memory_detail(memory) == (
        "Memory 5: Old\nOBSOLETE: replaced\nImportance: 2    Tags: -\nContext: -\n\nx"
    )


async def test_a_search_lists_the_primary_memories_of_the_answer():
    answer = {"primary_memories": [{"id": 1, "title": "Hit", "importance": 6}], "linked_memories": [{"id": 2, "title": "Other"}]}

    payload, human = await verbs.run(AnswerExecutor(answer), build_parser().parse_args(["memory", "search", "q", "-c", "why"]))

    assert payload == answer
    assert human == "#1  [6]  Hit  (memory 1)"


def _watch_listing(monkeypatch):
    """Record the memories that a verb hands to the listing, and let the listing run."""
    handed = []
    real = verbs.render_memory_lines

    def watching(memories):
        handed.append(memories)
        return real(memories)

    monkeypatch.setattr(verbs, "render_memory_lines", watching)
    return handed


async def test_a_search_without_primary_memories_says_none_were_found_from_an_empty_list(monkeypatch):
    handed = _watch_listing(monkeypatch)

    _, human = await verbs.run(AnswerExecutor({"linked_memories": [{"id": 2, "title": "Other"}]}), build_parser().parse_args(["memory", "search", "q", "-c", "why"]))

    assert human == "No memories found."
    assert handed == [[]]


async def test_the_recent_memories_are_listed_from_the_memories_of_the_answer():
    answer = {"memories": [{"id": 1, "title": "Newest", "importance": 4}], "primary_memories": [{"id": 2, "title": "Not this"}]}

    payload, human = await verbs.run(AnswerExecutor(answer), build_parser().parse_args(["memory", "recent"]))

    assert payload == answer
    assert human == "#1  [4]  Newest  (memory 1)"


async def test_recent_memories_without_a_memories_entry_says_none_were_found_from_an_empty_list(monkeypatch):
    handed = _watch_listing(monkeypatch)

    _, human = await verbs.run(AnswerExecutor({"primary_memories": [{"id": 2, "title": "Not this"}]}), build_parser().parse_args(["memory", "recent"]))

    assert human == "No memories found."
    assert handed == [[]]
