"""The pre-push pipeline is the one place that runs the GPU, Docker and Qdrant gates, and it never passes by skipping.

Stage order, a failing gate refusing the push, a missing tool or daemon failing its gate instead of skipping it, and
the suites that the test gates run: all with stand-in gates or stand-in tools, so nothing here starts a server.
``test_pre_push_hook.py`` proves the Git side, and ``test_gpu_preflight.py`` and ``test_qdrant_preflight.py`` prove that
each suite ends its run without a GPU or without Docker.
"""

import ast
from pathlib import Path

import pytest

from scripts.quality import cli, core, registry, security, static, testing

ROOT = Path(__file__).resolve().parents[2]
STATIC_GATES = ["ruff", "imports", "bandit", "semgrep", "vulture", "radon-cc", "radon-mi"]
NEEDS_GPU_OR_DOCKER = {
    "snapshots", "tests-fast", "tests", "e2e-qdrant", "coverage", "memray", "qodana", "mutation", "gitnexus",
}


def _pre_push_order():
    return [name for group in registry.STAGES["pre-push"] for name in group]


def test_the_pre_push_pipeline_runs_the_cheap_gates_first_and_each_gate_after_the_data_it_reads():
    order = _pre_push_order()
    groups = registry.STAGES["pre-push"]

    for cheap in [*STATIC_GATES, "trivy", "mypy", "pylint"]:
        assert order.index(cheap) < order.index("tests"), f"{cheap} should fail the push before the long suites start"
    assert groups[0] == [*STATIC_GATES, "trivy"]
    assert order.index("tests") < order.index("e2e-qdrant") < order.index("coverage")  # coverage reads both data files
    assert order.index("coverage") < order.index("memray") < order.index("qodana") < order.index("mutation")
    assert order[-1] == "gitnexus"
    assert len(order) == len(set(order))


def test_the_ci_stage_holds_the_gates_that_need_neither_a_gpu_nor_docker():
    ci = [name for group in registry.STAGES["ci"] for name in group]

    assert set(ci) == {*STATIC_GATES, "trivy", "mypy", "pylint"}
    assert not set(ci) & NEEDS_GPU_OR_DOCKER
    assert registry.STAGES["ci"] == registry.STAGES["pre-push"][:2]


@pytest.mark.parametrize("failing", _pre_push_order())
def test_a_failing_gate_refuses_the_push_and_stops_the_gates_after_it(monkeypatch, failing):
    ran = []

    def gate(name):
        def run(_ctx):
            ran.append(name)
            return name != failing, f"{name} output"
        return run

    monkeypatch.setattr(registry, "GATES", {name: (gate(name), "") for name in _pre_push_order()})
    monkeypatch.delenv("SKIP", raising=False)
    monkeypatch.delenv("ONLY", raising=False)
    monkeypatch.setenv("MUTATION", "1")
    expected = []
    for group in registry.STAGES["pre-push"]:
        expected += group
        if failing in group:
            break

    assert cli.run_stage("pre-push") == 1
    assert sorted(ran) == sorted(expected)


def test_a_pipeline_whose_gates_all_pass_lets_the_push_through(monkeypatch):
    monkeypatch.setattr(registry, "GATES", {name: (lambda _ctx: (True, ""), "") for name in _pre_push_order()})
    monkeypatch.delenv("SKIP", raising=False)
    monkeypatch.delenv("ONLY", raising=False)
    monkeypatch.setenv("MUTATION", "1")

    assert cli.run_stage("pre-push") == 0


def test_a_missing_tool_fails_the_gate_instead_of_skipping_it(monkeypatch, capsys):
    def gate(_ctx):
        raise core.MissingToolError("docker is missing")

    monkeypatch.setattr(registry, "GATES", {"x": (gate, "")})
    monkeypatch.setattr(registry, "STAGES", {"pre-push": [["x"]]})
    monkeypatch.delenv("SKIP", raising=False)
    monkeypatch.delenv("ONLY", raising=False)

    result = cli.run_gate("x", core.Ctx(stage="pre-push", files=None))
    assert (result.status, result.output) == ("FAIL", "docker is missing")
    assert cli.run_stage("pre-push") == 1
    assert "docker is missing" in capsys.readouterr().out


def test_the_cluster_gate_fails_without_docker_instead_of_skipping(monkeypatch):
    monkeypatch.setattr(testing, "docker_ready", lambda: False)
    monkeypatch.setattr(testing, "check_tests", lambda *a, **k: pytest.fail("the cluster suite must not start"))

    result = cli.run_gate("e2e-qdrant", core.Ctx(stage="pre-push", files=None))

    assert result.status == "FAIL"
    assert "no Docker daemon answers" in result.output
    assert "SKIP=e2e-qdrant" in result.output


def test_trivy_fails_without_a_binary_and_without_docker_instead_of_skipping(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TRIVY_BIN", raising=False)
    monkeypatch.setattr(security.shutil, "which", lambda _name: None)
    monkeypatch.setattr(security, "docker_ready", lambda: False)

    result = cli.run_gate("trivy", core.Ctx(stage="pre-push", files=None))

    assert result.status == "FAIL"
    assert "trivy is not installed" in result.output


def test_qodana_fails_without_its_cli_instead_of_skipping(monkeypatch):
    monkeypatch.setattr(security.shutil, "which", lambda _name: None)

    result = cli.run_gate("qodana", core.Ctx(stage="pre-push", files=None))

    assert result.status == "FAIL"
    assert "the qodana CLI is not installed" in result.output


def test_semgrep_fails_when_nothing_can_run_it_instead_of_skipping(monkeypatch):
    monkeypatch.setattr(static.shutil, "which", lambda _name: None)
    monkeypatch.setattr(static, "docker_ready", lambda: False)

    result = cli.run_gate("semgrep", core.Ctx(stage="pre-push", files=None))

    assert result.status == "FAIL"
    assert "semgrep is not installed" in result.output


def test_a_gate_with_nothing_to_look_at_still_skips(monkeypatch):
    monkeypatch.setattr(security.shutil, "which", lambda _name: None)

    result = cli.run_gate("gitnexus", core.Ctx(stage="pre-push", files=None))

    assert result.status == "SKIP"  # GitNexus is local tooling with a local index; a push does not depend on it


def _suites_of(gate, monkeypatch):
    """The paths that a test gate hands to pytest."""
    seen = []
    monkeypatch.setattr(testing, "docker_ready", lambda: True)
    monkeypatch.setattr(testing, "run", lambda *a, **k: (0, ""))
    for name in ("check", "check_tests"):
        monkeypatch.setattr(testing, name, lambda argv, **k: seen.append(argv) or (True, ""))
    gate(core.Ctx(stage="pre-push", files=None))
    return [word for word in seen[0] if word.startswith("tests/")]


def _conftest_calls(suite):
    """The names called by ``pytest_collection_modifyitems`` in the conftest of a suite."""
    tree = ast.parse((ROOT / suite / "conftest.py").read_text(encoding="utf-8"))
    (hook,) = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "pytest_collection_modifyitems"]
    return {node.func.id for node in ast.walk(hook) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def test_the_gates_that_run_tests_run_only_suites_that_end_the_run_without_a_gpu_and_without_docker(monkeypatch):
    run_by = {
        "tests": _suites_of(testing.gate_tests, monkeypatch),
        "e2e-qdrant": _suites_of(testing.gate_e2e_qdrant, monkeypatch),
        "memray": _suites_of(testing.gate_memray, monkeypatch),
    }

    assert run_by == {
        "tests": ["tests/architecture", "tests/integration", "tests/e2e"],
        "e2e-qdrant": ["tests/cluster"],
        "memray": ["tests/e2e"],
    }
    for suite in ("tests/integration", "tests/e2e", "tests/cluster"):
        assert {"require_gpu", "require_docker"} <= _conftest_calls(suite), suite


def test_the_coverage_gate_demands_every_line_and_branch(monkeypatch):
    seen = []
    monkeypatch.setattr(testing, "check", lambda argv, **k: seen.append(argv) or (True, ""))

    assert testing.gate_coverage(core.Ctx(stage="pre-push", files=None)) == (True, "")
    assert seen[0][seen[0].index("report"):] == ["report", "--fail-under=100"]
