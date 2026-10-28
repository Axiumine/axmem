"""The TestPyPI rehearsal of a release: is the repository set up for it, and what did ``publish-testpypi.yml`` do.

``scripts/release-testpypi.sh`` uploads the signed files of a release that ``scripts/release-pypi.sh`` has already made
(release commit, tag, signing run) to TestPyPI, through ``.github/workflows/publish-testpypi.yml`` and the GitHub
environment ``testpypi``, so that the trusted publisher, the environment and the upload are tried on the scratch index
before the PyPI upload that cannot be taken back. The rehearsal is advisory, not a gate: nothing here is read by the PyPI
path, and the PyPI path reads nothing from here.

Nothing is copied from the PyPI side. ``testpypi-ready`` is ``publish-ready`` (``scripts.release.publish``) with the key
``testpypi`` of ``facts.INDEXES``: the same checks of the repository, the environment, the tag ruleset and the SLSA
generator tag, none left out. ``testpypi-state``, ``testpypi-urls`` and ``testpypi-missing`` are ``pypi-state``,
``pypi-urls`` and ``pypi-missing`` of ``scripts.release.guard`` with that key, registered there. The index is never an
option, an input or an environment variable: it is the name of the command, and the one row of the table it selects holds
the file host, the workflow and the environment as literals. What is new here is the run of the rehearsal workflow,
``testpypi-run``, which adopts a live run of the tag or dispatches one, and follows it. It is orchestration only: every
condition of the upload is checked in the workflow (the job ``verify``) and in the guard, as for PyPI.

Exit codes of ``testpypi-run``: 0 the run succeeded, 1 it ended otherwise (a cancelled run is a failure) or none showed
in time, 2 GitHub could not be asked, 3 deadline. Stdlib only, like the rest of ``scripts.release``.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from functools import partial
from typing import Any

from .facts import (
    INDEXES,
    DeadlineError,
    FactsError,
    gh_json,
    poll,
    publish_runs,
    run_text,
)
from .publish import FAILED_LOG_LINES, SPENT_MARKERS, cmd_publish_ready

INDEX = "testpypi"
TESTPYPI_WORKFLOW = INDEXES[INDEX].workflow
TESTPYPI_ENVIRONMENT = INDEXES[INDEX].environment
SPENT_HINT = "TestPyPI refused a name already used: this version is spent on TestPyPI, PyPI is unaffected"


def _runs(args: argparse.Namespace) -> list[dict[str, Any]]:
    """The runs of ``publish-testpypi.yml`` dispatched on the tag at the commit, and no run of another workflow."""
    return publish_runs(args.repo, args.sha, args.tag, INDEX)


def _newest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The run with the highest id."""
    return max(rows, key=lambda row: int(row["databaseId"]))


def _start(args: argparse.Namespace) -> str | None:
    """The id of the run to follow: a live run of this tag (no second dispatch), else the run that a dispatch starts.

    None when the dispatched run does not show in the list within ``--timeout``.
    """
    rows = _runs(args)
    live = [row for row in rows if row.get("status") != "completed"]
    if live:
        found = _newest(live)
        print(f"A {TESTPYPI_WORKFLOW} run of {args.tag} is live ({found.get('url', '')}) - waiting for it, no second dispatch.", flush=True)
        return str(found["databaseId"])
    known = {row["databaseId"] for row in rows}
    print(f"Dispatching {TESTPYPI_WORKFLOW} on {args.tag} (it runs in the environment {TESTPYPI_ENVIRONMENT}, whose reviewers must approve)...", flush=True)
    run_text(["gh", "workflow", "run", TESTPYPI_WORKFLOW, "-R", args.repo, "--ref", args.tag, "-f", f"tag={args.tag}"])

    def probe() -> tuple[bool, list[dict[str, Any]]]:
        fresh = [row for row in _runs(args) if row["databaseId"] not in known]
        return bool(fresh), fresh

    try:
        fresh = poll(probe, time.time() + args.timeout, args.interval)
    except DeadlineError:
        print(f"ERROR: no {TESTPYPI_WORKFLOW} run of {args.tag} at {args.sha[:8]} appeared in time", file=sys.stderr)
        return None
    found = _newest(fresh)
    print(f"Dispatched: {found.get('url', '')}", flush=True)
    return str(found["databaseId"])


def _view(args: argparse.Namespace, run: str) -> dict[str, Any]:
    """The run ``run`` as GitHub shows it, once it is proven to be the dispatch of the tag at the commit."""
    row: dict[str, Any] = gh_json(
        ["run", "view", run, "-R", args.repo, "--json", "databaseId,status,conclusion,url,headSha,headBranch,event"],
    )
    found = (row.get("headSha"), row.get("headBranch"), row.get("event"))
    if found != (args.sha, args.tag, "workflow_dispatch"):
        raise FactsError(f"run {run} is {found}, not a dispatch of {args.tag} at {args.sha}")
    return row


def _status_line(row: dict[str, Any]) -> str:
    """What to tell about a run once: its status, and for a waiting run who must act."""
    status = str(row.get("status"))
    if status == "completed":
        return f"{TESTPYPI_WORKFLOW} run {row['databaseId']} completed: {row.get('conclusion')} — {row.get('url', '')}"
    shown = f"{TESTPYPI_WORKFLOW} run {row['databaseId']} is {status} — {row.get('url', '')}"
    if status == "waiting":
        shown += f"\n  a reviewer must approve the deployment to environment {TESTPYPI_ENVIRONMENT}: {row.get('url', '')}"
    return shown


def _failure_report(repo: str, run: str) -> None:
    """The tail of the failed log of ``run`` on stderr, with the hint when TestPyPI refused a used name."""
    done = run_text(["gh", "run", "view", run, "-R", repo, "--log-failed"], ok=(0, 1))
    tail = done.stdout.splitlines()[-FAILED_LOG_LINES:]
    for line in tail:
        print(f"  {line}", file=sys.stderr)
    if any(marker in line.lower() for line in tail for marker in SPENT_MARKERS):
        print(f"ERROR: {SPENT_HINT}", file=sys.stderr)


def cmd_testpypi_run(args: argparse.Namespace) -> int:
    """Adopt a live run of ``publish-testpypi.yml`` on the tag or dispatch one, and follow it to its end.

    Each change of status is printed once. A run that is cancelled is a failure: the rehearsal is advisory and is run
    again by hand, with nothing waiting on it.
    """
    run = _start(args)
    if run is None:
        return 1
    seen: list[str] = []

    def probe() -> tuple[bool, dict[str, Any]]:
        row = _view(args, run)
        line = _status_line(row)
        if line not in seen:
            seen.append(line)
            print(line, flush=True)
        return row.get("status") == "completed", row

    try:
        final = poll(probe, args.deadline, args.interval)
    except DeadlineError:
        print(f"ERROR: {TESTPYPI_WORKFLOW} did not finish before the deadline: {seen[-1]}", file=sys.stderr)
        return 3
    if final.get("conclusion") == "success":
        return 0
    _failure_report(args.repo, run)
    return 1


def register(add: Callable[..., argparse.ArgumentParser], seconds: Callable[[str], float]) -> None:
    """Add ``testpypi-ready`` and ``testpypi-run`` to the parser of ``guard``; ``add(name, run, *required_flags)`` is its ``command``."""
    add("testpypi-ready", partial(cmd_publish_ready, index=INDEX), "repo")
    run = add("testpypi-run", cmd_testpypi_run, "repo", "sha", "tag")
    run.add_argument("--deadline", type=seconds, required=True)
    run.add_argument("--interval", type=seconds, default=30.0)
    run.add_argument("--timeout", type=seconds, default=120.0)
