"""The reads and waits of the PyPI publication: is the repository set up for it, and what did ``publish.yml`` do.

``publish.yml`` is the only workflow that uploads to PyPI, and it runs only when ``scripts/release-pypi.sh``
dispatches it on a release tag. Nothing inside the workflow file can keep another ref from publishing, so the
safety sits in GitHub's settings: the environment ``pypi`` (reviewers, tag rule ``v*.*.*``, no admin bypass) and
a tag ruleset on ``v*``. ``publish-ready`` reads those settings before every release; the other commands find,
start and follow the dispatched run. Each is a small function over ``facts.gh_json`` and ``facts.run_text``, and
``scripts.release.guard`` only registers them (see its docstring for the command lines).

Exit codes: 0 holds, 1 refused or failed, 2 GitHub could not be asked (a 403 or 404 names the endpoint), 3 deadline.
Stdlib only, like the rest of ``scripts.release``.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from typing import Any

from .facts import (
    PUBLISH_WORKFLOW,
    DeadlineError,
    FactsError,
    gh_json,
    poll,
    publish_runs,
    run_text,
)

ENVIRONMENT = "pypi"
TAG_RULE = "v*.*.*"
# the pattern of the release tags; a ruleset that includes it (or every ref) protects them
TAG_REF = "refs/tags/v*"
TAG_RULES = ("creation", "update", "deletion", "non_fast_forward")
# "Selected branches and tags" of an environment: the answer when the rules are the ones listed under deployment-branch-policies
SELECTED_ONLY = {"protected_branches": False, "custom_branch_policies": True}
# the repository role Admin: the one actor a tag ruleset may let bypass its rules, so that the maintainer can make a tag
ADMIN_ROLE_ID = 5
MAIN_RULES = ("non_fast_forward", "deletion", "required_status_checks")
FAILED_LOG_LINES = 30
SPENT_MARKERS = ("previously used", "already exists")
SPENT_HINT = "PyPI refused a name already used: this version is spent"


def run_id(text: str) -> str:
    """``text`` as a run id (an argparse ``type``): digits only, so it cannot pass for an option of ``gh``."""
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError(f"{text!r} is not a run id")
    return text


def _read(endpoint: str) -> Any:
    """The JSON of ``gh api <endpoint>``; a refusal (403, 404) is a ``FactsError`` that names the endpoint in full.

    ``run_text`` shortens the words of a long command in its message, and a deployment-branch-policies path is cut.
    """
    try:
        return gh_json(["api", endpoint])
    except FactsError as exc:
        raise FactsError(f"gh api {endpoint}: {str(exc).partition(' failed: ')[2] or exc}") from exc


def _repo_problems(repo: str) -> list[str]:
    """Why the repository cannot publish: it must be public, and the token must be able to push."""
    body = _read(f"repos/{repo}")
    problems = []
    if body.get("private") is not False:
        problems.append(f"{repo} is not public: make it public before the first release")
    if not (body.get("permissions") or {}).get("push"):
        problems.append(f"the gh token has no push right on {repo}")
    return problems


def _workflow_problems(repo: str) -> list[str]:
    """Why ``publish.yml`` is not registered and active on the default branch."""
    state = _read(f"repos/{repo}/actions/workflows/{PUBLISH_WORKFLOW}").get("state")
    if state == "active":
        return []
    return [f"{PUBLISH_WORKFLOW} is {state or 'not registered'}, not active: merge it to the default branch first"]


def _reviewer_problems(env: dict[str, Any]) -> list[str]:
    """Why the environment ``pypi`` has no reviewer to approve a deployment, or lets administrators bypass them."""
    problems = []
    if env.get("can_admins_bypass") is not False:
        problems.append(f"environment {ENVIRONMENT} lets administrators bypass its rules")
    rules = [
        rule
        for rule in env.get("protection_rules") or []
        if rule.get("type") == "required_reviewers" and rule.get("reviewers")
    ]
    if not rules:
        problems.append(f"environment {ENVIRONMENT} has no required reviewer")
    return problems


def _selected_problems(env: dict[str, Any]) -> list[str]:
    """Why the environment is open to every branch, or to the protected ones only: the tag rule would not apply."""
    if env.get("deployment_branch_policy") == SELECTED_ONLY:
        return []
    return [f"environment {ENVIRONMENT} must deploy from selected branches and tags only (Deployment branches and tags: Selected)"]


def _policy_problems(policies: list[dict[str, Any]]) -> list[str]:
    """Why the deployment rules of the environment are not exactly the one tag rule ``v*.*.*``."""
    if [(p.get("name"), p.get("type")) for p in policies] == [(TAG_RULE, "tag")]:
        return []
    has = ", ".join(f"{p.get('name')} of type {p.get('type')}" for p in policies) or "none"
    return [f"environment {ENVIRONMENT} must have exactly one deployment rule, the tag {TAG_RULE} (it has {has})"]


def _environment_problems(repo: str) -> list[str]:
    """Why the environment ``pypi`` does not gate the upload: reviewers, no admin bypass, tags ``v*.*.*`` only."""
    base = f"repos/{repo}/environments/{ENVIRONMENT}"
    policies = _read(f"{base}/deployment-branch-policies").get("branch_policies") or []
    env = _read(base)
    return [*_reviewer_problems(env), *_selected_problems(env), *_policy_problems(policies)]


def _covers(ref_name: dict[str, Any]) -> bool:
    """True when the ``ref_name`` condition of a ruleset includes every release tag (``~ALL`` or a pattern as wide) and excludes none.

    An exclude counts when it is as wide as the release tags or begins inside them (``refs/tags/v0.*``): the guard
    cannot tell which release tags such a pattern leaves out, so it takes it that some are left unprotected.
    """
    include, exclude = ref_name.get("include") or [], ref_name.get("exclude") or []
    wide = any(p == "~ALL" or fnmatchcase(TAG_REF, p) for p in include)
    inside = TAG_REF.removesuffix("*")
    return wide and not any(fnmatchcase(TAG_REF, p) or p.startswith(inside) for p in exclude)


def _is_admin(actor: dict[str, Any]) -> bool:
    """True for an actor that is the repository role Admin or the organisation's owners."""
    kind = actor.get("actor_type")
    return kind == "OrganizationAdmin" or (kind == "RepositoryRole" and actor.get("actor_id") == ADMIN_ROLE_ID)


def _has_tag_rules(ruleset: dict[str, Any]) -> bool:
    """True when the ruleset covers the release tags and holds all of ``TAG_RULES``."""
    held = {rule.get("type") for rule in ruleset.get("rules") or []}
    return _covers((ruleset.get("conditions") or {}).get("ref_name") or {}) and held.issuperset(TAG_RULES)


def _protects_tags(ruleset: dict[str, Any]) -> bool:
    """True for an enforced tag ruleset that covers the release tags with ``TAG_RULES`` and is bypassed by admins alone.

    A creation rule that the Write role may bypass would let any collaborator make a release tag, and a tag is what
    starts the signing run and lets the publication be dispatched.
    """
    if ruleset.get("target") != "tag" or ruleset.get("enforcement") != "active":
        return False
    return _has_tag_rules(ruleset) and all(_is_admin(actor) for actor in ruleset.get("bypass_actors") or [])


def _ruleset_problems(repo: str) -> list[str]:
    """Why no ruleset keeps a release tag from being made by anybody, moved or deleted."""
    listed = [r for r in _read(f"repos/{repo}/rulesets") if r.get("target") == "tag"]
    details = [_read(f"repos/{repo}/rulesets/{r['id']}") for r in listed if r.get("enforcement") == "active"]
    if any(_protects_tags(detail) for detail in details):
        return []
    return [
        (
            f"no active tag ruleset covers {TAG_REF} with {', '.join(TAG_RULES)} and lets only repository admins bypass it"
            " (Settings > Rules > Rulesets: restrict creation to the maintainer, block update, deletion and force pushes)"
        ),
    ]


def _main_warnings(repo: str) -> list[str]:
    """What the rules in force on ``main`` lack; a warning only, because a bypass actor is not visible here."""
    held = {rule.get("type") for rule in _read(f"repos/{repo}/rules/branches/main")}
    return [f"main has no {name} rule" for name in MAIN_RULES if name not in held]


def cmd_publish_ready(args: argparse.Namespace) -> int:
    """Exit 0 when the repository is set up for the publication, 1 listing every reason when it is not."""
    problems = [
        *_repo_problems(args.repo),
        *_workflow_problems(args.repo),
        *_environment_problems(args.repo),
        *_ruleset_problems(args.repo),
    ]
    for warning in _main_warnings(args.repo):
        print(f"WARNING: {warning}", file=sys.stderr)
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"{args.repo} is ready to publish: {PUBLISH_WORKFLOW}, environment {ENVIRONMENT} and the tag ruleset hold")
    return 0


def _age_s(stamp: str) -> float:
    """Seconds since the GitHub timestamp ``stamp`` (``2026-01-31T10:00:00Z``); one that cannot be read is infinitely old."""
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def _newest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The run with the highest id."""
    return max(rows, key=lambda row: int(row["databaseId"]))


def cmd_publish_state(args: argparse.Namespace) -> int:
    """Print ``live``, ``recent`` or ``none``, then the run id, its URL and its age (``-`` where there is none to tell).

    ``live``: a run is not completed (its age is ``-``). ``recent``: the newest is a success that ended less than
    ``--lag`` seconds ago, so PyPI's listing may not show its files yet and a second dispatch would be noise; its age
    in whole seconds lets the caller count the wait for PyPI from the end of that run.
    """
    runs = publish_runs(args.repo, args.sha, args.tag)
    live = [row for row in runs if row.get("status") != "completed"]
    recent = [
        row
        for row in runs
        if row.get("status") == "completed"
        and row.get("conclusion") == "success"
        and _age_s(str(row.get("updatedAt"))) < args.lag
    ]
    for kind, rows in (("live", live), ("recent", recent)):
        if rows:
            found = _newest(rows)
            age = int(_age_s(str(found.get("updatedAt")))) if kind == "recent" else "-"
            print(f"{kind}\t{found['databaseId']}\t{found.get('url', '')}\t{age}")
            return 0
    print("none\t-\t-\t-")
    return 0


def cmd_publish_dispatch(args: argparse.Namespace) -> int:
    """Dispatch ``publish.yml`` on the tag and print ``id<TAB>url`` of the run it started; 1 when none shows in time."""
    known = {row["databaseId"] for row in publish_runs(args.repo, args.sha, args.tag)}
    run_text(["gh", "workflow", "run", PUBLISH_WORKFLOW, "-R", args.repo, "--ref", args.tag, "-f", f"tag={args.tag}"])

    def probe() -> tuple[bool, list[dict[str, Any]]]:
        fresh = [row for row in publish_runs(args.repo, args.sha, args.tag) if row["databaseId"] not in known]
        return bool(fresh), fresh

    try:
        fresh = poll(probe, time.time() + args.timeout, args.interval)
    except DeadlineError:
        print(f"ERROR: no {PUBLISH_WORKFLOW} run of {args.tag} at {args.sha[:8]} appeared in time", file=sys.stderr)
        return 1
    found = _newest(fresh)
    print(f"{found['databaseId']}\t{found.get('url', '')}")
    return 0


def _view(repo: str, run: str, sha: str, tag: str) -> dict[str, Any]:
    """The run ``run`` as GitHub shows it, once it is proven to be the dispatch of ``tag`` at ``sha``."""
    row: dict[str, Any] = gh_json(
        [
            "run",
            "view",
            run,
            "-R",
            repo,
            "--json",
            "databaseId,status,conclusion,url,headSha,headBranch,event",
        ],
    )
    found = (row.get("headSha"), row.get("headBranch"), row.get("event"))
    if found != (sha, tag, "workflow_dispatch"):
        raise FactsError(f"run {run} is {found}, not a dispatch of {tag} at {sha}")
    return row


def _status_line(row: dict[str, Any]) -> str:
    """What to tell about a run once: its status, and for a waiting run who must act."""
    status = str(row.get("status"))
    if status == "completed":
        return f"{PUBLISH_WORKFLOW} run {row['databaseId']} completed: {row.get('conclusion')} — {row.get('url', '')}"
    shown = f"{PUBLISH_WORKFLOW} run {row['databaseId']} is {status} — {row.get('url', '')}"
    if status == "waiting":
        shown += f"\n  a reviewer must approve the deployment to environment {ENVIRONMENT}: {row.get('url', '')}"
    return shown


def _failure_report(repo: str, run: str) -> None:
    """The tail of the failed log of ``run`` on stderr, with the hint when PyPI refused a used name."""
    done = run_text(["gh", "run", "view", run, "-R", repo, "--log-failed"], ok=(0, 1))
    tail = done.stdout.splitlines()[-FAILED_LOG_LINES:]
    for line in tail:
        print(f"  {line}", file=sys.stderr)
    if any(marker in line.lower() for line in tail for marker in SPENT_MARKERS):
        print(f"ERROR: {SPENT_HINT}", file=sys.stderr)


def _successor(args: argparse.Namespace, current: str) -> str | None:
    """The id of another dispatch of the same tag and sha that is live or newer than ``current``, else None."""
    others = [row for row in publish_runs(args.repo, args.sha, args.tag) if str(row["databaseId"]) != current]
    later = [row for row in others if row.get("status") != "completed" or int(row["databaseId"]) > int(current)]
    return str(_newest(later)["databaseId"]) if later else None


def cmd_publish_wait(args: argparse.Namespace) -> int:
    """Follow run ``--run`` to its end: 0 success, 1 another conclusion, 3 deadline.

    Each change of status is printed once. A run cancelled because a newer dispatch of the same tag and sha
    replaced it in the concurrency queue is not a failure: the wait moves to that run.
    """
    current: list[str] = [args.run_id]
    seen: list[str] = []

    def probe() -> tuple[bool, dict[str, Any]]:
        row = _view(args.repo, current[0], args.sha, args.tag)
        line = _status_line(row)
        if line not in seen:
            seen.append(line)
            print(line, flush=True)
        if row.get("status") == "completed" and row.get("conclusion") == "cancelled":
            successor = _successor(args, current[0])
            if successor is not None:
                print(f"run {current[0]} was cancelled; waiting for run {successor} of the same tag", flush=True)
                current[0] = successor
                return False, row
        return row.get("status") == "completed", row

    try:
        final = poll(probe, args.deadline, args.interval)
    except DeadlineError:
        print(f"ERROR: {PUBLISH_WORKFLOW} did not finish before the deadline: {seen[-1]}", file=sys.stderr)
        return 3
    if final.get("conclusion") == "success":
        return 0
    _failure_report(args.repo, str(final["databaseId"]))
    return 1


def register(add: Callable[..., argparse.ArgumentParser], seconds: Callable[[str], float]) -> None:
    """Add the four commands to the parser of ``guard``; ``add(name, run, *required_flags)`` is its ``command``."""
    add("publish-ready", cmd_publish_ready, "repo")
    state = add("publish-state", cmd_publish_state, "repo", "sha", "tag")
    state.add_argument("--lag", type=seconds, required=True)
    dispatch = add("publish-dispatch", cmd_publish_dispatch, "repo", "sha", "tag")
    dispatch.add_argument("--timeout", type=seconds, default=120.0)
    dispatch.add_argument("--interval", type=seconds, default=5.0)
    wait = add("publish-wait", cmd_publish_wait, "repo", "sha", "tag")
    wait.add_argument("--run", dest="run_id", type=run_id, required=True)
    wait.add_argument("--deadline", type=seconds, required=True)
    wait.add_argument("--interval", type=seconds, default=30.0)
