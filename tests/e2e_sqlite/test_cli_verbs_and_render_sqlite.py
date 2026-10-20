"""E2E tests for CLI project resolution and human-output safety

Full-stack cli() invocations over a file-backed SQLite database: deterministic -p
resolution, terminal-safe rendering of stored text, the obsolete marker in
`memory get` and stdout encoding safety after a committed write.
"""
import io
import json
import re
import sys

import pytest

import main
from app.config.settings import settings

_PATCHED_FIELDS = [
    "DATABASE",
    "SQLITE_MEMORY",
    "SQLITE_PATH",
    "EMBEDDING_PROVIDER",
    "RERANKING_ENABLED",
    "FORGETFUL_SCOPES",
    "FORGETFUL_SERVER",
]

RAW_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")
EVIL_TITLE = "evil\x1b[2J\x1b]0;pwned\x07\n#99  [10]  Fake injected row"


@pytest.fixture
def cli_env(tmp_path, monkeypatch, tensorrt_reranker, tensorrt_embedder):
    original = {name: getattr(settings, name) for name in _PATCHED_FIELDS}
    settings.DATABASE = "SQLite"
    settings.SQLITE_MEMORY = False
    settings.SQLITE_PATH = str(tmp_path / "cli-verbs-render-e2e.db")
    settings.EMBEDDING_PROVIDER = "TensorRT"
    settings.RERANKING_ENABLED = True  # the runtime shares the session's real TensorRT reranker
    settings.FORGETFUL_SCOPES = "*"
    settings.FORGETFUL_SERVER = ""
    monkeypatch.delenv("FORGETFUL_TOKEN", raising=False)
    yield settings
    for name, value in original.items():
        setattr(settings, name, value)


def invoke_cli(monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", ["forgetful", *argv])
    exit_code = main.cli()
    captured = capsys.readouterr()
    return exit_code or 0, captured.out, captured.err


def _call_json(monkeypatch, capsys, tool, arguments):
    code, out, err = invoke_cli(
        monkeypatch, capsys, ["call", tool, "--args", json.dumps(arguments), "--json"],
    )
    assert code == 0, err
    return json.loads(out)


def test_project_scopes_are_matched_with_exact_case(cli_env, monkeypatch, capsys):
    code, out, err = invoke_cli(
        monkeypatch, capsys, ["memory", "save", "exact case body", "--title", "t", "-p", "Alpha", "--json"],
    )
    assert code == 0, err
    assert json.loads(out)["project"] == "Alpha"

    code, out, err = invoke_cli(monkeypatch, capsys, ["memory", "recent", "-p", "Alpha", "--json"])
    assert code == 0, err
    assert [m["title"] for m in json.loads(out)["memories"]] == ["t"]

    code, out, err = invoke_cli(monkeypatch, capsys, ["memory", "recent", "-p", "alpha", "--json"])
    assert code == 0, err
    assert json.loads(out)["memories"] == []


@pytest.mark.parametrize("scope", ["2024", "²", "名前"])
def test_project_scope_is_a_plain_string_whatever_it_looks_like(cli_env, monkeypatch, capsys, scope):
    code, out, err = invoke_cli(
        monkeypatch, capsys,
        ["memory", "save", "numeric project body", "--title", "Odd scope memory", "-p", scope],
    )
    assert code == 0, err

    code, out, err = invoke_cli(monkeypatch, capsys, ["memory", "recent", "-p", scope])
    assert code == 0, err
    assert "Odd scope memory" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["memory", "save", "note body", "--title", "T", "-p", ""],
        ["memory", "recent", "-p", ""],
        ["memory", "search", "note body", "-c", "ctx", "-p", ""],
    ],
)
def test_empty_project_value_is_rejected_and_nothing_is_saved(cli_env, monkeypatch, capsys, argv):
    code, out, err = invoke_cli(monkeypatch, capsys, argv)

    assert code == 1
    assert out == ""
    assert "project" in err
    _, recent_out, _ = invoke_cli(monkeypatch, capsys, ["memory", "recent", "--json"])
    assert json.loads(recent_out)["memories"] == []


def test_save_with_unencodable_title_succeeds_on_a_cp1252_stdout(cli_env, monkeypatch):
    title = "Arrow → test"
    raw_out, raw_err = io.BytesIO(), io.BytesIO()
    strict_stdout = io.TextIOWrapper(raw_out, encoding="cp1252", errors="strict", write_through=True)
    err_stream = io.TextIOWrapper(raw_err, encoding="utf-8", write_through=True)
    monkeypatch.setattr(sys, "stdout", strict_stdout)
    monkeypatch.setattr(sys, "stderr", err_stream)
    monkeypatch.setattr(
        sys, "argv", ["forgetful", "memory", "save", "body text for the arrow memory", "--title", title],
    )

    code = main.cli() or 0

    stderr_text = raw_err.getvalue().decode("utf-8")
    assert code == 0, f"committed write reported as failure: {stderr_text!r}"
    assert "Error:" not in stderr_text
    assert raw_out.getvalue().decode("cp1252").startswith("Created memory 1: Arrow ")


def test_recent_and_get_never_replay_stored_control_characters(cli_env, monkeypatch, capsys):
    created = _call_json(monkeypatch, capsys, "create_memory", {
        "title": EVIL_TITLE,
        "content": "c\x1b[31mred\nsecond line",
        "context": "ctx\x1b[2J",
        "keywords": [],
        "tags": ["tag\x07"],
        "importance": 5,
    })

    code, out, err = invoke_cli(monkeypatch, capsys, ["memory", "recent"])
    assert code == 0, err
    assert not RAW_CONTROL.search(out.replace("\n", ""))
    rows = [ln for ln in out.splitlines() if ln.startswith("#")]
    assert len(rows) == 1, rows

    code, out, err = invoke_cli(monkeypatch, capsys, ["memory", "get", str(created["id"])])
    assert code == 0, err
    assert not RAW_CONTROL.search(out.replace("\n", ""))
    assert "second line" in out.splitlines()[-1]


def test_memory_get_flags_obsolete_memory(cli_env, monkeypatch, capsys):
    code, out, err = invoke_cli(monkeypatch, capsys, ["memory", "save", "remote content", "--title", "R1"])
    assert code == 0, err
    memory_id = int(out.split("memory ")[1].split(":")[0])
    reason = "wrong, replaced"
    _call_json(monkeypatch, capsys, "mark_memory_obsolete", {"memory_id": memory_id, "reason": reason})

    code, out, err = invoke_cli(monkeypatch, capsys, ["memory", "get", str(memory_id)])

    assert code == 0, err
    assert out.splitlines()[0] == f"Memory {memory_id}: R1"
    assert out.splitlines()[1] == f"OBSOLETE: {reason}"
