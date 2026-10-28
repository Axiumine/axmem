"""The guard command ``testpypi-ready`` of the TestPyPI rehearsal.

It is not a second implementation: ``testpypi-ready`` is ``publish-ready`` with the key ``testpypi`` of ``facts.INDEXES``:
every check of the repository, the environment (reviewers, no admin bypass, Selected, the one tag rule ``v*.*.*``), the tag
ruleset and the SLSA generator tag, for ``publish-testpypi.yml`` and the environment ``testpypi``. These cases pin the
TestPyPI words and routes literally, and prove that the two environments are held to the same rules with the same words and
independently of each other. ``test_release_testpypi_state.py`` holds ``testpypi-state``, ``-urls`` and ``-missing``.
"""

import hashlib

import pytest

from scripts.release import guard
from tests.integration.release_guard_world import REPO, World

GENERATOR_REF = "repos/slsa-framework/slsa-github-generator/git/ref/tags/v2.1.0"
ELSEWHERE = "0123456789abcdef0123456789abcdef01234567"
WHEEL, SDIST = "axmem-0.1.1-py3-none-any.whl", "axmem-0.1.1.tar.gz"
OLD_WHEEL, OLD_SDIST = "axmem-0.1.0-py3-none-any.whl", "axmem-0.1.0.tar.gz"
CONTENT = {WHEEL: b"the wheel", SDIST: b"the sdist"}
NEW = {name: hashlib.sha256(data).hexdigest() for name, data in CONTENT.items()}
OLD = {name: hashlib.sha256(name.encode()).hexdigest() for name in (OLD_WHEEL, OLD_SDIST)}
OTHER = hashlib.sha256(b"other bytes").hexdigest()
TESTPYPI_FILES = "test-files.pythonhosted.org"


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


# --- testpypi-ready -------------------------------------------------------------------------------


@pytest.fixture
def ready(world, capsys):
    """``ready(command, patch=..., replace=...)``: scripts the settings, runs ``<command> --repo`` and returns ``(code, out, err)``."""

    def run(command="testpypi-ready", patch=None, replace=None):
        world.script(publish_ready_patch=patch or {}, publish_ready=replace or {})
        code = int(guard.main([command, "--repo", REPO, "--root", str(world.repo)]))
        seen = capsys.readouterr()
        return code, seen.out, seen.err

    return run


def test_a_repository_that_is_set_up_is_ready_for_the_rehearsal_and_the_calls_are_read_only_and_in_order(ready, world):
    code, out, err = ready()

    assert code == 0
    assert out == f"{REPO} is ready to publish: publish-testpypi.yml, environment testpypi, the tag ruleset and the SLSA generator tag hold\n"
    assert err == ""
    paths = [line.split()[-1] for line in world.events() if line.startswith("gh api ")]
    assert paths == [
        f"repos/{REPO}",
        f"repos/{REPO}/actions/workflows/publish-testpypi.yml",
        f"repos/{REPO}/environments/testpypi/deployment-branch-policies",
        f"repos/{REPO}/environments/testpypi",
        f"repos/{REPO}/rulesets",
        f"repos/{REPO}/rulesets/7",
        GENERATOR_REF,
        f"repos/{REPO}/rules/branches/main",
    ]


def test_the_ready_check_of_pypi_does_not_read_a_thing_of_the_rehearsal(ready, world):
    code, out, _ = ready("publish-ready")

    assert code == 0
    assert out.startswith(f"{REPO} is ready to publish: publish.yml, environment pypi, ")
    paths = [line.split()[-1] for line in world.events() if line.startswith("gh api ")]
    assert not [path for path in paths if "testpypi" in path]


#: every reason of ``publish-ready`` that concerns the environment, for the environment ``testpypi`` and its workflow
ENVIRONMENT_REASONS = [
    ({"testpypi_environment": {"can_admins_bypass": True}}, "environment testpypi lets administrators bypass its rules"),
    ({"testpypi_environment": {"protection_rules": []}}, "environment testpypi has no required reviewer"),
    (
        {"testpypi_environment": {"protection_rules": [{"type": "required_reviewers", "reviewers": []}]}},
        "environment testpypi has no required reviewer",
    ),
    (
        {"testpypi_environment": {"deployment_branch_policy": {"protected_branches": True, "custom_branch_policies": False}}},
        "environment testpypi must deploy from selected branches and tags only (Deployment branches and tags: Selected)",
    ),
    (
        {"testpypi_policies": {"branch_policies": [{"name": "v*.*.*", "type": "branch"}]}},
        "environment testpypi must have exactly one deployment rule, the tag v*.*.* (it has v*.*.* of type branch)",
    ),
    (
        {"testpypi_policies": {"branch_policies": [{"name": "main", "type": "branch"}, {"name": "v*.*.*", "type": "tag"}]}},
        "(it has main of type branch, v*.*.* of type tag)",
    ),
    ({"testpypi_policies": {"branch_policies": []}}, "(it has none)"),
    ({"testpypi_workflow": {"state": "disabled_manually"}}, "publish-testpypi.yml is disabled_manually, not active: merge it to the default branch first"),
]


@pytest.mark.parametrize(("patch", "reason"), ENVIRONMENT_REASONS)
def test_each_reason_of_the_rehearsal_environment_and_workflow_alone_refuses(ready, patch, reason):
    code, out, err = ready(patch=patch)

    assert code == 1
    assert out == ""
    assert len(err.splitlines()) == 1
    assert reason in err


@pytest.mark.parametrize(("patch", "reason"), ENVIRONMENT_REASONS)
def test_a_reason_that_concerns_the_environment_of_the_rehearsal_alone_does_not_stop_the_release_to_pypi(ready, patch, reason):
    code, out, err = ready("publish-ready", patch=patch)

    assert (code, err) == (0, "")
    assert out.startswith(f"{REPO} is ready to publish: publish.yml, environment pypi")


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"environment": {"can_admins_bypass": True}}, "environment pypi lets administrators bypass its rules"),
        ({"policies": {"branch_policies": []}}, "environment pypi must have exactly one deployment rule"),
        ({"workflow": {"state": "disabled_manually"}}, "publish.yml is disabled_manually, not active"),
    ],
)
def test_a_reason_of_the_pypi_environment_does_not_count_for_the_rehearsal_and_the_other_way_round(ready, patch, reason):
    assert ready("testpypi-ready", patch=patch)[0] == 0
    code, _, err = ready("publish-ready", patch=patch)
    assert code == 1
    assert reason in err


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"repo": {"private": True}}, f"{REPO} is not public"),
        ({"repo": {"permissions": {"push": False}}}, f"the gh token has no push right on {REPO}"),
    ],
)
def test_the_repository_checks_are_those_of_publish_ready(ready, patch, reason):
    code, _, err = ready(patch=patch)

    assert code == 1
    assert f"ERROR: {reason}" in err


@pytest.mark.parametrize(
    "replace",
    [
        {"rulesets": []},
        {"rulesets": [{"id": 7, "target": "tag", "enforcement": "disabled"}], "ruleset_details": {"7": {"id": 7, "target": "tag", "enforcement": "disabled"}}},
    ],
)
def test_the_tag_ruleset_is_required_for_the_rehearsal_as_for_the_release(ready, replace):
    code, _, err = ready(replace=replace)

    assert code == 1
    assert "no active tag ruleset covers refs/tags/v* with creation, update, deletion, non_fast_forward and lets only repository admins bypass it" in err


def test_a_ruleset_that_lets_the_write_role_bypass_it_refuses_the_rehearsal_too(ready):
    ruleset = {
        "id": 7,
        "name": "release tags",
        "target": "tag",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        "rules": [{"type": kind} for kind in ("creation", "update", "deletion", "non_fast_forward")],
        "bypass_actors": [{"actor_id": 4, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
    }
    listed = {key: ruleset[key] for key in ("id", "name", "target", "enforcement")}

    code, _, err = ready(replace={"rulesets": [listed], "ruleset_details": {"7": ruleset}})

    assert code == 1
    assert "lets only repository admins bypass it" in err


def test_a_generator_tag_that_was_moved_refuses_the_rehearsal_too(ready):
    code, _, err = ready(replace={"slsa_ref": {"object": {"type": "commit", "sha": ELSEWHERE}}})

    assert code == 1
    assert f"points to {ELSEWHERE}" in err


def test_every_reason_is_listed_not_only_the_first(ready):
    patch = {
        "repo": {"private": True},
        "testpypi_workflow": {"state": "disabled_inactivity"},
        "testpypi_environment": {"can_admins_bypass": True},
    }

    code, _, err = ready(patch=patch)

    assert code == 1
    assert [line.split(":")[0] for line in err.splitlines()] == ["ERROR"] * 3


def test_a_main_without_rules_is_a_warning_and_still_ready(ready):
    code, out, err = ready(replace={"main_rules": []})

    assert code == 0
    assert "environment testpypi" in out
    assert err.splitlines() == [
        "WARNING: main has no non_fast_forward rule",
        "WARNING: main has no deletion rule",
        "WARNING: main has no required_status_checks rule",
    ]


@pytest.mark.parametrize(
    ("part", "endpoint"),
    [
        ("testpypi_workflow", f"repos/{REPO}/actions/workflows/publish-testpypi.yml"),
        ("testpypi_environment", f"repos/{REPO}/environments/testpypi"),
        ("testpypi_policies", f"repos/{REPO}/environments/testpypi/deployment-branch-policies"),
    ],
)
@pytest.mark.parametrize("http", [403, 404])
def test_an_environment_that_cannot_be_read_is_exit_two_and_names_the_endpoint(ready, part, endpoint, http):
    code, out, err = ready(replace={part: {"__http": http}})

    assert code == 2
    assert out == ""
    assert f"ERROR: gh api {endpoint}: " in err
    assert f"(HTTP {http})" in err


def test_an_environment_that_is_missing_altogether_is_a_refusal_not_a_pass(ready):
    code, _, err = ready(replace={"testpypi_environment": {"name": "testpypi"}, "testpypi_policies": {}})

    assert code == 1
    assert "environment testpypi lets administrators bypass its rules" in err
    assert "environment testpypi has no required reviewer" in err
    assert "(it has none)" in err


PARITY_PATCHES = [
    ({"can_admins_bypass": True}, {}),
    ({"protection_rules": []}, {}),
    ({"deployment_branch_policy": None}, {}),
    ({}, {"branch_policies": []}),
    ({}, {"branch_policies": [{"name": "v*", "type": "tag"}]}),
    ({"can_admins_bypass": True, "protection_rules": []}, {"branch_policies": [{"name": "main", "type": "branch"}]}),
]


@pytest.mark.parametrize(("environment", "policies"), PARITY_PATCHES)
def test_the_two_environments_are_held_to_the_same_rules_with_the_same_words(ready, environment, policies):
    pypi = ready("publish-ready", patch={"environment": environment, "policies": policies})
    rehearsal = ready("testpypi-ready", patch={"testpypi_environment": environment, "testpypi_policies": policies})

    def plain(text):
        return text.replace("publish-testpypi.yml", "publish.yml").replace("testpypi", "pypi")

    assert rehearsal[0] == pypi[0] == 1
    assert plain(rehearsal[1]) == pypi[1]
    assert plain(rehearsal[2]) == pypi[2]
