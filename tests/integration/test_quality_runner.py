"""The quality-gate runner wires every stage to a real gate and enforces ordering/skips."""

import json
import os
import re
import subprocess
import sys

import pytest

from scripts.quality import cli, core, package, registry, security, static, testing


@pytest.fixture(autouse=True)
def _no_inherited_git_environment(monkeypatch):
    """A Git hook exports GIT_DIR/GIT_INDEX_FILE; tests that init repos in tmp dirs must not inherit them."""
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(name)
    monkeypatch.delenv("QUALITY_PUSH_REMOTE", raising=False)
    # nor the configuration of whoever runs them: a global commit.gpgsign=true would sign the commits of the temporary repositories
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


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
        "tests", "e2e-qdrant", "coverage", "memray", "trivy", "qodana",
        "gitnexus", "mutation", "package", "wheel", "shellcheck", "actionlint", "architecture",
    } <= pre_push


def test_the_package_that_a_build_of_this_checkout_makes_holds_the_source_only():
    """The real build: uv builds the wheel and the sdist of the files in this checkout, and the gate reads them."""
    assert package.gate_package(core.Ctx(stage="manual", files=None)) == (True, "")


def test_the_trivy_image_is_pinned_to_a_version(monkeypatch, tmp_path):
    """Without a trivy binary the gate runs Docker; a moving tag would change its findings from one day to the next."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TRIVY_BIN", raising=False)
    monkeypatch.setattr(security.shutil, "which", lambda _name: None)
    monkeypatch.setattr(security, "docker_ready", lambda: True)

    command = security.trivy_cmd()

    images = [word for word in command if word.startswith("aquasec/trivy")]
    assert images == [security.TRIVY_IMAGE]
    assert re.fullmatch(r"aquasec/trivy:\d+\.\d+\.\d+", security.TRIVY_IMAGE)


def test_selected_honours_skip_and_only_and_leaves_no_gate_out_otherwise(monkeypatch):
    monkeypatch.delenv("SKIP", raising=False)
    monkeypatch.delenv("ONLY", raising=False)
    assert all(registry.selected(name) for name in registry.GATES)  # the slow mutation gate is no exception
    monkeypatch.setenv("SKIP", "mutation")
    assert not registry.selected("mutation")
    assert registry.left_out("mutation") == "left out by SKIP"
    monkeypatch.delenv("SKIP")
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


def stage_of(monkeypatch, names=("a", "b", "mutation")):
    """A ``ci`` stage of stand-in gates that all pass, in one group per name; returns the list of gates that ran."""
    calls = []
    monkeypatch.setattr(registry, "GATES", {n: (lambda _ctx, n=n: calls.append(n) or (True, ""), "") for n in names})
    monkeypatch.setattr(registry, "STAGES", {"ci": [[n] for n in names]})
    for variable in ("SKIP", "ONLY"):
        monkeypatch.delenv(variable, raising=False)
    return calls


def test_a_stage_names_every_gate_it_left_out_and_counts_them(monkeypatch, capsys):
    calls = stage_of(monkeypatch)
    monkeypatch.setenv("SKIP", "a")

    assert cli.run_stage("ci") == 0

    out = capsys.readouterr().out
    assert calls == ["b", "mutation"]
    assert "SKIP  a  (left out by SKIP)" in out
    assert "[ci] OK (1 gate left out)" in out
    monkeypatch.setenv("ONLY", "b")
    monkeypatch.delenv("SKIP")
    assert cli.run_stage("ci") == 0
    out = capsys.readouterr().out
    assert "SKIP  a  (left out by ONLY)" in out
    assert "SKIP  mutation  (left out by ONLY)" in out
    assert "[ci] OK (2 gates left out)" in out


def test_a_stage_with_nothing_left_out_says_plain_ok(monkeypatch, capsys):
    calls = stage_of(monkeypatch)

    assert cli.run_stage("ci") == 0

    assert calls == ["a", "b", "mutation"]
    assert capsys.readouterr().out.rstrip().endswith("[ci] OK")


def test_skip_or_only_naming_no_gate_is_refused_instead_of_leaving_a_gate_out_unseen(monkeypatch, capsys):
    calls = stage_of(monkeypatch)
    monkeypatch.setenv("SKIP", "a,shelcheck")

    assert cli.run_stage("ci") == 2
    assert "shelcheck" in capsys.readouterr().err
    monkeypatch.delenv("SKIP")
    monkeypatch.setenv("ONLY", "typo")
    assert cli.run_stage("ci") == 2
    assert calls == []


def test_only_that_selects_no_gate_of_the_stage_is_refused(monkeypatch, capsys):
    calls = stage_of(monkeypatch)
    monkeypatch.setitem(registry.GATES, "elsewhere", (lambda _ctx: (True, ""), ""))
    monkeypatch.setenv("ONLY", "elsewhere")

    assert cli.run_stage("ci") == 2
    assert "selects no gate of the ci stage" in capsys.readouterr().err
    assert calls == []


def test_a_gate_that_raises_fails_and_the_others_of_its_group_still_report(monkeypatch, capsys):
    def broken(_ctx):
        raise KeyError("python")

    monkeypatch.setattr(registry, "GATES", {"broken": (broken, ""), "fine": (lambda _ctx: (True, ""), "")})
    monkeypatch.setattr(registry, "STAGES", {"ci": [["broken", "fine"]]})
    for variable in ("SKIP", "ONLY"):
        monkeypatch.delenv(variable, raising=False)

    assert cli.run_stage("ci") == 1

    out = capsys.readouterr().out
    assert "PASS  fine" in out
    assert "FAIL  broken" in out
    assert "KeyError" in out


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


def test_test_gates_run_without_the_git_hook_environment(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return 0, ""

    monkeypatch.setattr(core, "run", fake_run)
    ok, _ = testing.check_tests(["pytest"])
    assert ok
    assert seen["scrub_git"] is True


def test_scrub_git_drops_git_variables_from_the_child_environment(monkeypatch):
    monkeypatch.setenv("GIT_INDEX_FILE", "/nonexistent/index")
    monkeypatch.setenv("KEEP_ME", "1")
    code, out = core.run(
        [sys.executable, "-c", "import os;print(sorted(k for k in os.environ if k in ('GIT_INDEX_FILE','KEEP_ME')))"],
        scrub_git=True,
    )
    assert code == 0
    assert out.strip() == "['KEEP_ME']"
