"""The publishing half of the fake ``gh``: the ``publish.yml`` runs and the settings that make a repository publish-ready.

The full fake ``gh`` (``fake_bin/gh``) and the light one of ``tests/release_support/fakes.py`` both hand every call to
``handle`` first; ``handle`` answers what concerns ``publish.yml`` and the GitHub settings and returns False for the rest.

``gh workflow run publish.yml --ref T -f tag=T`` is the one way a run starts. It is refused (exit 1, ``gh workflow-run-refused``
in the event log) for another workflow, for a ``--ref`` that is not ``T``, for a missing or extra input, and for a tag the
remote does not have; otherwise it logs ``gh workflow-run publish.yml tag=T`` and creates a run of ``T`` at the commit of ``T``.
The run is played from the config key ``publish_runs``: a list of states, one per look at the run, the last one repeating
(``queued``, ``waiting``, ``in_progress``, then ``success``, ``failure`` or ``cancelled``; default
``["queued", "in_progress", "success"]``), or a list of such lists, one per dispatch. A look is a ``run view`` (``run list``
shows the current state without moving on). The run is first reported completed with ``success``: the artefacts of the release
of ``T`` are copied into the fake PyPI as ``publish_effect`` says (``all``, ``none``, ``partial``: the first file only,
``wrong_bytes``: every file with ``tampered`` appended) and ``published`` records their names for the fake ``curl``.

Other config keys: ``publish_existing`` (runs of the tag that exist before the script dispatches anything:
``{"state": "in_progress"|"success"|..., "age_s": 600}`` or ``{"script": [...]}``, and ``"row": {...}`` to change fields of
how that run reads; their sha and tag are those of the first query; the ids are 1000, 1001, ... and a dispatched run
takes the next one), ``publish_hidden_lists`` (the number of ``run list`` calls before a dispatched run shows in the list: 10**6 is never),
``publish_row`` (fields that replace the same fields of every row, to break the checks on ``headSha``, ``headBranch``,
``event``), ``publish_successor`` (a script: when a run is first reported ``cancelled`` a newer run of the same tag and commit
starts with it), ``publish_rerun`` (the script of a run after ``gh run rerun ID``, default ``["success"]``), ``run_log`` (the
text of ``gh run view ID --log-failed``, a string or a list of lines).

``gh api`` answers the settings from ``publish_ready``, a dict with the parts ``repo``, ``workflow``, ``environment``,
``policies``, ``rulesets`` (the list), ``ruleset_details`` (id as text to the body) and ``main_rules``. A part that is given
replaces the default (everything is ready); ``publish_ready_patch`` merges its dicts into the default part instead (one
reason at a time: ``{"environment": {"can_admins_bypass": true}}``). A part that is ``{"__http": 404}`` answers the error of
the endpoint (exit 1, ``gh: ... (HTTP 404)``). ``gh_fail_on`` and ``gh_hooks`` know the kinds ``publish-runs``,
``publish-dispatch``, ``publish-view``, ``publish-log``, ``publish-rerun`` and ``api``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import _state

WORKFLOW = "publish.yml"
RUNS = _state.STATE / "publish-runs.json"
DONE = ("success", "failure", "cancelled")
DEFAULT_SCRIPT = ["queued", "in_progress", "success"]
Origin = Callable[..., "subprocess.CompletedProcess[str]"]


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _flag(args: list[str], *flags: str) -> str:
    """The value after the first of ``flags`` found in ``args``."""
    for flag in flags:
        if flag in args:
            return args[args.index(flag) + 1]
    return ""


def _fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    sys.exit(code)


def _scripts(conf: dict[str, Any]) -> list[list[str]]:
    """``publish_runs`` as one script per dispatch."""
    planted = conf.get("publish_runs", DEFAULT_SCRIPT)
    return planted if planted and isinstance(planted[0], list) else [planted]


# --- the runs ---------------------------------------------------------------------------------------------------------


def _run(script: list[str], tag: str, sha: str, state: dict[str, Any], age_s: float = 0, **extra: Any) -> dict[str, Any]:
    """A new run in ``state``, created ``age_s`` seconds ago, with the next id."""
    state["next_id"] += 1
    created = _stamp(datetime.now(UTC) - timedelta(seconds=age_s))
    run = {
        "id": state["next_id"] - 1,
        "tag": tag,
        "sha": sha,
        "script": script,
        "seen": 0,
        "created": created,
        "updated": created if script[-1] in DONE else None,
        "effect": True,
        "effected": False,
        "hide": 0,
        **extra,
    }
    state["runs"].append(run)
    return run


def _load(tag: str | None = None, sha: str | None = None) -> dict[str, Any]:
    """The runs; the first call that knows a tag and a commit also makes the ``publish_existing`` runs of the scenario."""
    if RUNS.exists():
        loaded: dict[str, Any] = json.loads(RUNS.read_text(encoding="utf-8"))
        return loaded
    state: dict[str, Any] = {"next_id": 1000, "runs": [], "lists": 0, "dispatches": 0}
    if tag and sha:
        for row in _state.config().get("publish_existing", []):
            script = list(row.get("script") or [row.get("state", "success")])
            _run(script, tag, sha, state, row.get("age_s", 0), effect=False, effected=True, row=row.get("row", {}))
    _save(state)
    return state


def _save(state: dict[str, Any]) -> None:
    RUNS.write_text(json.dumps(state), encoding="utf-8")


def _current(run: dict[str, Any]) -> str:
    return str(run["script"][min(run["seen"], len(run["script"]) - 1)])


def _apply_effect(run: dict[str, Any], artefacts: Callable[[str], dict[str, bytes]]) -> None:
    """A run that succeeded put the release's files on the fake PyPI, as ``publish_effect`` says."""
    mode = _state.config().get("publish_effect", "all")
    files = artefacts(run["tag"]) if mode != "none" else {}
    names = sorted(files)[:1] if mode == "partial" else sorted(files)
    _state.PYPI_DIR.mkdir(exist_ok=True)
    for name in names:
        (_state.PYPI_DIR / name).write_bytes(files[name] + (b"tampered" if mode == "wrong_bytes" else b""))
    if names:
        (_state.STATE / "published").write_text(json.dumps(names), encoding="utf-8")


def _settle(state: dict[str, Any], run: dict[str, Any], artefacts: Callable[[str], dict[str, bytes]]) -> str:
    """What looking at ``run`` now causes, once: the stamp of its end, the files of a success, the run that replaces a cancelled one."""
    current = _current(run)
    if current in DONE and run["updated"] is None:
        run["updated"] = _stamp(datetime.now(UTC))
    if current == "success" and run["effect"] and not run["effected"]:
        run["effected"] = True
        _apply_effect(run, artefacts)
    successor = _state.config().get("publish_successor")
    if current == "cancelled" and successor and not run.get("replaced"):
        run["replaced"] = True
        _run(list(successor), run["tag"], run["sha"], state)
    return current


def _report(state: dict[str, Any], run: dict[str, Any], artefacts: Callable[[str], dict[str, bytes]]) -> dict[str, Any]:
    """The row GitHub shows for ``run`` now; the first time it shows a success the files appear on the fake PyPI."""
    current = _settle(state, run, artefacts)
    done = current in DONE
    now = _stamp(datetime.now(UTC))
    row = {
        "databaseId": run["id"],
        "workflowName": "Publish to PyPI",
        "name": "Publish to PyPI",
        "status": "completed" if done else current,
        "conclusion": current if done else "",
        "event": "workflow_dispatch",
        "headSha": run["sha"],
        "headBranch": run["tag"],
        "createdAt": run["created"],
        "updatedAt": run["updated"] if done else now,
        "url": f"https://github.example/run/publish-{run['id']}",
    }
    return {**row, **_state.config().get("publish_row", {}), **run.get("row", {})}


def _list_runs(args: list[str], artefacts: Callable[[str], dict[str, bytes]]) -> None:
    """``gh run list --workflow publish.yml``: the runs the filters (``--branch``, ``--commit``, ``--event``) let through."""
    state = _load(_flag(args, "--branch"), _flag(args, "--commit"))
    shown = []
    for run in list(state["runs"]):
        if run["hide"] > 0:
            run["hide"] -= 1
            continue
        # GitHub filters on what the run is; a row planted with ``row`` or ``publish_row`` only reads differently
        stored = (run["tag"], run["sha"], "workflow_dispatch")
        asked = (_flag(args, "--branch"), _flag(args, "--commit"), _flag(args, "--event"))
        row = _report(state, run, artefacts)
        if all(want in ("", have) for want, have in zip(asked, stored, strict=True)):
            shown.append(row)
    _save(state)
    fields = _flag(args, "--json").split(",")
    print(json.dumps([{key: row[key] for key in fields if key in row} for row in shown]))


def _view_run(args: list[str], artefacts: Callable[[str], dict[str, bytes]], fail_kind: Callable[[str], None]) -> None:
    """``gh run view ID --json ...`` (one look at the run: the next state of its script) or ``--log-failed``."""
    state = _load()
    found = [run for run in state["runs"] if str(run["id"]) == args[2]]
    if not found:
        _fail(f"HTTP 404: Not Found (https://api.github.com/repos/x/actions/runs/{args[2]})")
    run = found[0]
    if "--log-failed" in args:
        fail_kind("publish-log")
        _state.log("gh", f"run-log {run['id']}")
        text = _state.config().get("run_log", "")
        print("\n".join(text) if isinstance(text, list) else text)
        return
    fail_kind("publish-view")
    row = _report(state, run, artefacts)
    run["seen"] += 1
    _save(state)
    fields = _flag(args, "--json").split(",")
    print(json.dumps({key: row[key] for key in fields if key in row}))


def _refusal(args: list[str], origin: Origin) -> str:
    """Why ``gh workflow run`` would refuse the call (an empty string when it would not)."""
    inputs = [args[i + 1] for i, word in enumerate(args) if word in ("-f", "--raw-field") and i + 1 < len(args)]
    ref = _flag(args, "--ref")
    tag = next((item.removeprefix("tag=") for item in inputs if item.startswith("tag=")), "")
    known = origin("rev-parse", "-q", "--verify", f"refs/tags/{tag}").returncode == 0
    checks = [
        (args[2:3] != [WORKFLOW], f"could not find any workflows named {args[2:3]}"),
        (not tag, "required input 'tag' not provided"),
        (ref != tag, f"--ref {ref!r} is not the tag {tag!r}: dispatch on the tag itself"),
        (len(inputs) != 1, f"unexpected inputs {inputs}"),
        (not known, f"HTTP 422: No ref found for: {tag}"),
    ]
    return next((message for refused, message in checks if refused), "")


def _dispatch(args: list[str], origin: Origin, fail_kind: Callable[[str], None]) -> None:
    """``gh workflow run publish.yml --ref T -f tag=T``: refused unless it is exactly that."""
    fail_kind("publish-dispatch")
    reason = _refusal(args, origin)
    if reason:
        _state.log("gh", f"workflow-run-refused {reason}")
        _fail(f"fake gh: {reason}")
    tag = _flag(args, "--ref")
    sha = origin("rev-parse", f"refs/tags/{tag}^{{commit}}").stdout.strip()
    _state.log("gh", f"workflow-run {WORKFLOW} tag={tag}")
    state = _load(tag, sha)
    scripts = _scripts(_state.config())
    run = _run(list(scripts[min(state["dispatches"], len(scripts) - 1)]), tag, sha, state)
    run["hide"] = int(_state.config().get("publish_hidden_lists", 0))
    state["dispatches"] += 1
    _save(state)
    print(f"Created workflow_dispatch event for {WORKFLOW} at {tag}")


def _rerun(args: list[str], fail_kind: Callable[[str], None]) -> None:
    """``gh run rerun ID``: a publish run is played again from ``publish_rerun``; any other id is the signing run's."""
    fail_kind("publish-rerun")
    _state.log("gh", f"run-rerun {args[2]}")
    state = _load()
    for run in state["runs"]:
        if str(run["id"]) == args[2]:
            script = list(_state.config().get("publish_rerun", ["success"]))
            run.update({"script": script, "seen": 0, "updated": None, "effect": True, "effected": False})
            _save(state)
            return
    (_state.STATE / "rerun").write_text(args[2], encoding="utf-8")


# --- the settings -----------------------------------------------------------------------------------------------------


def _ready(repo: str) -> dict[str, Any]:
    """What GitHub answers for a repository that is ready to publish (the shapes of the REST API)."""
    ruleset = {
        "id": 7,
        "name": "release tags",
        "target": "tag",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        "rules": [{"type": kind} for kind in ("creation", "update", "deletion", "non_fast_forward")],
        "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
    }
    return {
        "repo": {"full_name": repo, "private": False, "permissions": {"admin": True, "push": True, "pull": True}},
        "workflow": {"id": 1, "name": "Publish to PyPI", "path": ".github/workflows/publish.yml", "state": "active"},
        "environment": {
            "name": "pypi",
            "can_admins_bypass": False,
            "protection_rules": [
                {"id": 1, "type": "required_reviewers", "prevent_self_review": False,
                 "reviewers": [{"type": "User", "reviewer": {"login": "maintainer", "id": 1}}]},
            ],
            "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
        },
        "policies": {"total_count": 1, "branch_policies": [{"id": 1, "name": "v*.*.*", "type": "tag"}]},
        "rulesets": [{"id": 7, "name": "release tags", "target": "tag", "enforcement": "active"}],
        "ruleset_details": {"7": ruleset},
        "main_rules": [{"type": kind} for kind in ("non_fast_forward", "deletion", "required_status_checks")],
    }


def _merge(base: Any, patch: Any) -> Any:
    """``patch`` over ``base``: dicts merge key by key, anything else is replaced."""
    if isinstance(base, dict) and isinstance(patch, dict):
        return {**base, **{key: _merge(base.get(key), value) for key, value in patch.items()}}
    return patch


def _part(repo: str, name: str) -> Any:
    """The answer for one part of the settings: the scenario's own, else the default with the patch merged into it."""
    conf = _state.config()
    if name in conf.get("publish_ready", {}):
        return conf["publish_ready"][name]
    default = _ready(repo)[name]
    patch = conf.get("publish_ready_patch", {})
    return _merge(default, patch[name]) if name in patch else default


ENDPOINTS = {
    "": "repo",
    "actions/workflows/publish.yml": "workflow",
    "environments/pypi": "environment",
    "environments/pypi/deployment-branch-policies": "policies",
    "rulesets": "rulesets",
    "rules/branches/main": "main_rules",
}


def _http_error(path: str, code: int = 404) -> None:
    _fail(f"gh: {'Forbidden' if code == 403 else 'Not Found'} (HTTP {code}) {path}")


def _target(args: list[str], repo: str | None) -> tuple[str, str, str]:
    """The repository, the part of the settings and the ruleset id that ``gh api <path>`` asks for."""
    pieces = args[1].split("?")[0].split("/")
    named = "/".join(pieces[1:3])
    if pieces[0] != "repos" or len(pieces) < 3 or (repo and named != repo):
        _fail(f"fake gh: unexpected api call {args}", 2)
    ruleset = pieces[4] if len(pieces) == 5 and pieces[3] == "rulesets" else ""
    part = "ruleset_details" if ruleset else ENDPOINTS.get("/".join(pieces[3:]), "")
    return named, part, ruleset


def _api(args: list[str], repo: str | None, fail_kind: Callable[[str], None]) -> None:
    """``gh api repos/R...``: the settings of the repository as ``publish_ready`` says."""
    fail_kind("api")
    path = args[1].split("?")[0]
    named, part, ruleset = _target(args, repo)
    _state.log("gh", f"api {path}")
    body = _part(named, part) if part else None
    if isinstance(body, dict) and "__http" in body:
        _http_error(path, int(body["__http"]))
    if body is None or (ruleset and ruleset not in body):
        _http_error(path)
    print(json.dumps(body[ruleset] if ruleset else body))


def handle(
    args: list[str],
    origin: Origin,
    fail_kind: Callable[[str], None],
    artefacts: Callable[[str], dict[str, bytes]],
    repo: str | None = None,
) -> bool:
    """Answer the call if it is about ``publish.yml``, a run of it, or the settings; False when it is not.

    ``origin`` runs git in the remote's bare repository, ``fail_kind`` plays the ``gh_fail_on`` and ``gh_hooks`` of a
    kind of call, ``artefacts`` gives the files (name to bytes) of the release of a tag, and ``repo`` is the GitHub
    repository every call must name (``-R`` or the path of ``api``), None for no check.
    """
    kind = args[:2]
    if args[:1] == ["api"]:
        _api(args, repo, fail_kind)
        return True
    if kind == ["run", "list"] and _flag(args, "--workflow") != WORKFLOW:
        return False
    if kind not in (["workflow", "run"], ["run", "list"], ["run", "view"], ["run", "rerun"]):
        return False
    if repo and _flag(args, "-R", "--repo") != repo:
        _fail(f"fake gh: expected -R {repo}, got {_flag(args, '-R', '--repo')!r}", 2)
    if kind == ["workflow", "run"]:
        _dispatch(args, origin, fail_kind)
    elif kind == ["run", "rerun"]:
        _rerun(args, fail_kind)
    elif kind == ["run", "list"]:
        fail_kind("publish-runs")
        _list_runs(args, artefacts)
    else:
        _view_run(args, artefacts, fail_kind)
    return True
