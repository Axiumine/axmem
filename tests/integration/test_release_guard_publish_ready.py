# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""``guard publish-ready``: is the repository set up for the PyPI publication, read from GitHub's settings before a release.

Nothing in ``publish.yml`` can keep another ref from publishing, so the safety is in the settings: the repository is public
and the token can push, the workflow is registered and active, the environment ``pypi`` has a required reviewer, no
administrator bypass and exactly one deployment rule, the tag ``v*.*.*``, and an active tag ruleset on ``v*`` blocks
creation, update, deletion and force pushes. Every reason is tested alone against the fake ``gh`` (default: all of it
holds; ``publish_ready_patch`` changes one part, ``publish_ready`` replaces one), and the answers of the REST API that
cannot be read are exit 2 naming the endpoint, never exit 1.
"""

import pytest

from scripts.release import guard
from tests.integration.release_guard_world import REPO, World

RULES = ("creation", "update", "deletion", "non_fast_forward")
GENERATOR = "slsa-framework/slsa-github-generator"
GENERATOR_REF = f"repos/{GENERATOR}/git/ref/tags/v2.1.0"
ELSEWHERE = "0123456789abcdef0123456789abcdef01234567"
TAG_OBJECT = "89abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


@pytest.fixture
def ready(world, capsys):
    """``ready(patch=..., replace=...)``: scripts the settings, runs ``publish-ready`` and returns ``(code, out, err)``."""

    def run(patch=None, replace=None):
        world.script(publish_ready_patch=patch or {}, publish_ready=replace or {})
        code = int(guard.main(["publish-ready", "--repo", REPO, "--root", str(world.repo)]))
        seen = capsys.readouterr()
        return code, seen.out, seen.err

    return run


def _ruleset(**changes):
    """A tag ruleset that protects the release tags, with ``changes``."""
    return {
        "id": 7,
        "name": "release tags",
        "target": "tag",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        "rules": [{"type": kind} for kind in RULES],
        **changes,
    }


def _listed(ruleset):
    return {key: ruleset[key] for key in ("id", "name", "target", "enforcement") if key in ruleset}


def _with_ruleset(ruleset):
    """The ``publish_ready`` parts that make ``ruleset`` the only ruleset of the repository."""
    return {"rulesets": [_listed(ruleset)], "ruleset_details": {str(ruleset["id"]): ruleset}}


# --- all good -------------------------------------------------------------------------------------


def test_a_repository_that_is_set_up_is_ready_and_reads_only(ready, world):
    code, out, err = ready()

    assert code == 0
    assert out == f"{REPO} is ready to publish: publish.yml, environment pypi, the tag ruleset and the SLSA generator tag hold\n"
    assert err == ""
    paths = [line.split()[-1] for line in world.events() if line.startswith("gh api ")]
    assert paths == [
        f"repos/{REPO}",
        f"repos/{REPO}/actions/workflows/publish.yml",
        f"repos/{REPO}/environments/pypi/deployment-branch-policies",
        f"repos/{REPO}/environments/pypi",
        f"repos/{REPO}/rulesets",
        f"repos/{REPO}/rulesets/7",
        GENERATOR_REF,
        f"repos/{REPO}/rules/branches/main",
    ]


# --- the repository and the workflow --------------------------------------------------------------


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"repo": {"private": True}}, f"{REPO} is not public"),
        ({"repo": {"permissions": {"push": False}}}, f"the gh token has no push right on {REPO}"),
        ({"workflow": {"state": "disabled_manually"}}, "publish.yml is disabled_manually, not active"),
    ],
)
def test_each_reason_of_the_repository_and_the_workflow_alone_refuses(ready, patch, reason):
    code, out, err = ready(patch)

    assert code == 1
    assert out == ""
    assert len(err.splitlines()) == 1
    assert err.startswith(f"ERROR: {reason}")


@pytest.mark.parametrize(
    ("replace", "reason"),
    [
        ({"repo": {"full_name": REPO, "permissions": {"push": True}}}, f"{REPO} is not public"),
        ({"repo": {"full_name": REPO, "private": False}}, f"the gh token has no push right on {REPO}"),
        ({"workflow": {"id": 1}}, "publish.yml is not registered, not active"),
    ],
)
def test_a_missing_answer_is_a_refusal_not_a_pass(ready, replace, reason):
    code, _, err = ready(replace=replace)

    assert code == 1
    assert reason in err


def test_every_reason_is_listed_not_only_the_first(ready):
    patch = {
        "repo": {"private": True},
        "workflow": {"state": "disabled_inactivity"},
        "environment": {"can_admins_bypass": True},
    }

    code, _, err = ready(patch)

    assert code == 1
    assert len(err.splitlines()) == 3
    assert all(line.startswith("ERROR: ") for line in err.splitlines())


# --- the environment ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"environment": {"can_admins_bypass": True}}, "lets administrators bypass its rules"),
        ({"environment": {"protection_rules": []}}, "has no required reviewer"),
        ({"environment": {"protection_rules": [{"type": "wait_timer", "wait_timer": 5}]}}, "has no required reviewer"),
        ({"environment": {"protection_rules": [{"type": "required_reviewers", "reviewers": []}]}}, "has no required reviewer"),
        (
            {"policies": {"branch_policies": [{"name": "v*.*.*", "type": "branch"}]}},
            "exactly one deployment rule, the tag v*.*.* (it has v*.*.* of type branch)",
        ),
        (
            {"policies": {"branch_policies": [{"name": "main", "type": "branch"}, {"name": "v*.*.*", "type": "tag"}]}},
            "(it has main of type branch, v*.*.* of type tag)",
        ),
        (
            {"policies": {"branch_policies": [{"name": "v*", "type": "tag"}]}},
            "(it has v* of type tag)",
        ),
        ({"policies": {"branch_policies": []}}, "(it has none)"),
    ],
)
def test_each_reason_of_the_environment_alone_refuses(ready, patch, reason):
    code, out, err = ready(patch)

    assert code == 1
    assert out == ""
    assert len(err.splitlines()) == 1
    assert reason in err


def test_an_environment_without_a_list_of_rules_has_no_reviewer(ready):
    environment = {"name": "pypi", "can_admins_bypass": False, "protection_rules": None}

    code, _, err = ready(replace={"environment": environment, "policies": {}})

    assert code == 1
    assert "has no required reviewer" in err
    assert "(it has none)" in err


REVIEWERS = [{"type": "required_reviewers", "reviewers": [{"type": "User", "reviewer": {"login": "maintainer", "id": 1}}]}]


def _environment(**changes):
    """An environment that gates the upload, with ``changes``."""
    return {
        "name": "pypi",
        "can_admins_bypass": False,
        "protection_rules": REVIEWERS,
        "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
        **changes,
    }


def test_an_answer_that_does_not_say_whether_administrators_bypass_is_a_refusal_not_a_pass(ready):
    environment = _environment()
    del environment["can_admins_bypass"]

    code, _, err = ready(replace={"environment": environment})

    assert code == 1
    assert "lets administrators bypass its rules" in err


@pytest.mark.parametrize(
    "policy",
    [
        None,
        {"protected_branches": True, "custom_branch_policies": False},
        {"protected_branches": False, "custom_branch_policies": False},
        {"protected_branches": True, "custom_branch_policies": True},
        {},
    ],
)
def test_an_environment_that_is_open_to_every_branch_or_to_the_protected_ones_does_not_gate_by_tag(ready, policy):
    code, out, err = ready(replace={"environment": _environment(deployment_branch_policy=policy)})

    assert code == 1
    assert out == ""
    assert "must deploy from selected branches and tags only (Deployment branches and tags: Selected)" in err
    assert len(err.splitlines()) == 1


# --- the tag ruleset ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ruleset",
    [
        _ruleset(target="branch"),
        _ruleset(enforcement="disabled"),
        _ruleset(enforcement="evaluate"),
        _ruleset(conditions={"ref_name": {"include": ["refs/tags/release-*"], "exclude": []}}),
        _ruleset(conditions={"ref_name": {"include": [], "exclude": []}}),
        _ruleset(conditions={"ref_name": {"include": ["refs/tags/v*"], "exclude": ["refs/tags/v*"]}}),
        _ruleset(conditions={"ref_name": {"include": ["~ALL"], "exclude": ["refs/tags/v*"]}}),
        _ruleset(conditions={"ref_name": {"include": ["~ALL"], "exclude": ["refs/tags/v0.*"]}}),
        _ruleset(conditions={}),
        _ruleset(conditions=None),
        _ruleset(rules=[]),
        _ruleset(rules=None),
    ],
)
def test_a_ruleset_that_does_not_protect_the_release_tags_does_not_count(ready, ruleset):
    code, out, err = ready(replace=_with_ruleset(ruleset))

    assert code == 1
    assert out == ""
    assert "no active tag ruleset covers refs/tags/v* with creation, update, deletion, non_fast_forward" in err


@pytest.mark.parametrize("missing", RULES)
def test_a_ruleset_that_lacks_one_of_the_four_rules_does_not_count(ready, missing):
    ruleset = _ruleset(rules=[{"type": kind} for kind in RULES if kind != missing])

    code, _, err = ready(replace=_with_ruleset(ruleset))

    assert code == 1
    assert len(err.splitlines()) == 1


def test_no_ruleset_at_all_refuses(ready):
    code, _, err = ready(replace={"rulesets": []})

    assert code == 1
    assert "no active tag ruleset" in err


def test_a_listing_that_says_active_is_checked_against_the_ruleset_itself(ready):
    listed = _listed(_ruleset())
    replace = {"rulesets": [listed], "ruleset_details": {"7": _ruleset(enforcement="disabled")}}

    code, _, _ = ready(replace=replace)

    assert code == 1


@pytest.mark.parametrize("include", [["refs/tags/v*"], ["~ALL"], ["refs/tags/*"], ["refs/heads/main", "refs/tags/v*"]])
def test_a_ruleset_that_includes_every_release_tag_counts(ready, include):
    ruleset = _ruleset(conditions={"ref_name": {"include": include, "exclude": ["refs/tags/other"]}})

    code, _, err = ready(replace=_with_ruleset(ruleset))

    assert (code, err) == (0, "")


def test_one_good_ruleset_among_others_is_enough_and_a_stronger_one_may_add_rules(ready):
    weak = _ruleset(id=5, rules=[{"type": "creation"}])
    other = _ruleset(id=6, target="branch")
    strong = _ruleset(id=7, rules=[{"type": kind} for kind in (*RULES, "required_signatures")])
    replace = {
        "rulesets": [_listed(weak), _listed(other), _listed(strong)],
        "ruleset_details": {"5": weak, "6": other, "7": strong},
    }

    assert ready(replace=replace)[0] == 0


# --- main ------------------------------------------------------------------------------------------


def test_a_main_without_rules_is_a_warning_and_still_ready(ready):
    code, out, err = ready(replace={"main_rules": []})

    assert code == 0
    assert out.startswith(f"{REPO} is ready to publish")
    assert err.splitlines() == [
        "WARNING: main has no non_fast_forward rule",
        "WARNING: main has no deletion rule",
        "WARNING: main has no required_status_checks rule",
    ]


def test_a_warning_comes_with_the_errors_when_there_are_any(ready):
    code, _, err = ready(patch={"repo": {"private": True}}, replace={"main_rules": [{"type": "deletion"}]})

    assert code == 1
    assert err.splitlines()[0] == "WARNING: main has no non_fast_forward rule"
    assert err.splitlines()[-1].startswith("ERROR: ")


# --- GitHub cannot be asked ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("part", "endpoint"),
    [
        ("repo", f"repos/{REPO}"),
        ("workflow", f"repos/{REPO}/actions/workflows/publish.yml"),
        ("environment", f"repos/{REPO}/environments/pypi"),
        ("policies", f"repos/{REPO}/environments/pypi/deployment-branch-policies"),
        ("rulesets", f"repos/{REPO}/rulesets"),
        ("main_rules", f"repos/{REPO}/rules/branches/main"),
    ],
)
@pytest.mark.parametrize("http", [403, 404])
def test_an_answer_that_is_an_http_error_is_exit_two_and_names_the_endpoint(ready, part, endpoint, http):
    code, out, err = ready(replace={part: {"__http": http}})

    assert code == 2
    assert out == ""
    assert f"ERROR: gh api {endpoint}: " in err
    assert f"(HTTP {http})" in err


def test_a_ruleset_that_cannot_be_read_is_exit_two_naming_its_endpoint(ready):
    replace = {"rulesets": [_listed(_ruleset())], "ruleset_details": {}}

    code, _, err = ready(replace=replace)

    assert code == 2
    assert f"ERROR: gh api repos/{REPO}/rulesets/7: " in err


def test_a_gh_that_fails_is_exit_two(ready, world):
    world.script(fail="gh: unexpected failure")
    code = int(guard.main(["publish-ready", "--repo", REPO, "--root", str(world.repo)]))

    assert code == 2


# --- who may bypass the tag ruleset ----------------------------------------------------------------

ADMIN = {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}


@pytest.mark.parametrize(
    "actors",
    [
        [],
        None,
        [ADMIN],
        [{"actor_id": None, "actor_type": "OrganizationAdmin", "bypass_mode": "always"}],
        [ADMIN, {"actor_id": None, "actor_type": "OrganizationAdmin", "bypass_mode": "pull_request"}],
    ],
)
def test_a_ruleset_that_only_admins_may_bypass_counts(ready, actors):
    code, _, err = ready(replace=_with_ruleset(_ruleset(bypass_actors=actors)))

    assert (code, err) == (0, "")


@pytest.mark.parametrize(
    "actor",
    [
        {"actor_id": 4, "actor_type": "RepositoryRole", "bypass_mode": "always"},  # Write
        {"actor_id": 2, "actor_type": "RepositoryRole", "bypass_mode": "always"},  # Triage
        {"actor_id": 8, "actor_type": "RepositoryRole", "bypass_mode": "always"},  # Maintain
        {"actor_id": 5, "actor_type": "Team", "bypass_mode": "always"},  # the number of the Admin role, not the role
        {"actor_id": 123, "actor_type": "Integration", "bypass_mode": "always"},
        {"actor_id": 9, "actor_type": "DeployKey", "bypass_mode": "always"},
        {},
    ],
)
def test_a_ruleset_that_lets_anybody_but_admins_bypass_does_not_count(ready, actor):
    code, _, err = ready(replace=_with_ruleset(_ruleset(bypass_actors=[ADMIN, actor])))

    assert code == 1
    assert (
        "no active tag ruleset covers refs/tags/v* with creation, update, deletion, non_fast_forward and lets only repository "
        "admins bypass it"
    ) in err
