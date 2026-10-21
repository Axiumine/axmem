"""The pre-push hook refuses a push whose pipeline fails, and a push that no pipeline checked.

The hook is the one place that runs the GPU, Docker and Qdrant gates, so a push must not get past it by accident. Real
``git push`` runs go to a bare repository in a temporary directory, with a stand-in ``uv`` that records how it is called
and exits as told; what the pipeline itself does is proven by ``test_quality_runner.py``.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[2] / ".githooks"
SYSTEM_PATH = "/usr/bin:/bin"
PIPELINE = "run --frozen python -m scripts.quality stage pre-push"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None or shutil.which("git") is None,
    reason="the hook is a POSIX shell script",
)


class Pushes:
    """A working repository with the hook installed, a bare remote, and a ``uv`` that records its calls."""

    def __init__(self, tmp_path):
        self.work = tmp_path / "work"
        self.remote = tmp_path / "remote.git"
        self.bin = tmp_path / "bin"
        self.log = tmp_path / "uv.log"
        self.log.touch()
        self.bin.mkdir()
        self.set_uv_exit(0)
        self.env = {
            "PATH": f"{self.bin}:{SYSTEM_PATH}", "UV_LOG": str(self.log),
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        self.git(tmp_path, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.git(tmp_path, "init", "-q", "-b", "main", str(self.work))
        (self.work / ".githooks").mkdir()
        for name in ("pre-push", "denylist-check"):
            shutil.copy2(HOOKS / name, self.work / ".githooks" / name)
            (self.work / ".githooks" / name).chmod(0o755)
        self.commit("first")
        self.git(self.work, "config", "core.hooksPath", ".githooks")
        self.git(self.work, "remote", "add", "origin", str(self.remote))

    def set_uv_exit(self, code):
        uv = self.bin / "uv"
        uv.write_text(f'#!/bin/sh\necho "$@" >> "$UV_LOG"\nexit {code}\n', encoding="utf-8")
        uv.chmod(0o755)

    def git(self, cwd, *args):
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(cwd), *args],  # noqa: S607
            capture_output=True, text=True, env=self.env, check=True,
        ).stdout.strip()

    def commit(self, message):
        (self.work / "file.txt").write_text(message, encoding="utf-8")
        self.git(self.work, "add", "file.txt")
        self.git(self.work, "commit", "-q", "-m", message)

    def push(self, *refspecs):
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(self.work), "push", "origin", *refspecs],  # noqa: S607
            capture_output=True, text=True, env=self.env, check=False,
        )

    def remote_refs(self):
        return self.git(self.remote, "for-each-ref", "--format=%(refname)").splitlines()

    def pipeline_runs(self):
        return self.log.read_text(encoding="utf-8").splitlines()

    def branch_elsewhere(self, name):
        """A branch on a commit of its own, with ``main`` checked out again."""
        self.git(self.work, "checkout", "-q", "-b", name)
        self.commit(name)
        self.git(self.work, "checkout", "-q", "main")


@pytest.fixture
def pushes(tmp_path):
    return Pushes(tmp_path)


def test_a_push_of_the_checked_out_branch_runs_the_pipeline_and_goes_through(pushes):
    result = pushes.push("main")

    assert result.returncode == 0, result.stderr
    assert pushes.pipeline_runs() == [PIPELINE]
    assert pushes.remote_refs() == ["refs/heads/main"]


def test_a_failing_pipeline_refuses_the_push(pushes):
    pushes.set_uv_exit(1)

    result = pushes.push("main")

    assert result.returncode != 0
    assert pushes.pipeline_runs() == [PIPELINE]
    assert pushes.remote_refs() == []


def test_a_machine_without_uv_refuses_the_push(pushes):
    (pushes.bin / "uv").unlink()

    result = pushes.push("main")

    assert result.returncode != 0
    assert pushes.remote_refs() == []


def test_a_second_branch_on_the_checked_out_commit_is_gated_like_the_first(pushes):
    result = pushes.push("main", "main:copy")

    assert result.returncode == 0, result.stderr
    assert pushes.pipeline_runs() == [PIPELINE]
    assert sorted(pushes.remote_refs()) == ["refs/heads/copy", "refs/heads/main"]


def test_a_branch_that_is_not_checked_out_is_refused_instead_of_passing_unchecked(pushes):
    pushes.branch_elsewhere("other")

    result = pushes.push("other")

    assert result.returncode != 0
    assert "refs/heads/other is not the checked-out HEAD" in result.stderr
    assert pushes.pipeline_runs() == []
    assert pushes.remote_refs() == []


def test_a_push_that_adds_a_branch_that_is_not_checked_out_is_refused_whole(pushes):
    pushes.branch_elsewhere("other")

    result = pushes.push("main", "other")

    assert result.returncode != 0
    assert pushes.pipeline_runs() == []
    assert pushes.remote_refs() == []


def test_deleting_a_remote_branch_needs_no_pipeline(pushes):
    assert pushes.push("main:old").returncode == 0
    pushes.log.write_text("", encoding="utf-8")
    assert pushes.remote_refs() == ["refs/heads/old"]

    result = pushes.push("--delete", "old")

    assert result.returncode == 0, result.stderr
    assert pushes.pipeline_runs() == []
    assert pushes.remote_refs() == []


def test_an_annotated_tag_on_the_checked_out_commit_is_gated(pushes):
    pushes.git(pushes.work, "tag", "-a", "v1", "-m", "release")

    result = pushes.push("v1")

    assert result.returncode == 0, result.stderr
    assert pushes.pipeline_runs() == [PIPELINE]
    assert pushes.remote_refs() == ["refs/tags/v1"]


def test_a_tag_on_an_older_commit_is_refused(pushes):
    pushes.git(pushes.work, "tag", "-a", "v0", "-m", "old")
    pushes.commit("second")

    result = pushes.push("v0")

    assert result.returncode != 0
    assert pushes.pipeline_runs() == []
    assert pushes.remote_refs() == []
