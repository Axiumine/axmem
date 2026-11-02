# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The pull request that carries a release commit to ``main``, and the merge commit the tag is made on.

The rules for ``main`` refuse every direct push, so ``scripts/release-pypi.sh`` pushes the release commit to the branch
``release/<version>``, opens a pull request, waits for the checks the rules for ``main`` require, and merges it with a merge
commit (``gh pr merge --merge``: never ``--admin``, never ``--auto``). The tag ``v<version>`` is then made on that merge
commit, not on the release commit: the release guard asks for the push-triggered CI runs of ``main`` on the tagged commit,
and only the merge commit has them (the release commit was a branch and a pull request head). The merge commit has ``main``
and the release commit as its parents and the release commit's tree, which ``merge-commit`` checks, so the manifest
``release-digests.json`` committed there is the one the build wrote and ``release.yml`` rebuilds from.

Each command reads its state from GitHub and git, never from a file of its own, so a re-run of the release script asks again
and continues: ``find`` (is there a pull request for this release commit, and is it merged), ``create``, ``wait`` (the
required checks of the pull request head), ``merge`` and ``merge-commit`` (the commit on ``main`` that carries the release
commit).

Exit codes: 0 = done / green, 1 = refused (a red check, a closed pull request, a merge GitHub refused, a merge commit with
another tree), 2 = GitHub or git could not be asked, 3 = ``wait`` reached its deadline, 4 = ``merge-commit`` found none.

Stdlib only, like the rest of ``scripts.release``. Run from the repository root:

    pull_request find         --repo R --branch B --sha S
    pull_request create       --repo R --branch B --package P --version V --tag T
    pull_request wait         --repo R --number N --sha S --deadline E [--interval N]
    pull_request merge        --repo R --number N --sha S
    pull_request merge-commit --sha S [--root D] [--remote origin]
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Callable
from typing import Any

from .facts import (
    NO_RUN_GRACE_S,
    DeadlineError,
    FactsError,
    gh_json,
    poll,
    run_text,
    seconds,
)

BASE = "main"
# the most pull requests of one branch that one query reads, and the most check runs of one commit
LIMIT = 100
# a check run that finished well, or that GitHub counts as passed (a skipped required check does not block a merge)
PASSED = frozenset({"success", "skipped", "neutral"})
# the body of the pull request: what it is, so a reader who finds it in the list needs nothing else
BODY = """Release commit of {package} {version}, made by scripts/release-pypi.sh.

It changes two files: CHANGELOG.md (the section [Unreleased] stamped as {tag}) and release-digests.json (the sha256 of the \
wheel and the sdist built from this tree).

The script merges it with a merge commit once the required checks are green, then tags the merge commit {tag}. The tag \
starts the signing run (release.yml). Nothing reaches PyPI before the signed files are verified and a reviewer approves \
the environment pypi. Do not squash or rebase it: the tag must point at a commit that has this commit's tree. See \
docs/release.md.
"""


def _view(repo: str, number: str) -> dict[str, Any]:
    """``state`` (OPEN, CLOSED or MERGED) and ``headRefOid`` of the pull request ``number``."""
    view: dict[str, Any] = gh_json(["pr", "view", number, "-R", repo, "--json", "state,headRefOid"])
    return view


def find_pull_request(repo: str, branch: str, sha: str) -> tuple[str, str, str]:
    """``(kind, number, url)`` of the pull request of ``branch`` whose head is ``sha``: ``merged``, ``open`` or ``none``.

    A pull request of the branch at another head is not this release's. A closed one that was not merged is not one
    either: a new pull request is opened for the same branch.
    """
    rows: list[dict[str, Any]] = gh_json(
        ["pr", "list", "-R", repo, "--head", branch, "--base", BASE, "--state", "all", "--limit", str(LIMIT),
         "--json", "number,state,headRefOid,url"],
    )
    if len(rows) >= LIMIT:
        raise FactsError(f"GitHub listed {len(rows)} pull requests of {branch}, the most this check reads")
    mine = sorted((row for row in rows if row.get("headRefOid") == sha), key=lambda row: int(row["number"]))
    for kind, state in (("merged", "MERGED"), ("open", "OPEN")):
        for row in mine:
            if row.get("state") == state:
                return kind, str(row["number"]), str(row.get("url") or "-")
    return "none", "0", "-"


def cmd_find(args: argparse.Namespace) -> int:
    print("\t".join(find_pull_request(args.repo, args.branch, args.sha)))
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    """Open the pull request of ``--branch`` into ``main`` with a title and a body the script writes: no attribution line."""
    title = f"chore(release): release {args.package} {args.version} to PyPI"
    body = BODY.format(package=args.package, version=args.version, tag=args.tag)
    out = run_text(
        ["gh", "pr", "create", "-R", args.repo, "--base", BASE, "--head", args.branch, "--title", title, "--body", body],
    ).stdout.strip()
    found = re.search(r"/pull/([0-9]+)\s*$", out)
    if found is None:
        raise FactsError(f"gh pr create did not print the address of a pull request: {out[:200]!r}")
    print(f"open\t{found.group(1)}\t{out.splitlines()[-1]}")
    return 0


def required_contexts(repo: str) -> list[str]:
    """The names of the checks the rules in force on ``main`` require, from every ruleset that applies to it."""
    rules: list[dict[str, Any]] = gh_json(["api", f"repos/{repo}/rules/branches/{BASE}"])
    names: list[str] = []
    for rule in rules:
        if rule.get("type") == "required_status_checks":
            checks = (rule.get("parameters") or {}).get("required_status_checks") or []
            names += [str(check["context"]) for check in checks if check.get("context")]
    return sorted(set(names))


def check_runs(repo: str, sha: str) -> list[dict[str, Any]]:
    """Every check run GitHub recorded for ``sha``, whatever started it (the push of the branch, the pull request)."""
    body: dict[str, Any] = gh_json(["api", f"repos/{repo}/commits/{sha}/check-runs?per_page={LIMIT}"])
    runs: list[dict[str, Any]] = body.get("check_runs") or []
    if int(body.get("total_count", len(runs))) > len(runs):
        raise FactsError(f"GitHub listed {body.get('total_count')} check runs for {sha[:8]}, the most this check reads is {LIMIT}")
    return runs


def context_state(runs: list[dict[str, Any]]) -> str:
    """``none`` (no run of that name yet), ``red``, ``pending`` or ``green`` for the runs of one required check.

    Two events can record a run of the same name on one commit (the push of the branch and the pull request): a red one
    makes it red, and all must be done for green.
    """
    if not runs:
        return "none"
    if any(run.get("status") == "completed" and run.get("conclusion") not in PASSED for run in runs):
        return "red"
    return "green" if all(run.get("status") == "completed" for run in runs) else "pending"


def _failure_link(runs: list[dict[str, Any]]) -> str:
    """The address of the first run that failed, as a suffix of a line, or nothing."""
    links = [str(run.get("html_url", "")) for run in runs if run.get("status") == "completed" and run.get("conclusion") not in PASSED]
    return f" — {links[0]}" if links else ""


def judge(contexts: list[str], runs: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """``red`` if a required check is red, else ``pending`` if one is, else ``green``; and a line for each that is not green."""
    named = {name: [run for run in runs if run.get("name") == name] for name in contexts}
    states = {name: context_state(found) for name, found in named.items()}
    lines = [f"{name}: {state}{_failure_link(named[name])}" for name, state in states.items() if state != "green"]
    if "red" in states.values():
        return "red", lines
    return ("pending" if lines else "green"), lines


def wait_for_checks(repo: str, number: str, sha: str, deadline: float, interval: float) -> tuple[str, list[str]]:
    """Poll until the required checks of the pull request head are green on two polls running, one is red, or the pull request ends.

    ``(verdict, lines)``: ``green``, ``merged`` (somebody merged it meanwhile), ``red``, ``closed``, ``moved`` (the head is no
    longer ``sha``) or ``missing`` (a required check has no run after the grace period of the CI wait). Raises ``DeadlineError``.
    """
    contexts = required_contexts(repo)
    if not contexts:
        return "red", [f"the rules in force on {BASE} require no status check, so nothing holds the merge until the checks are green"]
    started = time.time()
    seen: list[str] = []
    green_before = [False]

    def probe() -> tuple[bool, tuple[str, list[str]]]:
        view = _view(repo, number)
        for state, verdict in (("MERGED", "merged"), ("CLOSED", "closed")):
            if view.get("state") == state:
                return True, (verdict, [f"the pull request is {state.lower()}"])
        if view.get("headRefOid") != sha:
            return True, ("moved", [f"the head of the pull request is {str(view.get('headRefOid'))[:8]}, not the release commit {sha[:8]}"])
        verdict, lines = judge(contexts, check_runs(repo, sha))
        if lines != seen:
            seen[:] = lines
            print(f"waiting for the required checks on {sha[:8]}: {'; '.join(lines) or verdict}", flush=True)
        stable = verdict == "green" and green_before[0]
        green_before[0] = verdict == "green"
        missing = verdict == "pending" and all(line.endswith(": none") for line in lines) and time.time() - started >= NO_RUN_GRACE_S
        return verdict == "red" or stable or missing, ("missing" if missing else "green" if stable else verdict, lines)

    return poll(probe, deadline, interval)


def cmd_wait(args: argparse.Namespace) -> int:
    try:
        verdict, lines = wait_for_checks(args.repo, args.number, args.sha, args.deadline, args.interval)
    except DeadlineError:
        print(f"ERROR: the required checks on {args.sha[:8]} did not finish before the deadline", file=sys.stderr)
        return 3
    if verdict in ("green", "merged"):
        print(f"OK: the pull request #{args.number} is {'merged' if verdict == 'merged' else 'green on every required check'}")
        return 0
    print(f"ERROR: the pull request #{args.number} cannot be merged ({verdict}):", file=sys.stderr)
    for line in lines:
        print(f"  - {line}", file=sys.stderr)
    return 1


def cmd_merge(args: argparse.Namespace) -> int:
    """Merge with a merge commit, pinned to the head ``--sha``; a pull request somebody merged already is done.

    GitHub applies the rules for ``main`` here (up to date with ``main``, required checks, signed commits, resolved
    conversations) and nobody is exempt: there is no ``--admin``, and ``--auto`` would leave the merge to GitHub at a time
    this script does not see.
    """
    view = _view(args.repo, args.number)
    if view.get("state") == "MERGED":
        print(f"The pull request #{args.number} is merged already.")
        return 0
    if view.get("state") != "OPEN" or view.get("headRefOid") != args.sha:
        found = f"{view.get('state')} at {str(view.get('headRefOid'))[:8]}"
        print(f"ERROR: the pull request #{args.number} is {found}, not open at {args.sha[:8]}", file=sys.stderr)
        return 1
    try:
        run_text(["gh", "pr", "merge", args.number, "-R", args.repo, "--merge", "--match-head-commit", args.sha])
    except FactsError as exc:
        print(f"ERROR: GitHub did not merge the pull request #{args.number}: {str(exc).partition(' failed: ')[2] or exc}", file=sys.stderr)
        return 1
    print(f"Merged the pull request #{args.number} with a merge commit.")
    return 0


def merge_commit(root: str, remote: str, sha: str) -> tuple[str | None, str]:
    """``(commit, problem)``: the commit of ``remote``'s main that has ``sha`` as its second parent, if its tree is ``sha``'s.

    It is looked for on the first-parent line of main, which is where the merge commit of a pull request is. No commit and no
    problem means ``sha`` is not on main, or got there another way (a squash or a rebase gives the commit another hash).
    """
    tracking = f"refs/remotes/{remote}/{BASE}"
    onto = run_text(["git", "-C", root, "merge-base", "--is-ancestor", sha, tracking], ok=(0, 1, 128))
    if onto.returncode != 0:
        return None, ""
    listed = run_text(["git", "-C", root, "rev-list", "--first-parent", "--parents", f"{sha}..{tracking}"]).stdout
    for line in listed.splitlines():
        commit, *parents = line.split()
        if sha not in parents[1:]:
            continue
        trees = run_text(["git", "-C", root, "rev-parse", f"{commit}^{{tree}}", f"{sha}^{{tree}}"]).stdout.split()
        if trees[0] != trees[1]:
            return None, f"the merge commit {commit[:8]} has another tree than the release commit {sha[:8]}: more than it was merged"
        return commit, ""
    return None, ""


def cmd_merge_commit(args: argparse.Namespace) -> int:
    commit, problem = merge_commit(args.root, args.remote, args.sha)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    if commit is None:
        return 4
    print(commit)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, run: Callable[[argparse.Namespace], int], *flags: str) -> argparse.ArgumentParser:
        added = sub.add_parser(name)
        added.set_defaults(run=run)
        for flag in flags:
            added.add_argument(f"--{flag}", required=True)
        return added

    command("find", cmd_find, "repo", "branch", "sha")
    command("create", cmd_create, "repo", "branch", "package", "version", "tag")
    wait = command("wait", cmd_wait, "repo", "number", "sha")
    wait.add_argument("--deadline", type=seconds, required=True)
    wait.add_argument("--interval", type=seconds, default=30.0)
    command("merge", cmd_merge, "repo", "number", "sha")
    merged = command("merge-commit", cmd_merge_commit, "sha")
    merged.add_argument("--root", default=".")
    merged.add_argument("--remote", default="origin")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # both end up as arguments of gh and git: a commit and a pull request number, nothing that could pass for an option
    sha, number = getattr(args, "sha", "0" * 40), getattr(args, "number", "0")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"[0-9]+", number):
        print("ERROR: --sha is 40 hexadecimal digits and --number is a pull request number", file=sys.stderr)
        return 2
    try:
        return int(args.run(args))
    except FactsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
