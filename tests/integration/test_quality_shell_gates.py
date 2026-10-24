"""The shellcheck and actionlint gates lint every shell script and every workflow, with the pinned tools.

The pass and fail cases run the real tools that the ``dev`` group installs, on a scratch repository; the stand-in
``actionlint`` records how it was called. A missing tool fails the gate, as in ``test_pre_push_pipeline.py``.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.quality import cli, core, static

CLEAN_SCRIPT = '#!/usr/bin/env bash\nset -euo pipefail\necho "$1"\n'
BAD_SCRIPT = "#!/bin/sh\necho $1\n"  # SC2086: unquoted
CLEAN_WORKFLOW = """name: x
on: push
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - run: echo "$HOME"
"""
BAD_RUN_BLOCK = CLEAN_WORKFLOW.replace('echo "$HOME"', "echo $HOME")  # only shellcheck sees this
BAD_WORKFLOW = CLEAN_WORKFLOW.replace("runs-on: ubuntu-latest", "runs-on: ubuntu-latest\n    colour: red")
REAL_BIN = Path(sys.executable).parent


@pytest.fixture(autouse=True)
def repo(tmp_path, monkeypatch):
    """A scratch repository that the gates see as the project."""
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(name)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S603, S607
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(static, "ROOT", tmp_path)
    return tmp_path


def write(repo, name, text, executable=False):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def gate(name, files=None):
    return cli.run_gate(name, core.Ctx(stage="pre-push" if files is None else "pre-commit", files=files))


def an_environment_without(monkeypatch, tmp_path, tool):
    """An environment whose bin directory holds the real tools but ``tool``."""
    bin_dir = tmp_path / "venv-bin"
    bin_dir.mkdir()
    for name in ("shellcheck", "actionlint"):
        if name != tool:
            (bin_dir / name).symlink_to(REAL_BIN / name)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))


@pytest.mark.parametrize(("first_line", "interpreter"), [
    ("#!/bin/sh", "sh"),
    ("#!/usr/bin/env bash", "bash"),
    ("#!/usr/bin/env -S bash -e", "bash"),
    ("#! /bin/bash -eu", "bash"),
    ("#!/usr/bin/env python3", "python3"),
    ("#!/usr/bin/env", ""),
    ("echo hi", ""),
    ("", ""),
])
def test_the_interpreter_is_read_from_the_shebang_line(first_line, interpreter):
    assert static.interpreter_of(first_line) == interpreter


def test_shellcheck_passes_clean_scripts(repo):
    write(repo, "scripts/run.sh", CLEAN_SCRIPT)
    write(repo, ".githooks/pre-commit", "#!/bin/sh\nexit 0\n", executable=True)

    assert gate("shellcheck").status == "PASS"


def test_shellcheck_finds_a_defect_in_a_script_without_a_suffix(repo):
    write(repo, ".githooks/pre-commit", BAD_SCRIPT, executable=True)
    write(repo, "tools/helper.py", "#!/usr/bin/env python3\nx = '$1'\n")

    result = gate("shellcheck")

    assert result.status == "FAIL"
    assert ".githooks/pre-commit" in result.output
    assert "SC2086" in result.output
    assert "helper.py" not in result.output


def test_shellcheck_finds_a_defect_in_a_file_with_the_suffix_and_no_shebang(repo):
    write(repo, "scripts/lib/project.sh", "VALUE=$1\necho $VALUE\n")

    result = gate("shellcheck")

    assert result.status == "FAIL"
    assert "scripts/lib/project.sh" in result.output


def test_shellcheck_leaves_out_what_git_ignores(repo):
    write(repo, ".gitignore", "build.sh\n")
    write(repo, "build.sh", BAD_SCRIPT)
    write(repo, "ok.sh", CLEAN_SCRIPT)

    assert gate("shellcheck").status == "PASS"


def test_shellcheck_follows_a_sourced_file(repo):
    # lib.env is no input of the gate (no suffix, no shebang), so only -x finds what it assigns: SC1091 and SC2154 without
    write(repo, "main.sh", '#!/usr/bin/env bash\n# shellcheck source=lib.env\nsource "$(dirname "$0")/lib.env"\necho "$value"\n')
    write(repo, "lib.env", "# shellcheck shell=bash\nexport value=1\n")

    assert gate("shellcheck").status == "PASS"


def test_an_option_in_the_environment_cannot_turn_a_defect_into_a_pass(repo, monkeypatch):
    write(repo, "bad.sh", BAD_SCRIPT)
    write(repo, ".github/workflows/ci.yml", BAD_RUN_BLOCK)
    monkeypatch.setenv("SHELLCHECK_OPTS", "-e SC2086")

    assert gate("shellcheck").status == "FAIL"
    assert gate("actionlint").status == "FAIL"


def test_a_removed_shell_script_or_workflow_lints_the_ones_left(repo):
    write(repo, "bad.sh", BAD_SCRIPT)
    write(repo, ".github/workflows/ci.yml", BAD_WORKFLOW)

    for name, removed in (("shellcheck", "old.sh"), ("actionlint", ".github/workflows/old.yml")):
        ctx = core.Ctx(stage="pre-commit", files=[], removed=[removed])
        assert cli.run_gate(name, ctx).status == "FAIL"


def test_shellcheck_skips_a_project_without_shell_scripts(repo):
    write(repo, "app.py", "print(1)\n")

    result = gate("shellcheck")

    assert (result.status, result.note) == ("SKIP", "no shell script in the project")


def test_a_commit_that_stages_no_shell_script_skips_shellcheck(repo):
    write(repo, "bad.sh", BAD_SCRIPT)
    write(repo, "README.md", "text\n")

    result = gate("shellcheck", files=["README.md"])

    assert (result.status, result.note) == ("SKIP", "no staged shell script")


@pytest.mark.parametrize("staged", ["good.sh", ".githooks/commit-msg", ".shellcheckrc"])
def test_a_staged_shell_file_or_config_lints_every_script_of_the_project(repo, staged):
    write(repo, "good.sh", CLEAN_SCRIPT)
    write(repo, ".githooks/commit-msg", "#!/bin/sh\nexit 0\n", executable=True)
    write(repo, ".shellcheckrc", "")
    write(repo, "elsewhere/bad.sh", BAD_SCRIPT)

    result = gate("shellcheck", files=[staged])

    assert result.status == "FAIL"
    assert "elsewhere/bad.sh" in result.output


def test_shellcheck_fails_without_the_pinned_binary_instead_of_skipping(repo, tmp_path, monkeypatch):
    write(repo, "bad.sh", BAD_SCRIPT)
    an_environment_without(monkeypatch, tmp_path, "shellcheck")
    monkeypatch.setenv("PATH", os.defpath)

    result = gate("shellcheck")

    assert result.status == "FAIL"
    assert "shellcheck is not installed in the project environment" in result.output


def test_actionlint_passes_a_clean_workflow(repo):
    write(repo, ".github/workflows/ci.yml", CLEAN_WORKFLOW)

    assert gate("actionlint").status == "PASS"


def test_actionlint_finds_a_defect_in_a_workflow(repo):
    write(repo, ".github/workflows/ci.yml", CLEAN_WORKFLOW)
    write(repo, ".github/workflows/release.yaml", BAD_WORKFLOW)

    result = gate("actionlint")

    assert result.status == "FAIL"
    assert ".github/workflows/release.yaml" in result.output
    assert "colour" in result.output


def test_actionlint_lints_the_run_blocks_with_the_pinned_shellcheck_even_when_the_path_has_none(repo, monkeypatch):
    write(repo, ".github/workflows/ci.yml", BAD_RUN_BLOCK)
    monkeypatch.setenv("PATH", os.defpath)  # no shellcheck anywhere on it
    monkeypatch.setattr(static.shutil, "which", lambda _name: None)

    result = gate("actionlint")

    assert result.status == "FAIL"
    assert "SC2086" in result.output


def test_actionlint_is_given_the_pinned_shellcheck_first_on_the_path(repo, tmp_path, monkeypatch):
    bin_dir = tmp_path / "venv-bin"
    bin_dir.mkdir()
    (bin_dir / "shellcheck").write_text("#!/bin/sh\n", encoding="utf-8")
    (bin_dir / "actionlint").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$PATH" > "$0.path"\nprintf "%s\\n" "$@" > "$0.argv"\n', encoding="utf-8",
    )
    (bin_dir / "actionlint").chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    path = f"/somewhere/else{os.pathsep}{os.environ['PATH']}"
    monkeypatch.setenv("PATH", path)
    write(repo, ".github/workflows/ci.yml", CLEAN_WORKFLOW)
    write(repo, ".github/dependabot.yml", "version: 2\n")

    result = gate("actionlint")

    assert result.status == "PASS", result.output
    assert (bin_dir / "actionlint.path").read_text(encoding="utf-8").strip() == f"{bin_dir}{os.pathsep}{path}"
    argv = (bin_dir / "actionlint.argv").read_text(encoding="utf-8").split()
    assert argv == ["-shellcheck", str(bin_dir / "shellcheck"), ".github/workflows/ci.yml"]


def test_actionlint_skips_a_project_without_workflows(repo):
    write(repo, ".github/dependabot.yml", "version: 2\n")

    result = gate("actionlint")

    assert (result.status, result.note) == ("SKIP", "no workflow in the project")


def test_a_commit_that_stages_no_workflow_skips_actionlint(repo):
    write(repo, ".github/workflows/ci.yml", BAD_WORKFLOW)
    write(repo, ".github/dependabot.yml", "version: 2\n")

    result = gate("actionlint", files=[".github/dependabot.yml", "app/a.py"])

    assert (result.status, result.note) == ("SKIP", "no staged workflow")


@pytest.mark.parametrize("staged", [".github/workflows/ci.yml", ".github/actionlint.yaml", ".shellcheckrc"])
def test_a_staged_workflow_or_config_lints_every_workflow_of_the_project(repo, staged):
    write(repo, ".github/workflows/ci.yml", CLEAN_WORKFLOW)
    write(repo, ".github/workflows/other.yml", BAD_WORKFLOW)

    result = gate("actionlint", files=[staged])

    assert result.status == "FAIL"
    assert ".github/workflows/other.yml" in result.output


@pytest.mark.parametrize("tool", ["actionlint", "shellcheck"])
def test_actionlint_fails_without_either_pinned_binary_instead_of_skipping(repo, tmp_path, monkeypatch, tool):
    write(repo, ".github/workflows/ci.yml", CLEAN_WORKFLOW)
    an_environment_without(monkeypatch, tmp_path, tool)

    result = gate("actionlint")

    assert result.status == "FAIL"
    assert f"{tool} is not installed in the project environment" in result.output


def test_the_shell_scripts_and_the_workflows_of_this_checkout_pass(monkeypatch):
    """The real project: every hook, release script and workflow, with the pinned tools."""
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(core, "ROOT", root)
    monkeypatch.setattr(static, "ROOT", root)

    assert gate("shellcheck").output == ""
    assert gate("actionlint").output == ""
    assert {".githooks/pre-push", "scripts/check-package.sh"} <= {n for n in core.repo_files() if static.is_shell_script(n)}


def test_the_pinned_tools_are_not_taken_from_the_bin_directory_of_a_system_interpreter(monkeypatch):
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)

    with pytest.raises(core.MissingToolError, match="not in a virtual environment"):
        core.venv_binary("shellcheck")
