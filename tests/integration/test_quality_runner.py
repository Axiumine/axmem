"""The quality-gate runner wires every stage to a real gate and enforces ordering/skips."""

import json
import subprocess

import pytest

from scripts.quality import cli, core, registry, static


def test_every_stage_gate_is_registered():
    for stage, groups in registry.STAGES.items():
        assert groups, stage
        for group in groups:
            assert group, stage
            for name in group:
                assert name in registry.GATES, f"{stage} references unknown gate {name}"


def test_required_tools_are_gated_before_push():
    pre_push = {name for group in registry.STAGES["pre-push"] for name in group}
    assert {
        "ruff", "mypy", "pylint", "bandit", "vulture", "semgrep", "radon-cc", "radon-mi",
        "tests", "e2e-postgres", "coverage", "compat", "memray", "trivy", "qodana",
        "gitnexus", "mutation",
    } <= pre_push


def test_selected_honours_skip_only_and_opt_in(monkeypatch):
    monkeypatch.delenv("SKIP", raising=False)
    monkeypatch.delenv("ONLY", raising=False)
    monkeypatch.delenv("MUTATION", raising=False)
    assert registry.selected("ruff")
    assert not registry.selected("mutation")
    monkeypatch.setenv("MUTATION", "1")
    assert registry.selected("mutation")
    monkeypatch.setenv("SKIP", "ruff, mypy")
    assert not registry.selected("ruff")
    assert not registry.selected("mypy")
    assert registry.selected("pylint")
    monkeypatch.setenv("ONLY", "pylint")
    assert registry.selected("pylint")
    assert not registry.selected("bandit")


def test_dotenv_exposes_only_requested_keys(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "# comment\nOTHER=should-not-leak\nexport QODANA_TOKEN='tok-123'\n", encoding="utf-8",
    )
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.delenv("QODANA_TOKEN", raising=False)
    assert core.load_dotenv_values(("QODANA_TOKEN",)) == {"QODANA_TOKEN": "tok-123"}
    monkeypatch.setenv("QODANA_TOKEN", "from-env")
    assert core.load_dotenv_values(("QODANA_TOKEN",)) == {"QODANA_TOKEN": "from-env"}


def test_scoped_ctx_filters_python_files():
    ctx = core.Ctx(stage="pre-commit", files=["app/a.py", "README.md", "tests/t.py"])
    assert ctx.scoped
    assert ctx.py_files() == ["app/a.py", "tests/t.py"]
    assert ctx.py_files(("app/",)) == ["app/a.py"]
    assert not core.Ctx(stage="ci", files=None).scoped
    with pytest.raises(core.Skip):
        core.scoped_paths(core.Ctx(stage="pre-commit", files=["README.md"]), ["."], ("",))


def test_stage_stops_at_first_failing_group(monkeypatch, capsys):
    calls = []

    def fake(name, ok=True):
        def gate(_ctx):
            calls.append(name)
            return ok, f"{name} output"
        return gate

    monkeypatch.setattr(registry, "GATES", {
        "a": (fake("a"), ""), "b": (fake("b", ok=False), ""), "c": (fake("c"), ""),
    })
    monkeypatch.setattr(registry, "STAGES", {"ci": [["a"], ["b"], ["c"]]})
    monkeypatch.delenv("SKIP", raising=False)
    monkeypatch.delenv("ONLY", raising=False)
    assert cli.run_stage("ci") == 1
    assert calls == ["a", "b"]
    assert "b output" in capsys.readouterr().out

    calls.clear()
    monkeypatch.setenv("SKIP", "b")
    assert cli.run_stage("ci") == 0
    assert calls == ["a", "c"]
    assert cli.run_stage("nope") == 2


def test_skip_reason_is_reported_not_failed(monkeypatch):
    def gate(_ctx):
        raise core.Skip("tool missing")

    monkeypatch.setattr(registry, "GATES", {"x": (gate, "")})
    result = cli.run_gate("x", core.Ctx(stage="ci", files=None))
    assert (result.status, result.note) == ("SKIP", "tool missing")


def test_radon_cc_gate_rejects_rank_c_functions_but_not_classes(monkeypatch):
    payload = {
        "app/x.py": [
            {"type": "class", "rank": "F", "name": "Big", "lineno": 1, "complexity": 99},
            {"type": "function", "rank": "B", "name": "ok", "lineno": 5, "complexity": 8},
            {"type": "method", "rank": "C", "name": "Bad.go", "lineno": 9, "complexity": 12},
        ],
    }
    monkeypatch.setattr(static, "run", lambda *a, **k: (0, json.dumps(payload)))
    ok, out = static.gate_radon_cc(core.Ctx(stage="ci", files=None))
    assert not ok
    assert "Bad.go" in out
    assert "Big" not in out
    assert "ok " not in out


def test_radon_mi_gate_requires_grade_a(monkeypatch):
    payload = {"app/a.py": {"mi": 80.0, "rank": "A"}, "app/b.py": {"mi": 12.5, "rank": "B"}}
    monkeypatch.setattr(static, "run", lambda *a, **k: (0, json.dumps(payload)))
    ok, out = static.gate_radon_mi(core.Ctx(stage="ci", files=None))
    assert not ok
    assert "app/b.py" in out
    assert "app/a.py" not in out


def test_install_hooks_points_git_at_githooks(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S603, S607
    hooks = tmp_path / ".githooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cli.install_hooks() == 0
    value = subprocess.run(  # noqa: S603
        ["git", "-C", str(tmp_path), "config", "core.hooksPath"],  # noqa: S607
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert value == ".githooks"
    assert cli.uninstall_hooks() == 0
