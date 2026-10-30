# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The release commit comes first, the dispatch of the publish workflow comes last, and a refused commit stops the release.

A release that dispatched first and committed afterwards could leave a version on PyPI that no commit and no tag names:
PyPI refuses a second upload of a file name for good, so the upload is the one step that cannot be undone. The script
therefore refuses what it can before anything changes, makes the release commit, pushes it, waits for CI, tags it and waits
for the signing run, and only then asks the guard and dispatches ``publish.yml``, which makes the upload.

Two kinds of test pin that. The structure tests read the script and compare where its commands sit. The hermetic tests lift
``commit_release``, ``rf_stop`` and ``rf_stop_dispatched`` out of the script (``tests/release_support/script.py``) and run
them in a temporary repository whose hook accepts or refuses the commit, so the failure handling is proved by running it,
not by reading it. ``test_release_pipeline.py`` runs the whole script against fakes.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.release_support.script import (
    RELEASE_FLOW,
    RELEASE_SCRIPT,
    REPO_ROOT,
    function_source,
    run_functions,
)
from tests.release_support.world import PACKAGE, REMOTE, ambient_free

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

#: the two paths an Axmem release commits, as the script names them (the changelog stamp and the digest manifest): the
#: version is written nowhere, and a release changes no dependency
PATHS = ("CHANGELOG.md", "release-digests.json")
MESSAGE = f"🤖 chore(release): release {PACKAGE} 9.9.9 to PyPI"

#: what the script checks before it changes anything, then the commands of the publish path in the order it runs them;
#: each is a command line, not a comment
REFUSALS = (
    r"^rf_require_tools uv git gh python3 curl flock timeout$",
    r"^rf_check_waits$",
    r"^rf_require_hooks_armed$",
    r"^rf_lock$",
    r"^rf_require_dispatchable$",
    r"^rf_net git .* fetch ",
    r"^  rf_require_release_branch$",
    r"^  rf_require_synced_main$",
    r"^  rf_require_clean_tree$",
    r"^  rf_require_tag_free$",
    r"^  rf_py ci_green --repo \"\$GH_REPO\" --sha \"\$LOCAL_TIP\"",
    r"^  \( cd \"\$REPO_ROOT\" && uv run --frozen python -m scripts\.quality stage ci \)",
)
STAMP = r"^  stamp_changelog \"\$CHANGELOG\" \"\$TAG\"$"
COMMIT = r"^  commit_release \"\$CHANGELOG\" \"\$MANIFEST\"$"
REQUIRE_TREE = r"^rf_require_release_tree$"
PUBLISH = r"^publish_release$"
ORDER = (
    *REFUSALS,
    STAMP,
    r"^  export SOURCE_DATE_EPOCH$",
    r"^  rf_pin_version$",
    r"^  \( cd .* uv build",
    r"^  rf_require_built_version$",
    r"^  rf_py sdist ",
    r"^  BUILD=0 bash .*check-package\.sh",
    r"^  rf_py digests write",
    COMMIT,
    REQUIRE_TREE,
    r"^rf_push_release$",
    r"^rf_wait_ci$",
    r"^rf_tag$",
    r"^rf_wait_signing$",
    r"^rf_fetch_signed$",
    r"^rf_recheck_pypi$",
    PUBLISH,
    r"^rf_wait_pypi$",
    r"^rf_verify_pypi$",
)
#: the names of the retired route to a private index that no release script or module may carry, in any case: the helper
#: library of the environment file, the credential variables, the quality runner's ``qg_`` helpers and the file itself
RETIRED = re.compile(r"env-helpers|DEVPI|\bqg_|\.en" + r"v\b", re.IGNORECASE)


def _command(script: str, pattern: str) -> int:
    """Offset of the command line that matches, not of a comment that names it."""
    match = re.search(pattern, script, re.MULTILINE)
    assert match is not None, f"no command line matching {pattern!r}"
    return match.start()


def _code(text: str) -> list[str]:
    """The lines of ``text`` that are not comments."""
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def _environment(repo: Path) -> dict[str, str]:
    """The process environment with git told who commits and where its configuration is (nowhere else)."""
    empty = repo.parent / "gitconfig"
    empty.write_text("", encoding="utf-8")
    return {
        **ambient_free(dict(os.environ)),
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Release Test",
        "GIT_AUTHOR_EMAIL": "release@example.invalid",
        "GIT_COMMITTER_NAME": "Release Test",
        "GIT_COMMITTER_EMAIL": "release@example.invalid",
        "REPO_ROOT": str(repo),
        "PKG": PACKAGE,
        "VERSION": "9.9.9",
        "TAG": "v9.9.9",
        "RELEASE_REMOTE": REMOTE,
        "RELEASE_SHA": "0123456789abcdef0123456789abcdef01234567",
        "PUBLISH_WORKFLOW": "publish.yml",
        "PUBLISH_ENVIRONMENT": "pypi",
    }


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argv, a temporary repository
        ["git", "-C", str(repo), *args],  # noqa: S607 - a program of the test setup, run without a shell
        capture_output=True,
        text=True,
        check=True,
        env=_environment(repo),
    )
    return done.stdout.strip()


@pytest.fixture(name="script")
def _script() -> str:
    return RELEASE_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(name="repo")
def _repo(tmp_path: Path) -> Path:
    """A repository at HEAD with the two release paths and one unrelated tracked file, then the release's edits."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for name in (*PATHS, "notes.txt"):
        (root / name).write_text(f"{name} at HEAD\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    for name in PATHS:
        (root / name).write_text(f"{name} released\n", encoding="utf-8")
    (root / "notes.txt").write_text(
        "an unrelated edit the release must not touch\n", encoding="utf-8",
    )
    return root


def _hook(root: Path, accepts: bool) -> Path:
    """A pre-commit hook that accepts or refuses, and leaves a marker so a test can see it ran."""
    hooks = root.parent / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text(
        f"#!/bin/sh\ntouch '{root.parent}/hook-ran'\nexit {0 if accepts else 1}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    _git(root, "config", "core.hooksPath", str(hooks))
    return hooks


def _commit_release(root: Path) -> subprocess.CompletedProcess[str]:
    paths = " ".join(f'"{root}/{name}"' for name in PATHS)
    return run_functions(("restore_release_paths", "commit_release"), f"commit_release {paths}", _environment(root), root)


class TestTheDispatchIsTheLastStep:
    def test_the_publish_path_runs_in_the_order_of_the_invariant(self, script: str) -> None:
        offsets = [_command(script, pattern) for pattern in ORDER]
        assert offsets == sorted(offsets), (
            "refusals, preflight, changelog stamp, pinned build, manifest, release commit, push, CI, tag, "
            "signing run, signed files, PyPI read, guard and dispatch, wait for PyPI, read-back"
        )

    def test_the_commit_is_made_before_anything_is_pushed(self, script: str) -> None:
        assert _command(script, COMMIT) < _command(script, r"^rf_push_release$")

    def test_nothing_is_changed_before_the_refusals_have_run(self, script: str) -> None:
        """The stamp is the first edit of the tree: every check that needs no change comes before it."""
        assert max(_command(script, pattern) for pattern in REFUSALS) < _command(script, STAMP)

    def test_no_commit_is_made_once_the_dispatch_has_run(self, script: str) -> None:
        after = script[_command(script, PUBLISH) :]
        assert not re.search(r"git .*\bcommit\b", after), (
            "the one release commit is commit_release's"
        )

    def test_after_the_dispatch_only_the_waits_and_the_read_back_of_pypi_run(self, script: str) -> None:
        tail = script[script.index("\npublish_release\n") + len("\npublish_release\n") :]
        commands = [
            line
            for line in tail.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "echo"))
        ]
        assert commands == ["rf_wait_pypi", "rf_verify_pypi"]

    def test_the_guard_is_asked_before_the_workflow_is_dispatched(self) -> None:
        source = function_source("publish_release", RELEASE_FLOW.read_text(encoding="utf-8"))
        assert source.index("rf_py guard check") < source.index("rf_dispatch_publish")

    def test_only_publish_release_dispatches_and_the_workflow_is_started_in_one_place(self) -> None:
        both = (
            RELEASE_SCRIPT.read_text(encoding="utf-8") + RELEASE_FLOW.read_text(encoding="utf-8")
        )
        code = "\n".join(_code(both))
        publish = "\n".join(_code(function_source("publish_release")))
        callers = [
            line.strip()
            for line in code.splitlines()
            if re.search(r"\brf_dispatch_publish\b", line) and not line.startswith("rf_dispatch_publish()")
        ]
        assert callers == ["rf_dispatch_publish"]
        assert "rf_dispatch_publish" in publish
        assert len(re.findall(r"guard publish-dispatch", code)) == 1
        assert not re.search(r"gh .*\bworkflow run\b", code), "gh starts the workflow inside the guard module only"

    def test_the_scripts_hold_no_upload_command_and_no_retired_name(self) -> None:
        files = [
            RELEASE_SCRIPT,
            RELEASE_FLOW,
            RELEASE_FLOW.with_name("project.sh"),
            REPO_ROOT / "scripts" / "check-package.sh",
            *sorted((REPO_ROOT / "scripts" / "release").glob("*.py")),
        ]
        offenders = []
        for path in files:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\buv\s+publish\b", line) or RETIRED.search(line):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        assert offenders == []
        assert not (REPO_ROOT / "scripts" / "lib" / ("env-helpers" + ".sh")).exists()

    def test_the_commit_hooks_are_not_bypassed_by_an_option(self, script: str) -> None:
        flow = RELEASE_FLOW.read_text(encoding="utf-8")
        assert "--no-verify" not in script
        assert "--no-verify" not in flow

    def test_the_skip_switches_of_the_hooks_are_named_only_to_refuse_them(
        self, script: str,
    ) -> None:
        """A run with a switch exported is refused (the pipeline tests run it); no code sets or reads one else."""
        flow = RELEASE_FLOW.read_text(encoding="utf-8")
        refusing = function_source("rf_require_hooks_armed", flow)
        outside = "\n".join(_code(script + flow)).replace("\n".join(_code(refusing)), "")
        assert re.search(r"\bSKIP\b", refusing)
        assert re.search(r"\bONLY\b", refusing)
        assert not re.search(r"\bSKIP\b", outside)
        assert not re.search(r"\bONLY\b", outside)

    def test_the_ci_skip_hatch_is_gone(self, script: str) -> None:
        both = script + RELEASE_FLOW.read_text(encoding="utf-8")
        assert [ln for ln in _code(both) if "RELEASE_SKIP_CI_CHECK" in ln] == [], (
            "no variable skips the CI check: a commit CI has not verified is what the guard refuses"
        )

    def test_a_pushed_tag_is_never_moved_and_nothing_is_force_pushed(self, script: str) -> None:
        both = script + RELEASE_FLOW.read_text(encoding="utf-8")
        commands = [ln for ln in both.splitlines() if not ln.lstrip().startswith(("#", "echo"))]
        text = "\n".join(commands)
        assert not re.search(r"git\b.*\bpush\b.*(--force|--delete|\s-f\b|\s\+|\s:refs)", text)
        assert not re.search(r"git\b.*\btag\b.*(\s-d\b|--delete|\s-f\b|--force)", text)

    def test_the_script_never_resets_by_itself(self, script: str) -> None:
        both = script + RELEASE_FLOW.read_text(encoding="utf-8")
        resets = [ln for ln in both.splitlines() if re.search(r"\bgit\b.*\breset\b", ln)]
        assert all(ln.lstrip().startswith(("echo", "#")) for ln in resets), resets

    def test_a_refused_guard_goes_through_the_function_that_names_the_way_back(self) -> None:
        source = function_source("publish_release")
        assert "|| rf_stop " in source
        assert "rf_dispatch_publish" in source


class TestAFailureDropsNothingBehindItsBack:
    def test_a_refused_commit_restores_the_release_paths_then_stops(self, script: str) -> None:
        source = function_source("commit_release", script)
        restore = source.index('restore_release_paths "$@"')
        stop = source.index('err "the release commit was refused')
        assert restore < stop, "the paths are put back before the script exits"
        assert "restore --source=HEAD --staged --worktree" in function_source("restore_release_paths", script)
        assert "nothing was published" in source


class TestACommitThatTheHookAccepts:
    def test_it_commits_exactly_the_release_paths(self, repo: Path) -> None:
        _hook(repo, accepts=True)
        before = _git(repo, "rev-parse", "HEAD")
        done = _commit_release(repo)
        assert done.returncode == 0, done.stderr
        assert _git(repo, "rev-parse", "HEAD~1") == before
        assert sorted(
            _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines(),
        ) == sorted(PATHS)
        assert _git(repo, "log", "-1", "--format=%s") == MESSAGE
        assert _git(repo, "status", "--porcelain") == "M notes.txt"

    def test_it_runs_the_hook(self, repo: Path) -> None:
        _hook(repo, accepts=True)
        _commit_release(repo)
        assert (repo.parent / "hook-ran").exists()


class TestACommitThatTheHookRefuses:
    def test_it_exits_non_zero_saying_nothing_was_published(self, repo: Path) -> None:
        _hook(repo, accepts=False)
        done = _commit_release(repo)
        assert done.returncode != 0
        assert "nothing was published" in done.stderr

    def test_head_stays_and_the_release_paths_are_back_at_it(self, repo: Path) -> None:
        _hook(repo, accepts=False)
        before = _git(repo, "rev-parse", "HEAD")
        _commit_release(repo)
        assert _git(repo, "rev-parse", "HEAD") == before
        assert _git(repo, "diff", "HEAD", "--", *PATHS) == "", (
            "index and work tree equal HEAD on the release paths"
        )
        assert _git(repo, "diff", "--cached", "--name-only") == "", "nothing is left staged"
        assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == "CHANGELOG.md at HEAD\n"

    def test_only_the_release_paths_are_touched(self, repo: Path) -> None:
        _hook(repo, accepts=False)
        _commit_release(repo)
        assert (repo / "notes.txt").read_text(
            encoding="utf-8",
        ) == "an unrelated edit the release must not touch\n"
        assert _git(repo, "status", "--porcelain") == "M notes.txt"

    def test_a_path_head_does_not_hold_is_removed(self, repo: Path) -> None:
        """The digest manifest of a first release is new: restoring it to HEAD means it is gone."""
        _git(repo, "rm", "-q", "--cached", "release-digests.json")
        _git(repo, "commit", "-q", "-m", "drop the manifest from HEAD")
        (repo / "release-digests.json").write_text("written by this release\n", encoding="utf-8")
        _hook(repo, accepts=False)
        _commit_release(repo)
        assert not (repo / "release-digests.json").exists()
        assert _git(repo, "status", "--porcelain") == "M notes.txt"


class TestAStopAfterTheDispatch:
    def test_it_names_the_run_the_state_pypi_may_be_in_and_the_command_that_resumes(
        self, repo: Path,
    ) -> None:
        done = run_functions(
            ("rf_stop_dispatched",),
            'rf_stop_dispatched "the publish.yml run did not succeed"\necho unreachable',
            _environment(repo),
            repo,
        )
        assert done.returncode == 1
        assert "STOPPED: the publish.yml run did not succeed" in done.stderr
        assert "run of v9.9.9 may be live or done" in done.stderr
        assert "PyPI may hold none, some or all of its files" in done.stderr
        assert "bash scripts/release-pypi.sh 9.9.9" in done.stderr
        assert "unreachable" not in done.stdout

    def test_it_resets_nothing(self, repo: Path) -> None:
        _hook(repo, accepts=True)
        _commit_release(repo)
        head = _git(repo, "rev-parse", "HEAD")
        run_functions(("rf_stop_dispatched",), "rf_stop_dispatched why", _environment(repo), repo)
        assert _git(repo, "rev-parse", "HEAD") == head, "the script never resets by itself"


class TestAStopAfterTheCommit:
    def test_it_says_nothing_was_uploaded_and_names_the_command_that_resumes(
        self, repo: Path,
    ) -> None:
        done = run_functions(
            ("rf_stop",), 'rf_stop "CI is red"\necho unreachable', _environment(repo), repo,
        )
        assert done.returncode == 1
        assert "STOPPED: CI is red" in done.stderr
        assert "Nothing was uploaded to PyPI" in done.stderr
        assert "bash scripts/release-pypi.sh 9.9.9" in done.stderr
        assert "unreachable" not in done.stdout
