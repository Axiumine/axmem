"""E2E tests for the curated CLI verbs (docs/dev/cli_plan.md phase 5)

Full-stack cli() invocations over a file-backed SQLite database: memory save/get/
search/recent, including the project scope flag.
"""
import json
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


@pytest.fixture
def cli_sqlite_env(tmp_path, monkeypatch, tensorrt_reranker):
    original = {name: getattr(settings, name) for name in _PATCHED_FIELDS}
    settings.DATABASE = "SQLite"
    settings.SQLITE_MEMORY = False
    settings.SQLITE_PATH = str(tmp_path / "cli-verbs-e2e.db")
    settings.EMBEDDING_PROVIDER = "FastEmbed"
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


def _save(monkeypatch, capsys, content, title, *extra):
    return invoke_cli(
        monkeypatch, capsys, ["memory", "save", content, "--title", title, *extra],
    )


def test_memory_save_prints_id_and_get_shows_it(cli_sqlite_env, monkeypatch, capsys):
    code, out, _ = _save(
        monkeypatch, capsys,
        "Use uv tool install for pinned global CLI tooling", "Pinned CLI tooling",
    )
    assert code == 0
    memory_id = int(out.split("memory ")[1].split(":")[0])

    code, out, _ = invoke_cli(monkeypatch, capsys, ["memory", "get", str(memory_id)])
    assert code == 0
    assert f"Memory {memory_id}: Pinned CLI tooling" in out
    assert "Use uv tool install" in out


def test_memory_search_finds_saved_memory_in_human_format(cli_sqlite_env, monkeypatch, capsys):
    code, out, _ = _save(
        monkeypatch, capsys,
        "Set generateResolvConf false to fix WSL2 DNS", "WSL2 DNS resolution fix",
        "--importance", "8",
    )
    assert code == 0

    code, out, _ = invoke_cli(
        monkeypatch, capsys,
        ["memory", "search", "WSL2 DNS", "-c", "fixing wsl networking"],
    )
    assert code == 0
    line = next(ln for ln in out.splitlines() if "WSL2 DNS resolution fix" in ln)
    assert line.startswith("#1")
    assert "[8]" in line
    assert "(memory " in line


def test_memory_search_json_mode_emits_full_payload(cli_sqlite_env, monkeypatch, capsys):
    _save(monkeypatch, capsys, "JSON payload content", "JSON payload memory")

    code, out, _ = invoke_cli(
        monkeypatch, capsys,
        ["memory", "search", "JSON payload", "--json", "-c", "verifying json output"],
    )

    assert code == 0
    payload = json.loads(out)
    assert any(m["title"] == "JSON payload memory" for m in payload["primary_memories"])


def test_memory_recent_orders_newest_first_and_respects_limit(
    cli_sqlite_env, monkeypatch, capsys,
):
    for index in range(1, 5):
        _save(monkeypatch, capsys, f"Recent content {index}", f"Recent memory {index}")

    code, out, _ = invoke_cli(monkeypatch, capsys, ["memory", "recent", "-n", "3"])

    assert code == 0
    lines = [ln for ln in out.splitlines() if "(memory " in ln]
    assert len(lines) == 3
    assert "Recent memory 4" in lines[0]
    assert "Recent memory 3" in lines[1]
    assert "Recent memory 2" in lines[2]


def test_project_flag_scopes_save_search_and_recent(cli_sqlite_env, monkeypatch, capsys):
    code, out, _ = _save(
        monkeypatch, capsys,
        "Memory that belongs to the scoped project", "Scoped memory",
        "-p", "Scoped project",
    )
    assert code == 0
    _save(monkeypatch, capsys, "Memory that belongs to no project", "Unscoped memory")

    code, out, _ = invoke_cli(
        monkeypatch, capsys,
        ["memory", "search", "memory that belongs", "-p", "Scoped project",
         "-c", "checking project scoping"],
    )
    assert code == 0
    assert "Scoped memory" in out

    code, out, _ = invoke_cli(
        monkeypatch, capsys, ["memory", "recent", "-p", "Scoped project", "--json"],
    )
    assert code == 0
    assert [m["title"] for m in json.loads(out)["memories"]] == ["Scoped memory"]


def test_project_flag_creates_the_scope_on_first_use(cli_sqlite_env, monkeypatch, capsys):
    code, out, _ = invoke_cli(
        monkeypatch, capsys, ["memory", "recent", "-p", "never used before", "--json"],
    )

    assert code == 0
    assert json.loads(out)["memories"] == []


def test_project_commands_are_gone(cli_sqlite_env, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["forgetful", "project", "list"])

    with pytest.raises(SystemExit) as excinfo:
        main.cli()

    assert excinfo.value.code == 2
    assert "unrecognized arguments: project list" in capsys.readouterr().err


def test_memory_search_without_context_exits_two(cli_sqlite_env, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["forgetful", "memory", "search", "WSL2 DNS"])

    with pytest.raises(SystemExit) as excinfo:
        main.cli()

    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--context" in captured.err


@pytest.fixture
def alices_memory(cli_sqlite_env, monkeypatch, capsys):
    """A memory saved by the tenant alice in a scope that bob also uses; returns its id."""
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "alice")
    code, out, _ = _save(
        monkeypatch, capsys, "Alice keeps this to herself", "Alice private", "-p", "shared-scope",
    )
    assert code == 0
    return int(out.split("memory ")[1].split(":")[0])


def test_another_tenant_cannot_get_a_memory(alices_memory, monkeypatch, capsys):
    """Local mode serves the DEFAULT_TENANT tenant; another tenant gets 'not found', exactly as for an unknown id."""
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "bob")

    code, out, err = invoke_cli(monkeypatch, capsys, ["memory", "get", str(alices_memory)])

    assert code == 1
    assert "not found" in err.lower()
    assert "Alice private" not in out + err


@pytest.mark.parametrize("scope_args", [["-p", "shared-scope"], []], ids=["same-scope-name", "no-scope"])
def test_another_tenant_sees_none_of_the_memories_when_listing(alices_memory, monkeypatch, capsys, scope_args):
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "bob")

    code, out, _ = invoke_cli(monkeypatch, capsys, ["memory", "recent", *scope_args, "--json"])

    assert code == 0
    assert json.loads(out)["memories"] == []


def test_another_tenant_does_not_find_the_memory_by_searching(alices_memory, monkeypatch, capsys):
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "bob")

    code, out, _ = invoke_cli(
        monkeypatch, capsys,
        ["memory", "search", "Alice keeps this", "-p", "shared-scope", "-c", "probing another tenant", "--json"],
    )

    assert code == 0
    assert "Alice private" not in out


def test_the_owner_still_reads_the_memory_after_another_tenant_probed_it(alices_memory, monkeypatch, capsys):
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "bob")
    invoke_cli(monkeypatch, capsys, ["memory", "get", str(alices_memory)])

    monkeypatch.setattr(settings, "DEFAULT_TENANT", "alice")
    code, out, _ = invoke_cli(monkeypatch, capsys, ["memory", "get", str(alices_memory)])

    assert code == 0
    assert "Alice private" in out
