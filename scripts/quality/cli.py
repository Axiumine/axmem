"""Command line: run stages/gates, install the Git hooks.

    uv run python -m scripts.quality list
    uv run python -m scripts.quality stage pre-commit      # what the hook runs
    uv run python -m scripts.quality stage pre-push
    uv run python -m scripts.quality gate mypy             # one gate, whole project
    uv run python -m scripts.quality gate ruff --staged    # one gate, staged files only
    uv run python -m scripts.quality install               # point core.hooksPath at .githooks
    uv run python -m scripts.quality dist-check dist       # what a built wheel and sdist hold (see the package gate)
    uv run python -m scripts.quality wheel-check dist      # install and use the built wheel (see the wheel gate)

Environment knobs:

    SKIP=a,b         skip the named gates (e.g. SKIP=qodana,trivy git push)
    ONLY=a,b         run only the named gates of the stage
    QUALITY_FULL=1   treat pre-commit as project-wide instead of staged-files-only
    SEMGREP_PACKS    registry packs of the semgrep gate (default "p/python p/security-audit", needs the network,
                     except in pre-commit); an empty value leaves them out
    TRIVY_BIN        explicit trivy binary (otherwise PATH, then the docker image)
    MUTATION_TARGETS dotted patterns the mutation gate mutates instead of the app/ modules the push changes
    MUTATION_MAX_SURVIVORS  survivors tolerated by the mutation gate (default 0)
    MUTATION_TIMEOUT_S      seconds one mutmut run may take (a positive integer; default 86400, 24 h)
    QUALITY_PUSH_REMOTE     remote of the push (set by the pre-push hook): its main is the base of a branch without an upstream

Secrets such as QODANA_TOKEN are read from the git-ignored .env file by the gates that
declare them and are never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import package, registry, wheel
from .core import (
    HOOKS_DIR,
    ROOT,
    Ctx,
    MissingToolError,
    Result,
    Skip,
    removed_files,
    run,
    staged_files,
)

# Staged, these make `pre-commit` check the whole project: they decide what the scoped gates find in unchanged files.
GATE_CONFIG = frozenset({
    "pyproject.toml", "uv.lock", "ruff.toml", "radon.cfg", "pytest.ini", "vulture_whitelist.py", "trivy.yaml",
    ".trivyignore", ".semgrepignore", ".shellcheckrc",
})
GATE_CONFIG_DIRS = (".semgrep/",)


def run_gate(name: str, ctx: Ctx) -> Result:
    fn, _ = registry.GATES[name]
    started = time.monotonic()
    try:
        ok, out = fn(ctx)
    except Skip as why:
        return Result(name, "SKIP", time.monotonic() - started, note=str(why))
    except MissingToolError as why:
        return Result(name, "FAIL", time.monotonic() - started, output=str(why))
    except Exception:  # noqa: BLE001 - a crashed gate is a failed gate: the others of its group still report
        return Result(name, "FAIL", time.monotonic() - started, output=traceback.format_exc())
    return Result(name, "PASS" if ok else "FAIL", time.monotonic() - started, output=out)


def print_results(results: list[Result]) -> None:
    width = max((len(r.name) for r in results), default=4)
    for r in results:
        tail = f"  ({r.note})" if r.note else ""
        print(f"  {r.status:<4}  {r.name:<{width}}  {r.seconds:6.1f}s{tail}", flush=True)


def print_failures(results: list[Result], limit: int = 80) -> None:
    for r in results:
        if r.status != "FAIL":
            continue
        lines = r.output.rstrip().splitlines()
        print(f"\n--- {r.name} " + "-" * max(0, 60 - len(r.name)))
        if len(lines) > limit:
            print(f"... ({len(lines) - limit} earlier lines omitted)")
        print("\n".join(lines[-limit:]))


def stage_files(stage: str) -> tuple[list[str] | None, list[str]]:
    """The files a stage looks at (None: the whole project) and the staged deletions that scoped gates must still see."""
    if stage == "pre-commit" and os.environ.get("QUALITY_FULL") != "1":
        files, removed = staged_files(), removed_files()
        # A changed gate configuration or dependency pin can fail files that are not staged: check the whole project
        if any(name in GATE_CONFIG or name.startswith(GATE_CONFIG_DIRS) for name in files):
            return None, []
        return files, removed
    return None, []


def run_group(names: list[str], ctx: Ctx) -> list[Result]:
    """Run independent gates concurrently and report them."""
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        results = list(pool.map(lambda n: run_gate(n, ctx), names))
    print_results(results)
    return results


def run_stage_groups(stage: str, ctx: Ctx) -> tuple[bool, int]:
    """Run the stage's selected groups in order; print what was left out and the failures, stop at the first bad group.

    Returns whether every group passed and how many gates were left out (by ``SKIP`` or ``ONLY``).
    """
    left_out = 0
    for group in registry.STAGES[stage]:
        for name in group:
            if why := registry.left_out(name):
                left_out += 1
                print(f"  SKIP  {name}  ({why})", flush=True)
        names = [n for n in group if registry.selected(n)]
        if not names:
            continue
        results = run_group(names, ctx)
        if any(r.status == "FAIL" for r in results):
            print_failures(results)
            return False, left_out
    return True, left_out


def refused_selection(stage: str) -> str:
    """Why SKIP/ONLY cannot apply to the stage (a typo would leave gates out, or select none, in silence), or an empty string."""
    if unknown := registry.unknown_names():
        return f"SKIP/ONLY name no gate: {', '.join(unknown)}; try `list`"
    if registry.named("ONLY") and not any(registry.selected(n) for group in registry.STAGES[stage] for n in group):
        return f"ONLY={os.environ['ONLY']} selects no gate of the {stage} stage ({registry.stage_gates(stage)})"
    return ""


def run_stage(stage: str) -> int:
    if stage not in registry.STAGES:
        print(f"unknown stage {stage!r}; choose from {', '.join(registry.STAGES)}", file=sys.stderr)
        return 2
    if refusal := refused_selection(stage):
        print(refusal, file=sys.stderr)
        return 2
    files, removed = stage_files(stage)
    ctx = Ctx(stage=stage, files=files, removed=removed)
    if files is not None and not files and not removed:
        print(f"[{stage}] nothing staged; nothing to check")
        return 0
    print(f"[{stage}] {'staged files' if ctx.scoped else 'whole project'}", flush=True)
    ok, left_out = run_stage_groups(stage, ctx)
    if not ok:
        print(f"\n[{stage}] FAILED (bypass a gate with SKIP=<gate>; see docs/quality-gates.md)")
        return 1
    print(f"[{stage}] OK" + (f" ({left_out} gate{'s' if left_out != 1 else ''} left out)" if left_out else ""))
    return 0


def run_single(name: str, scope: str, extra: list[str]) -> int:
    if name not in registry.GATES:
        print(f"unknown gate {name!r}; try `list`", file=sys.stderr)
        return 2
    files: list[str] | None = None
    if scope == "staged":
        files = staged_files()
    elif scope == "files":
        files = extra
    result = run_gate(name, Ctx(stage="manual", files=files))
    print_results([result])
    print_failures([result], limit=400)
    return 1 if result.status == "FAIL" else 0


def install_hooks() -> int:
    hooks = ROOT / HOOKS_DIR
    if not hooks.is_dir():
        print(f"{HOOKS_DIR}/ not found", file=sys.stderr)
        return 1
    for hook in hooks.iterdir():
        if hook.is_file():
            hook.chmod(hook.stat().st_mode | 0o111)
    code, out = run(["git", "config", "core.hooksPath", HOOKS_DIR])
    print(out.strip() or f"core.hooksPath -> {HOOKS_DIR}")
    return code


def uninstall_hooks() -> int:
    code, out = run(["git", "config", "--unset", "core.hooksPath"])
    print(out.strip() or "core.hooksPath unset")
    return 0 if code in (0, 5) else code


def run_dist_check(dist: str) -> int:
    problems = package.check_dist(Path(dist), package.load_layout())
    if problems:
        print(package.HEADER + "\n".join(problems), file=sys.stderr)
        return 1
    print(f"OK: {dist} holds the wheel and the sdist of Axmem's source, and nothing else")
    return 0


def run_wheel_check(dist: str) -> int:
    try:
        ok, out = wheel.check_built_dist(Path(dist))
    except MissingToolError as why:
        ok, out = False, str(why)
    if not ok:
        print(out, file=sys.stderr)
        return 1
    print(f"OK: the wheel in {dist} installs on the locked dependencies, starts and resolves")
    return 0


def list_everything() -> int:
    for name, (_, desc) in registry.GATES.items():
        print(f"{name:<14} {desc}")
    for stage, groups in registry.STAGES.items():
        print(f"\n{stage}: " + "  ->  ".join("+".join(g) for g in groups))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.quality",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list gates and stages")
    sub.add_parser("install", help="enable the hooks in .githooks/")
    sub.add_parser("uninstall", help="disable the hooks")
    st = sub.add_parser("stage", help="run a stage (pre-commit, pre-push, ci)")
    st.add_argument("stage")
    gt = sub.add_parser("gate", help="run a single gate")
    gt.add_argument("name")
    gt.add_argument("--staged", action="store_true", help="only staged files")
    gt.add_argument("--files", nargs="+", help="only these files")
    dc = sub.add_parser("dist-check", help="check the wheel and the sdist in a directory (what the package gate checks)")
    dc.add_argument("dist", help="directory that holds one wheel and one sdist")
    wc = sub.add_parser("wheel-check", help="install and use the wheel in a directory (what the wheel gate checks)")
    wc.add_argument("dist", help="directory that holds one wheel and one sdist")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "list":
        return list_everything()
    if args.cmd == "install":
        return install_hooks()
    if args.cmd == "uninstall":
        return uninstall_hooks()
    if args.cmd == "stage":
        return run_stage(args.stage)
    if args.cmd == "dist-check":
        return run_dist_check(args.dist)
    if args.cmd == "wheel-check":
        return run_wheel_check(args.dist)
    scope = "staged" if args.staged else "files" if args.files else "all"
    return run_single(args.name, scope, args.files or [])
