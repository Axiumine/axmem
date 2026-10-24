"""What a commit can skip: a staged file that a gate reads never leaves that gate out, and a gate configuration widens the run.

Stand-ins for pytest and semgrep record the command, so nothing here starts a suite.
"""

import pytest

from scripts.quality import cli, core, static, testing


def ran(gate, monkeypatch, files):
    """The pytest command a test gate runs for staged ``files``, or ``None`` when it skips."""
    seen = []
    monkeypatch.setattr(testing, "check_tests", lambda argv, **k: seen.append(argv) or (True, ""))
    try:
        gate(core.Ctx(stage="pre-commit", files=files))
    except core.Skip:
        return None
    return seen[0]


@pytest.mark.parametrize("staged", [
    "app/a.py", "scripts/quality/wheel.py", "tests/architecture/test_workflows.py", ".github/workflows/ci.yml",
    ".githooks/pre-push", "pyproject.toml", "uv.lock", "main.py", "architecture.md", ".python-version",
    "tests/integration/test_mutation_sandbox_probe.py",  # test_mutation_sandbox scans the integration tests
])
def test_a_staged_file_that_an_architecture_rule_reads_runs_the_architecture_gate(monkeypatch, staged):
    command = ran(testing.gate_architecture, monkeypatch, [staged])

    assert command[-4:] == ["tests/architecture", "-q", "-p", "no:cacheprovider"]


def test_the_architecture_gate_runs_on_the_whole_project_when_nothing_is_scoped(monkeypatch):
    seen = []
    monkeypatch.setattr(testing, "check_tests", lambda argv, **k: seen.append(argv) or (True, ""))

    assert testing.gate_architecture(core.Ctx(stage="pre-push", files=None)) == (True, "")
    assert "tests/architecture" in seen[0]


def test_a_commit_that_stages_no_file_of_the_architecture_rules_skips_the_gate(monkeypatch):
    assert ran(testing.gate_architecture, monkeypatch, ["README.md", "docs/guide.md", "tests/e2e/a.py"]) is None


def test_a_staged_snapshot_file_runs_the_snapshot_and_integration_gates(monkeypatch):
    staged = ["tests/integration/__snapshots__/test_snapshots.ambr"]

    assert "snapshot" in ran(testing.gate_snapshots, monkeypatch, staged)
    assert "tests/integration" in ran(testing.gate_tests_fast, monkeypatch, staged)
    assert ran(testing.gate_snapshots, monkeypatch, ["README.md"]) is None
    assert ran(testing.gate_tests_fast, monkeypatch, ["LICENSE"]) is None


@pytest.mark.parametrize("staged", ["docs/quality-gates.md", "README.md", "CHANGELOG.md", ".env.example", ".github/workflows/ci.yml"])
def test_a_staged_file_that_the_integration_tests_read_runs_the_fast_tests(monkeypatch, staged):
    assert "tests/integration" in ran(testing.gate_tests_fast, monkeypatch, [staged])
    assert ran(testing.gate_snapshots, monkeypatch, [staged]) is None


def test_a_deletion_runs_the_whole_project_gates_but_lints_nothing(monkeypatch):
    seen = []
    monkeypatch.setattr(static, "check", lambda argv, **k: seen.append(argv) or (True, ""))
    gone = core.Ctx(stage="pre-commit", files=[], removed=["app/old.py"])
    kept = core.Ctx(stage="pre-commit", files=[], removed=["LICENSE"])

    assert static.gate_mypy(gone) == (True, "")
    assert static.gate_vulture(gone) == (True, "")
    assert static.gate_imports(gone) == (True, "")
    for gate in (static.gate_mypy, static.gate_vulture, static.gate_imports):
        with pytest.raises(core.Skip):
            gate(kept)
    with pytest.raises(core.Skip):
        static.gate_ruff(gone)  # nothing left to lint
    assert len(seen) == 3
    monkeypatch.setattr(testing, "check_tests", lambda argv, **k: (True, ""))
    for gate in (testing.gate_snapshots, testing.gate_tests_fast):
        assert gate(gone) == (True, "")
        with pytest.raises(core.Skip):
            gate(kept)


def test_a_commit_that_only_deletes_files_is_not_taken_for_an_empty_one(monkeypatch, capsys):
    ran_with = []
    monkeypatch.delenv("QUALITY_FULL", raising=False)
    monkeypatch.setattr(cli, "staged_files", list)
    monkeypatch.setattr(cli, "removed_files", lambda: ["app/old.py"])
    monkeypatch.setattr(cli, "run_stage_groups", lambda stage, ctx: ran_with.append(ctx) or (True, 0))

    assert cli.run_stage("pre-commit") == 0
    assert ran_with[0].removed == ["app/old.py"]
    monkeypatch.setattr(cli, "removed_files", list)
    assert cli.run_stage("pre-commit") == 0
    assert "nothing staged" in capsys.readouterr().out
    assert len(ran_with) == 1


def test_the_fast_tests_leave_the_architecture_tests_to_their_own_gate(monkeypatch):
    command = ran(testing.gate_tests_fast, monkeypatch, ["app/a.py"])

    assert "tests/architecture" not in command


@pytest.mark.parametrize("config", [
    "pyproject.toml", "uv.lock", "ruff.toml", "radon.cfg", "pytest.ini", "vulture_whitelist.py", "trivy.yaml",
    ".trivyignore", ".semgrepignore", ".semgrep/axmem.yml", ".shellcheckrc",
])
def test_a_staged_gate_configuration_makes_pre_commit_check_the_whole_project(monkeypatch, config):
    monkeypatch.delenv("QUALITY_FULL", raising=False)
    monkeypatch.setattr(cli, "staged_files", lambda: ["app/a.py", config])
    monkeypatch.setattr(cli, "removed_files", list)

    assert cli.stage_files("pre-commit") == (None, [])


def test_staged_code_scopes_pre_commit_and_the_other_stages_check_everything(monkeypatch):
    monkeypatch.delenv("QUALITY_FULL", raising=False)
    monkeypatch.setattr(cli, "staged_files", lambda: ["app/a.py", "README.md"])
    monkeypatch.setattr(cli, "removed_files", lambda: ["app/b.py"])

    assert cli.stage_files("pre-commit") == (["app/a.py", "README.md"], ["app/b.py"])
    assert cli.stage_files("pre-push") == (None, [])
    monkeypatch.setenv("QUALITY_FULL", "1")
    assert cli.stage_files("pre-commit") == (None, [])


def semgrep_args(monkeypatch, ctx):
    seen = []
    monkeypatch.setattr(static, "semgrep_cmd", lambda: ["semgrep"])
    monkeypatch.setattr(static, "check", lambda argv, **k: seen.append(argv) or (True, ""))
    static.gate_semgrep(ctx)
    return [seen[0][i + 1] for i, word in enumerate(seen[0]) if word == "--config"]


def test_semgrep_runs_the_registry_packs_on_the_whole_project_by_default(monkeypatch):
    monkeypatch.delenv("SEMGREP_PACKS", raising=False)

    configs = semgrep_args(monkeypatch, core.Ctx(stage="pre-push", files=None))

    assert configs == [".semgrep", "p/python", "p/security-audit"]


def test_semgrep_keeps_to_the_local_rules_on_staged_files_so_a_commit_needs_no_network(monkeypatch):
    monkeypatch.delenv("SEMGREP_PACKS", raising=False)

    configs = semgrep_args(monkeypatch, core.Ctx(stage="pre-commit", files=["app/a.py"]))

    assert configs == [".semgrep"]


@pytest.mark.parametrize(("value", "configs"), [
    ("", [".semgrep"]),
    ("p/ci", [".semgrep", "p/ci"]),
])
def test_the_semgrep_packs_variable_replaces_the_default_and_empty_leaves_them_out(monkeypatch, value, configs):
    monkeypatch.setenv("SEMGREP_PACKS", value)

    assert semgrep_args(monkeypatch, core.Ctx(stage="pre-push", files=None)) == configs
