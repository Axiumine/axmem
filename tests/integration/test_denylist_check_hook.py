"""The denylist guard refuses staged changes, messages and pushes that match a clone-local term."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".githooks" / "denylist-check"
TERM = "zzforbidden"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None or shutil.which("git") is None,
    reason="the guard is a POSIX shell script; it is exercised on developer machines and Linux CI",
)


def _git(repo, *args):
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"},
    )


def _guard(repo, *args):
    return subprocess.run(  # noqa: S603
        ["sh", str(HOOK), *args],  # noqa: S607
        cwd=repo, capture_output=True, text=True, check=False,
        env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
    )


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".git" / "info").mkdir(exist_ok=True)
    (tmp_path / ".git" / "info" / "denylist").write_text(f"\n{TERM}\nother[-_ ]?term\n", encoding="utf-8")
    return tmp_path


def _commit(repo, name, text):
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", "chore: x")


def test_clean_staged_change_passes(repo):
    (repo / "a.txt").write_text("nothing special\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    assert _guard(repo, "staged").returncode == 0


@pytest.mark.parametrize("name,text", [
    ("a.txt", f"a line with {TERM.upper()} inside\n"),
    ("a.txt", "an Other-Term here\n"),
    (f"{TERM}.txt", "clean content\n"),
])
def test_staged_match_is_refused_without_echoing_the_term(repo, name, text):
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    result = _guard(repo, "staged")
    assert result.returncode == 1
    assert TERM not in result.stderr.lower()
    assert "denylist" in result.stderr


def test_removed_lines_are_not_flagged(repo):
    _commit(repo, "a.txt", f"{TERM}\n")
    (repo / "a.txt").write_text("clean now\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    assert _guard(repo, "staged").returncode == 0


def test_message_match_is_refused_and_comments_are_ignored(repo, tmp_path_factory):
    msg = tmp_path_factory.mktemp("msg") / "COMMIT_EDITMSG"
    msg.write_text(f"feat: x\n\nmentions {TERM}\n", encoding="utf-8")
    assert _guard(repo, "message", str(msg)).returncode == 1
    msg.write_text(f"feat: x\n# {TERM} only in a git comment\n", encoding="utf-8")
    assert _guard(repo, "message", str(msg)).returncode == 0


def test_range_refuses_unpushed_commits_with_a_match(repo):
    _commit(repo, "a.txt", "clean\n")
    assert _guard(repo, "range", "HEAD").returncode == 0
    _commit(repo, "b.txt", f"{TERM}\n")
    result = _guard(repo, "range", "HEAD")
    assert result.returncode == 1
    assert TERM not in result.stderr.lower()


def test_range_ignores_commits_a_remote_already_has(repo):
    _commit(repo, "a.txt", f"{TERM}\n")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert _guard(repo, "range", "HEAD").returncode == 0


def test_without_a_denylist_nothing_is_checked(repo):
    (repo / ".git" / "info" / "denylist").unlink()
    (repo / "a.txt").write_text(f"{TERM}\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    assert _guard(repo, "staged").returncode == 0


def test_unknown_mode_is_a_usage_error(repo):
    assert _guard(repo, "nope").returncode == 2
