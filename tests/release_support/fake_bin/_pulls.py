# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The pull request half of the fake ``gh``: ``gh pr list|create|view|merge`` and the check runs of a commit.

The full fake ``gh`` hands every call to ``handle`` before ``_publish.handle``; ``handle`` answers what concerns pull requests
and returns False for the rest. The pull requests are kept in ``prs.json`` of the state directory. A pull request is a
branch of the bare remote (its head, read from the remote every time, so a push to the branch moves it) into ``main``.

``gh pr merge N --merge --match-head-commit SHA`` is the one way to merge, and it makes a real merge commit in the remote
(two parents, the head's tree) the way GitHub does it. The rules for ``main`` are played here: a call with ``--admin``,
``--auto``, ``--squash`` or ``--rebase`` is refused as a failure of the test itself (exit 2, ``gh pr-merge-flag`` in the event
log), and a head that is not up to date with ``main`` is refused as GitHub refuses it (exit 1, ``gh pr-merge-refused``).

``gh api repos/R/commits/SHA/check-runs`` answers one check run for each required check of ``main`` (the contexts of
``publish_ready.main_rules``, by default the six of the repository's ruleset) as the key ``pr_checks`` says: a list of
states, one per call, the last repeating (``green``, ``red``, ``pending``, ``none``: no run at all, ``skipped``). ``pr_checks_raw``
replaces the rows, ``pr_checks_double`` adds a second run of each name that is ``pending`` (the push and the pull request
events).

Other config keys: ``pr_merge_fail`` (a text: ``gh pr merge`` fails with it, once per call listed when a list), ``pr_merge_as``
(``squash``: the merge makes a commit with one parent; ``tree``: the merge commit's tree holds one more file),
``pr_create_fail`` (a text: ``gh pr create`` fails with it). The kinds of ``gh_fail_on`` and ``gh_hooks`` are ``pr-list``,
``pr-create``, ``pr-view``, ``pr-merge`` and ``pr-checks``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import _state

Origin = Callable[..., "subprocess.CompletedProcess[str]"]
STORE = _state.STATE / "prs.json"
URL = f"https://github.example/{_state.GITHUB_REPO}/pull/"
DEFAULT_CONTEXTS = ("lint (py3.14)", "static (3.14)", "trivy", "smoke-test", "analyze (python)", "analyze (actions)")
FORBIDDEN_MERGE_FLAGS = ("--admin", "--auto", "--squash", "--rebase", "-s", "-r")


def _fail(text: str, code: int = 1) -> None:
    print(text, file=sys.stderr)
    sys.exit(code)


def _flag(args: list[str], *names: str) -> str:
    for name in names:
        if name in args:
            return args[args.index(name) + 1]
    return ""


def _load() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else []
    return rows


def _save(rows: list[dict[str, Any]]) -> None:
    STORE.write_text(json.dumps(rows), encoding="utf-8")


def _head_sha(origin: Origin, row: dict[str, Any]) -> str:
    """The tip of the pull request's branch now; the last one it had when the branch is gone."""
    done = origin("rev-parse", "-q", "--verify", f"refs/heads/{row['head']}")
    if done.returncode == 0:
        row["head_sha"] = done.stdout.strip()
    return str(row["head_sha"])


def _row(origin: Origin, row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    full = {
        "number": row["number"],
        "state": row["state"],
        "headRefOid": _head_sha(origin, row),
        "url": f"{URL}{row['number']}",
        "title": row["title"],
        "mergeCommit": {"oid": row["merge"]} if row.get("merge") else None,
    }
    return {key: full[key] for key in fields if key in full}


def _find(rows: list[dict[str, Any]], number: str) -> dict[str, Any]:
    for row in rows:
        if str(row["number"]) == number:
            return row
    _fail(f"GraphQL: Could not resolve to a PullRequest with the number of {number}. (repository.pullRequest)")
    raise AssertionError  # unreachable: _fail exits


def _list(args: list[str], origin: Origin) -> None:
    rows = _load()
    fields = _flag(args, "--json").split(",")
    head, state = _flag(args, "--head"), _flag(args, "--state") or "open"
    shown = [
        _row(origin, row, fields)
        for row in rows
        if row["head"] == head and (state == "all" or row["state"].lower() == state)
    ]
    print(json.dumps(shown))


def _create(args: list[str], origin: Origin) -> None:
    conf = _state.config()
    if conf.get("pr_create_fail"):
        _fail(str(conf["pr_create_fail"]))
    head, base = _flag(args, "--head", "-H"), _flag(args, "--base", "-B")
    rows = _load()
    if origin("rev-parse", "-q", "--verify", f"refs/heads/{head}").returncode != 0:
        _fail(f"pull request create failed: GraphQL: Head sha can't be blank, No commits between {base} and {head}")
    if any(row["head"] == head and row["state"] == "OPEN" for row in rows):
        _fail(f'a pull request for branch "{head}" into branch "{base}" already exists')
    row = {
        "number": len(rows) + 1, "state": "OPEN", "head": head, "base": base, "head_sha": "",
        "title": _flag(args, "--title", "-t"), "body": _flag(args, "--body", "-b"),
    }
    _head_sha(origin, row)
    _save([*rows, row])
    _state.log("gh", f"pr-create {row['number']} {head}")
    print(f"{URL}{row['number']}")


def _view(args: list[str], origin: Origin) -> None:
    row = _find(_load(), args[2])
    _state.log("gh", f"pr-view {row['number']} {row['state']}")
    print(json.dumps(_row(origin, row, _flag(args, "--json").split(","))))


def _tree_of(origin: Origin, row: dict[str, Any]) -> str:
    """The tree of the merge commit: the head's, or with ``pr_merge_as: tree`` one more file."""
    tree = origin("rev-parse", f"{row['head_sha']}^{{tree}}").stdout.strip()
    if _state.config().get("pr_merge_as") != "tree":
        return tree
    blob = subprocess.run(  # noqa: S603 - a program of the test itself
        ["git", "--git-dir", str(_state.STATE / "origin.git"), "hash-object", "-w", "--stdin"],  # noqa: S607
        input="more\n", capture_output=True, text=True, check=True,
    ).stdout.strip()
    listing = origin("ls-tree", tree).stdout + f"100644 blob {blob}\tmore-than-the-release.txt\n"
    made = subprocess.run(  # noqa: S603 - a program of the test itself
        ["git", "--git-dir", str(_state.STATE / "origin.git"), "mktree"],  # noqa: S607
        input=listing, capture_output=True, text=True, check=True,
    )
    return made.stdout.strip()


def _refuse_merge(reason: str) -> None:
    _state.log("gh", "pr-merge-refused")
    _fail(reason)


def _merge_refusal(args: list[str], row: dict[str, Any], origin: Origin) -> None:
    """Refuse the way GitHub does: a failure that the config asks for, a head that moved, or one not up to date with main."""
    fails = _state.config().get("pr_merge_fail")
    text = _state.step("pr_merge_fail", fails if isinstance(fails, list) else [fails]) if fails else ""
    if text:
        _refuse_merge(str(text))
    sha = _head_sha(origin, row)
    if row["state"] != "OPEN" or sha != _flag(args, "--match-head-commit"):
        _refuse_merge("Pull request is not mergeable: the head commit changed or the pull request is closed")
    main = origin("rev-parse", "refs/heads/main").stdout.strip()
    if origin("merge-base", "--is-ancestor", main, sha).returncode != 0:
        _refuse_merge("Pull request is not mergeable: the head branch is not up to date with the base branch")


def _merge(args: list[str], origin: Origin) -> None:
    rows = _load()
    row = _find(rows, args[2])
    if any(flag in args for flag in FORBIDDEN_MERGE_FLAGS) or "--merge" not in args:
        _state.log("gh", "pr-merge-flag")
        _fail(f"fake gh: a release merges with --merge and nothing else, got {args}", 2)
    _merge_refusal(args, row, origin)
    main = origin("rev-parse", "refs/heads/main").stdout.strip()
    sha = str(row["head_sha"])
    parents = ["-p", main] if _state.config().get("pr_merge_as") == "squash" else ["-p", main, "-p", sha]
    made = origin("commit-tree", _tree_of(origin, row), *parents, "-m", f"Merge pull request #{row['number']} from {row['head']}")
    merge = made.stdout.strip()
    origin("update-ref", "refs/heads/main", merge, main)
    row.update(state="MERGED", merge=merge)
    _save(rows)
    _state.log("gh", f"pr-merge {row['number']}")


def _contexts() -> list[str]:
    for rule in _state.config().get("publish_ready", {}).get("main_rules", []):
        if rule.get("type") == "required_status_checks":
            return [check["context"] for check in rule["parameters"]["required_status_checks"]]
    return list(DEFAULT_CONTEXTS)


def _check_rows(state: str) -> list[dict[str, Any]]:
    body = {
        "green": {"status": "completed", "conclusion": "success"},
        "skipped": {"status": "completed", "conclusion": "skipped"},
        "red": {"status": "completed", "conclusion": "failure"},
        "pending": {"status": "in_progress", "conclusion": None},
    }
    if state == "none":
        return []
    rows = [
        {"id": index, "name": name, "html_url": f"https://github.example/check/{index}", **body[state]}
        for index, name in enumerate(_contexts(), start=1)
    ]
    if _state.config().get("pr_checks_double"):
        rows += [{**row, "id": row["id"] + 100, **body["pending"]} for row in rows]
    return rows


def _check_runs(fail_kind: Callable[[str], None]) -> None:
    fail_kind("pr-checks")
    conf = _state.config()
    if "pr_checks_raw" in conf:
        rows = list(conf["pr_checks_raw"])
        state = "raw"
    else:
        state = str(_state.step("pr_checks", conf.get("pr_checks", ["green"])))
        rows = _check_rows(state)
    _state.log("gh", f"pr-checks {state}")
    print(json.dumps({"total_count": len(rows), "check_runs": rows}))


def handle(args: list[str], origin: Origin, fail_kind: Callable[[str], None]) -> bool:
    """Answer the call if it is about a pull request or the check runs of a commit; False when it is not."""
    if args[:1] == ["api"] and args[1].split("?")[0].endswith("/check-runs"):
        _check_runs(fail_kind)
        return True
    if args[:1] != ["pr"] or args[1:2] not in (["list"], ["create"], ["view"], ["merge"]):
        return False
    if _flag(args, "-R", "--repo") != _state.GITHUB_REPO:
        _fail(f"fake gh: expected -R {_state.GITHUB_REPO}, got {_flag(args, '-R', '--repo')!r}", 2)
    fail_kind(f"pr-{args[1]}")
    {"list": _list, "create": _create, "view": _view, "merge": _merge}[args[1]](args, origin)
    return True
