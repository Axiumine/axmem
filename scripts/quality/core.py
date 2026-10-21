"""Shared plumbing for the quality gates: context, results, subprocess and git helpers."""

from __future__ import annotations

import os
import shutil
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

    @property
    def scoped(self) -> bool:
        return self.files is not None

    def py_files(self, prefixes: tuple[str, ...] | None = None) -> list[str]:
        files = [f for f in (self.files or []) if f.endswith(".py")]
        if prefixes:
            files = [f for f in files if f.startswith(prefixes)]
        return files


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


def run(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 1800,
    cwd: Path | None = None,
    scrub_git: bool = False,
) -> tuple[int, str]:
    """Run a command and return (exit code, combined output).

    ``scrub_git`` drops every ``GIT_*`` variable from the child's environment. A Git hook exports
    ``GIT_INDEX_FILE`` and friends, and test suites that create temporary repositories must not
    inherit them, or their git commands act on the repository being committed to.
    """
    base = {k: v for k, v in os.environ.items() if not (scrub_git and k.startswith("GIT_"))}
    merged = {**base, **(env or {})}
    try:
        proc = subprocess.run(  # nosec B603 - argv lists built from constants and git output, never a shell
            argv,
            cwd=cwd or ROOT,
            env=merged,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return 127, f"command not found: {exc.filename}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s: {' '.join(argv)}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def check(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 1800,
    scrub_git: bool = False,
) -> tuple[bool, str]:
    code, out = run(argv, env=env, timeout=timeout, scrub_git=scrub_git)
    return code == 0, out


def git_lines(*args: str) -> list[str]:
    code, out = run(["git", *args], timeout=60)
    return [line for line in out.splitlines() if line.strip()] if code == 0 else []


def staged_files() -> list[str]:
    files = git_lines("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [f for f in files if (ROOT / f).exists()]


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
