"""Refuse a release whose exact commit has not passed CI on GitHub.

The release script runs the local gate, but the local gate cannot see what only GitHub Actions runs. This asks
GitHub for every push-triggered run on the commit and passes only when there is at least one, all have
completed, none failed, and one succeeded (runs that were all skipped verified nothing).

``--branch main`` keeps the runs of the branch push and drops the runs the tag push of a release starts
(the release flow checks the release commit before it tags it and again, for the branch runs, as the last
condition of its guard). ``--wait`` polls every ``--interval`` seconds until the same runs are green on two
polls in a row, a run is red, or no run appeared for ten minutes, and never past ``--deadline`` (epoch
seconds); the release flow sets one deadline for all its waits.

Exit codes: 0 = green, 1 = red / pending / no runs / nothing succeeded, 2 = GitHub query failed (or
more runs than the check reads), 3 = ``--wait`` reached its deadline.

    python3 -m scripts.release.ci_green --repo R --sha S [--branch B] [--wait --interval N --deadline E]
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - runs git, gh, uv and python with fixed argv lists, never a shell
import sys
import time
from typing import Any, NamedTuple

from .facts import (
    NO_RUN_GRACE_S,
    RUN_TIMEOUT_S,
    DeadlineError,
    FactsError,
    poll,
    seconds,
)

# skipped / neutral runs are not failures; everything else must be success
_OK = frozenset({"success", "skipped", "neutral"})
# the most runs one query reads: a commit with that many cannot be judged, because some may be missing
RUN_LIMIT = 200


class Query(NamedTuple):
    """Which runs to ask GitHub for: the repository, the commit and, optionally, the branch they ran on."""

    repo: str
    sha: str
    branch: str | None = None


def fetch_runs(repo: str, sha: str, branch: str | None = None) -> list[dict[str, Any]]:
    """Every push-triggered workflow run GitHub recorded for sha (only those of ``branch`` when given)."""
    argv = ["gh", "run", "list", "-R", repo, "--commit", sha, "--event", "push"]
    argv += ["--limit", str(RUN_LIMIT)]
    if branch:
        argv += ["--branch", branch]
    out = subprocess.run(  # nosec B603 - fixed argv, no shell
        [*argv, "--json", "workflowName,status,conclusion,url,databaseId"],
        capture_output=True,
        text=True,
        check=True,
        timeout=RUN_TIMEOUT_S,
    )
    runs: list[dict[str, Any]] = json.loads(out.stdout)
    if len(runs) >= RUN_LIMIT:
        raise FactsError(
            f"GitHub listed {len(runs)} runs for this commit, the most this check reads",
        )
    return runs


def problems_with(runs: list[dict[str, Any]]) -> list[str]:
    """One line per blocking run — empty list means green."""
    if not runs:
        return ["no CI runs recorded for this commit — push it and let CI run first"]
    problems = []
    for run in sorted(runs, key=lambda r: str(r.get("workflowName", ""))):
        name = run.get("workflowName", "?")
        if run.get("status") != "completed":
            problems.append(f"{name}: still {run.get('status')}")
        elif run.get("conclusion") == "cancelled":
            problems.append(
                f"{name}: cancelled (a newer push to main cancels the runs of an older commit) — run it again "
                f"with 'gh run rerun {run.get('databaseId', '<id>')}' or hold new pushes — {run.get('url', '')}",
            )
        elif run.get("conclusion") not in _OK:
            problems.append(f"{name}: {run.get('conclusion')} — {run.get('url', '')}")
    if not problems and not _succeeded(runs):
        problems.append(
            "no run concluded success (every run was skipped or neutral) — nothing was verified",
        )
    return problems


def _succeeded(runs: list[dict[str, Any]]) -> bool:
    """At least one run concluded ``success``."""
    return any(run.get("conclusion") == "success" for run in runs)


def verdict(runs: list[dict[str, Any]]) -> str:
    """``none`` (no run), ``red`` (a run failed, or all finished and none succeeded), ``pending`` or ``green``."""
    if not runs:
        return "none"
    if any(r.get("status") == "completed" and r.get("conclusion") not in _OK for r in runs):
        return "red"
    if any(r.get("status") != "completed" for r in runs):
        return "pending"
    return "green" if _succeeded(runs) else "red"


def wait_for_ci(query: Query, deadline: float, interval: float) -> tuple[str, list[dict[str, Any]]]:
    """Poll until the verdict is ``red``, ``green`` twice running, or ``none`` past the grace period.

    A commit starts its workflows over several seconds, so a first poll that shows only the fast ones green
    is not the end: the same set of runs must be green on two polls an interval apart. ``(verdict, runs)``.
    Raises ``DeadlineError`` at the deadline. A change in what is pending is printed once, not every poll.
    """
    started = time.time()
    seen: list[str] = []
    green_before: list[list[str]] = []

    def probe() -> tuple[bool, tuple[str, list[dict[str, Any]]]]:
        runs = fetch_runs(*query)
        state = verdict(runs)
        lines = problems_with(runs)
        if lines != seen:
            seen[:] = lines
            print(f"waiting for CI on {query.sha[:8]}: {'; '.join(lines) or state}", flush=True)
        names = sorted(str(r.get("workflowName")) for r in runs)
        stable = state == "green" and green_before == [names]
        green_before[:] = [names] if state == "green" else []
        done = (
            state == "red"
            or stable
            or (state == "none" and time.time() - started >= NO_RUN_GRACE_S)
        )
        return done, (state, runs)

    return poll(probe, deadline, interval)


def _runs(args: argparse.Namespace) -> list[dict[str, Any]]:
    """The runs to judge: one query, or the runs at the end of ``--wait``."""
    if not args.wait:
        return fetch_runs(args.repo, args.sha, args.branch)
    return wait_for_ci(Query(args.repo, args.sha, args.branch), args.deadline, args.interval)[1]


def main(argv: list[str] | None = None) -> int:
    """CLI entry — see module docstring for exit codes."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name on GitHub")
    parser.add_argument("--sha", required=True, help="commit being released")
    parser.add_argument("--branch", help="only the runs of this branch (the tag runs are dropped)")
    parser.add_argument("--wait", action="store_true", help="poll until the runs are done")
    parser.add_argument(
        "--deadline", type=seconds, help="epoch seconds; --wait stops there (exit 3)",
    )
    parser.add_argument("--interval", type=seconds, default=30.0, help="seconds between polls")
    args = parser.parse_args(argv)
    if args.wait and args.deadline is None:
        parser.error("--wait needs --deadline: a wait has no unbounded form")
    try:
        runs = _runs(args)
    except DeadlineError:
        print(f"ERROR: CI on {args.sha[:8]} did not finish before the deadline", file=sys.stderr)
        return 3
    except (OSError, FactsError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", "") or exc
        print(
            f"ERROR: could not query GitHub Actions for {args.sha[:8]}: {detail}", file=sys.stderr,
        )
        return 2
    problems = problems_with(runs)
    if problems:
        print(f"CI is not green for {args.sha[:8]} ({len(runs)} run(s)):", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(f"OK: all {len(runs)} CI run(s) green for {args.sha[:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
