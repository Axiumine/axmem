# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The scripts of the TestPyPI rehearsal hold no release, and the shell, the workflow and the Python hold the same facts.

``scripts/release-testpypi.sh`` and ``scripts/lib/testpypi-flow.sh`` are read as text: they hold no commit, tag, push, build or stamp, no
step of the PyPI upload, no host of PyPI, the one dispatch is the rehearsal's, the files come from the file host of TestPyPI over https
only, and the lock is its own. The host, the file host, the workflow and the environment are written three times (the shell, the workflow
and ``scripts/release/facts.py``) and are held together here, and the block that derives the repository from the remote is the release
script's, byte for byte. ``test_release_testpypi_script.py`` runs the script.
"""

import re
from pathlib import Path

import pytest
import yaml

from scripts.release import facts, testpypi
from tests.release_support.script import RELEASE_SCRIPT, TESTPYPI_FLOW, TESTPYPI_SCRIPT
from tests.release_support.world import GITHUB_REPO, REPO_ROOT

SCRIPT = TESTPYPI_SCRIPT
LIB = TESTPYPI_FLOW
PYPI_SCRIPT = RELEASE_SCRIPT
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-testpypi.yml"


def _code(path: Path) -> str:
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))


def _constants(path: Path) -> dict[str, str]:
    found = re.findall(r'^([A-Z_]+)="([^"$]*)"', path.read_text(encoding="utf-8"), re.MULTILINE)
    return dict(found)


class TestTheThreeCopiesOfTheFacts:
    def test_the_shell_the_workflow_and_the_python_name_the_same_host_workflow_and_environment(self) -> None:
        shell = _constants(LIB)
        row = facts.INDEXES["testpypi"]
        document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

        assert shell["TESTPYPI_HOST"] == "test.pypi.org"
        assert shell["TESTPYPI_FILES_HOST"] == row.file_host
        assert shell["TESTPYPI_WORKFLOW"] == row.workflow == testpypi.TESTPYPI_WORKFLOW == WORKFLOW.name
        assert shell["TESTPYPI_ENVIRONMENT"] == row.environment == testpypi.TESTPYPI_ENVIRONMENT
        assert document["jobs"]["publish"]["environment"] == shell["TESTPYPI_ENVIRONMENT"]
        assert f"https://{shell['TESTPYPI_HOST']}/pypi/axmem/json" in WORKFLOW.read_text(encoding="utf-8")
        assert f"https://{shell['TESTPYPI_HOST']}/legacy/" in WORKFLOW.read_text(encoding="utf-8")

    def test_the_repository_is_derived_from_the_remote_as_the_release_script_derives_it(self) -> None:
        def block(path: Path) -> str:
            text = path.read_text(encoding="utf-8")
            start = text.index('REMOTE_URL="$(git -C')
            end = text.index("\n", text.index("is not a github.com repository"))
            return text[start : end + 1]

        assert block(SCRIPT) == block(PYPI_SCRIPT)
        assert 'GH_REPO="$(printf' in block(SCRIPT)

    def test_the_rehearsal_never_reads_the_remote_branch_but_through_the_guard(self) -> None:
        code = _code(SCRIPT) + _code(LIB)

        assert 'git -C "$REPO_ROOT" fetch' not in code
        assert "rf_net git" not in code.replace("rf_require_no_branch_of_tag", "")


class TestTheScriptsHoldNoRelease:
    @pytest.mark.parametrize(
        "word",
        [
            "git push",
            "git tag",
            "git commit",
            "git fetch",
            "uv ",
            "stamp_changelog",
            "commit_release",
            "rf_push_release",
            "rf_tag",
            "rf_wait_ci",
            "publish_release",
            "rf_dispatch_publish",
            "rf_wait_pypi",
            "rf_verify_pypi",
            "rf_load_pypi",
            "rf_lock",
            "guard publish-dispatch",
            "guard publish-wait",
            "PYPI_JSON_URL",
            "PYPI_FILES_HOST",
            "PYPI_HOST",
            "pypi.org",
            "files.pythonhosted.org",
            "twine",
            "curl -X",
            "--upload-file",
            "UV_PUBLISH",
        ],
    )
    def test_the_rehearsal_does_not_contain(self, word: str) -> None:
        code = (_code(SCRIPT) + _code(LIB)).replace("test.pypi.org", "").replace("test-files.pythonhosted.org", "")

        assert not re.search(rf"(?<![A-Za-z_]){re.escape(word)}", code)

    def test_the_one_workflow_it_dispatches_is_the_rehearsal_s_and_it_names_publish_yml_to_read_its_runs_only(self) -> None:
        code = _code(SCRIPT) + _code(LIB)
        commands = re.findall(r"rf_py guard ([a-z-]+)", code)

        assert re.findall(r"gh workflow run", code) == []
        assert commands.count("testpypi-run") == 1
        assert sorted(set(commands)) == [
            "check",
            "publish-state",
            "testpypi-ready",
            "testpypi-run",
            "testpypi-state",
            "testpypi-urls",
        ]

    def test_the_download_of_the_files_is_https_only_from_the_files_host_of_testpypi(self) -> None:
        code = _code(LIB)

        assert '[[ "$url" == "https://${TESTPYPI_FILES_HOST}/"* ]]' in code
        assert "curl -sSLf --globoff --proto =https --proto-redir =https" in code
        assert "--insecure" not in code
        assert " -k " not in code

    def test_the_script_s_own_lock_is_not_the_releases(self) -> None:
        assert "release-testpypi.lock" in _code(LIB)
        assert "release-pypi.lock" not in _code(LIB) + _code(SCRIPT)

    def test_the_last_line_is_the_one_that_tells_the_operator_to_approve_the_environment_pypi(self) -> None:
        assert _code(SCRIPT).rstrip().splitlines()[-1] == (
            'echo "TestPyPI rehearsal complete: now '
            'approve environment '
            '${PUBLISH_ENVIRONMENT}"'
        )
        assert _constants(REPO_ROOT / "scripts" / "lib" / "project.sh")["PUBLISH_ENVIRONMENT"] == "pypi"
        assert GITHUB_REPO == "Axiumine/axmem"
