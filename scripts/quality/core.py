"""Shared plumbing for the quality gates: context, results, subprocess and git helpers."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess  # nosec B404 - runs the quality tools with fixed argv lists, never a shell
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = ".githooks"

# Static-analysis scopes. Tests are measured by radon (complexity/maintainability) only.
SOURCE_PATHS = ["app", "main.py", "test_harness", "scripts"]
RADON_PATHS = [*SOURCE_PATHS, "tests"]
TYPED_PATHS = ["app", "main.py", "scripts"]
PYLINT_PATHS = ["app", "main.py", "test_harness", "scripts"]
SUITES = ["tests/architecture", "tests/integration", "tests/e2e"]


@dataclass
class Ctx:
    """What a gate may look at."""

    stage: str
    files: list[str] | None  # None => whole project
    env: dict[str, str] = field(default_factory=dict)
    # Staged deletions: nothing to lint in a removed file, but its removal can break what is left (a scoped gate must see it)
    removed: list[str] = field(default_factory=list)

    @property
    def scoped(self) -> bool:
        return self.files is not None

    def touches(self, *prefixes: str) -> bool:
        """Whether a staged file is one of the given files or lies under one of the given directories."""
        return any(f.startswith(prefixes) for f in [*(self.files or []), *self.removed])

    def py_files(self, prefixes: tuple[str, ...] | None = None) -> list[str]:
        files = [f for f in (self.files or []) if f.endswith(".py")]
        if prefixes:
            files = [f for f in files if f.startswith(prefixes)]
        return files

    def py_changed(self, prefixes: tuple[str, ...] | None = None) -> bool:
        """Whether a Python file was staged or removed: what a gate over the whole project needs to decide to run."""
        return bool(self.py_files(prefixes)) or any(
            f.endswith(".py") and (not prefixes or f.startswith(prefixes)) for f in self.removed
        )


@dataclass
class Result:
    name: str
    status: str  # PASS | FAIL | SKIP
    seconds: float = 0.0
    output: str = ""
    note: str = ""


class Skip(Exception):  # noqa: N818 - control-flow signal, not an error
    """Raised by a gate that cannot or need not run (reason in args[0])."""


class MissingToolError(Exception):
    """Raised by a gate whose tool or daemon is absent (reason in args[0]).

    The gate fails instead of skipping: a push that these gates guard must not pass because the machine cannot
    run one of them. ``SKIP=<gate>`` is the deliberate way to leave a gate out.
    """


GateFn = Callable[[Ctx], tuple[bool, str]]


def venv_tool(name: str) -> list[str]:
    """Command prefix for a tool installed in the interpreter's environment."""
    exe = Path(sys.executable).with_name(name + (".exe" if os.name == "nt" else ""))
    return [str(exe)] if exe.exists() else [sys.executable, "-m", name]


def venv_binary(name: str) -> str:
    """Path of an executable that the project environment installs.

    The pinned binaries (shellcheck, actionlint, twine, ...) come from the ``dev`` group: one that is absent means the
    toolchain was not synced, which fails the gate (``venv_tool``'s ``python -m`` fallback would run something else).
    Outside a virtual environment the directory of the interpreter is a system one, whose tools are not the pinned ones.
    """
    if sys.prefix == sys.base_prefix:
        raise MissingToolError(f"{name} is taken from the project environment, but {sys.executable} is not in a virtual environment (uv run ...)")
    exe = Path(sys.executable).with_name(name + (".exe" if os.name == "nt" else ""))
    if not exe.exists():
        raise MissingToolError(f"{name} is not installed in the project environment (uv sync --locked --all-extras --dev)")
    return str(exe)


def run(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 1800,
    cwd: Path | None = None,
    scrub_git: bool = False,
    own_session: bool = False,
) -> tuple[int, str]:
    """Run a command and return (exit code, combined output).

    ``scrub_git`` drops every ``GIT_*`` variable from the child's environment. A Git hook exports
    ``GIT_INDEX_FILE`` and friends, and test suites that create temporary repositories must not
    inherit them, or their git commands act on the repository being committed to.

    ``own_session`` starts the command in a session of its own and, at the timeout, kills the whole process group:
    a command that forks workers (mutmut's test runs hold the GPU and a Qdrant node) would otherwise leave them running
    after only the direct child is killed.
    """
    merged = _environment(env, scrub_git)
    grouped = own_session and os.name != "nt"
    try:
        with subprocess.Popen(  # nosec B603 - argv lists built from constants and git output, never a shell
            argv,
            cwd=cwd or ROOT,
            env=merged,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=grouped,
        ) as proc:
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                _stop(proc, grouped)
                return 124, f"timed out after {timeout}s: {' '.join(argv)}"
    except FileNotFoundError as exc:
        return 127, f"command not found: {exc.filename}"
    return proc.returncode, (stdout or "") + (stderr or "")


def _environment(env: dict[str, str] | None, scrub_git: bool) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if not (scrub_git and k.startswith("GIT_"))}
    return {**base, **(env or {})}


def _stop(proc: subprocess.Popen[str], grouped: bool) -> None:
    """Kill a command that timed out, its whole process group when it has one, and collect what it left."""
    if grouped:
        os.killpg(proc.pid, signal.SIGKILL)
    else:
        proc.kill()
    proc.communicate()


def check(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 1800,
    scrub_git: bool = False,
) -> tuple[bool, str]:
    code, out = run(argv, env=env, timeout=timeout, scrub_git=scrub_git)
    return code == 0, out


def git_lines(*args: str, strict: bool = False) -> list[str]:
    """The non-empty lines git prints; none when it fails, unless ``strict`` makes that a failed gate (an empty answer would pass)."""
    code, out = run(["git", *args], timeout=60)
    if code != 0 and strict:
        raise MissingToolError(f"git {' '.join(args)} failed: {out.strip()}")
    return [line for line in out.splitlines() if line.strip()] if code == 0 else []


def base_ref() -> str:
    """What a push is compared with: the branch's upstream, else ``main`` of the remote being pushed to, else ``origin/main``,
    else the commit before ``HEAD``.

    The upstream comes first because ``origin`` is not always the project's own remote (a clone can track another remote
    than the one it was cloned from), and a diff against the wrong one would hold the whole history instead of the push.
    A branch pushed for the first time (``git push -u axiumine feat/x``) has no upstream yet: the pre-push hook names the
    remote in ``QUALITY_PUSH_REMOTE``, and that remote's ``main`` is the base.
    """
    upstream = git_lines("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream:
        return upstream[0].strip()
    pushed = os.environ.get("QUALITY_PUSH_REMOTE", "")
    for candidate in (f"{pushed}/main" if pushed else "", "origin/main"):
        if candidate and git_lines("rev-parse", "--verify", "-q", f"refs/remotes/{candidate}"):
            return candidate
    return "HEAD~1"


def repo_files() -> list[str]:
    """The files a build or a commit of the project sees: tracked, and untracked ones that git does not ignore."""
    code, out = run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], timeout=60, scrub_git=True)
    if code != 0:
        raise MissingToolError(f"git could not list the files of the project: {out.strip()}")
    return [name for name in out.split("\0") if name and (ROOT / name).is_file()]


def staged_files() -> list[str]:
    files = git_lines("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [f for f in files if (ROOT / f).exists()]


def removed_files() -> list[str]:
    """The paths a commit deletes, a renamed file's old path included (``--no-renames``)."""
    return git_lines("diff", "--cached", "--name-only", "--no-renames", "--diff-filter=D")


def load_dotenv_values(keys: tuple[str, ...]) -> dict[str, str]:
    """Return only the requested keys, from the process env first, then the dotenv file."""
    found = {k: os.environ[k] for k in keys if os.environ.get(k)}
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for raw in dotenv.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.removeprefix("export ").strip()
            if key in keys and key not in found:
                found[key] = value.strip().strip("'\"")
    return found


def docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    code, _ = run(["docker", "info"], timeout=20)
    return code == 0


def scoped_paths(ctx: Ctx, default: list[str], prefixes: tuple[str, ...]) -> list[str]:
    """Whole-project default, or the matching changed files (Skip when none)."""
    if not ctx.scoped:
        return default
    files = ctx.py_files(prefixes)
    if not files:
        raise Skip("no matching staged Python files")
    return files
