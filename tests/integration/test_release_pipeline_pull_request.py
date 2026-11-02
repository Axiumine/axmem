# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""How the release commit reaches ``main``: a branch, a pull request, the required checks, a merge commit.

The rules for ``main`` refuse every direct push, the maintainer's and the Admin role's included, so
``scripts/release-pypi.sh`` pushes the release commit to ``release/<version>`` (the ``pre-push`` gate runs on that push),
opens a pull request, waits until the checks the rules require are green on its head, merges it with a merge commit and
makes the tag on that merge commit. Every case runs the real script against the fake ``gh`` of
``tests/release_support/fake_bin/_pulls.py``, which plays the rules (a merge by anything but ``--merge``, or of a head that
is not up to date, is refused) and makes a real merge commit in the bare remote. The cases that stop run the script again to
prove that it continues at the first step that is not done: no second commit, no second push, no second pull request and no
second merge. The judgment of a check run is in ``test_release_pull_request_checks.py``.
"""

import json
import shutil
from pathlib import Path

import pytest

from tests.release_support.world import GITHUB_REPO, PACKAGE, World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
TAG = f"v{VERSION}"
BRANCH = f"release/{VERSION}"
DISPATCH = f"gh workflow-run publish.yml tag={TAG}"
RESUME = f"release-pypi.sh {VERSION}"
PUSH_OF_BRANCH = f"hook pre-push refs/heads/{BRANCH}"
TITLE = f"chore(release): release {PACKAGE} {VERSION} to PyPI"
#: what a pull request opened by a script must not carry: an attribution line of a tool or of an assistant
ATTRIBUTION = ("Generated with", "Co-Authored-By", "Claude")
SHORT = {"RELEASE_WAIT_TIMEOUT_S": "8", "RELEASE_POLL_INTERVAL_S": "0.2"}


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


def _count(world: World, prefix: str) -> int:
    return len([line for line in world.events() if line.startswith(prefix)])


def _first(world: World, prefix: str) -> int:
    return next(index for index, line in enumerate(world.events()) if line.startswith(prefix))


def _states(world: World) -> list[str]:
    return [row["state"] for row in world.pull_requests()]


class TestTheReleaseCommitTravelsInAPullRequest:
    def test_the_pull_request_is_the_scripts_own_and_carries_no_attribution(self, world: World) -> None:
        assert world.release(VERSION).returncode == 0
        (request,) = world.pull_requests()
        assert (request["head"], request["base"], request["title"]) == (BRANCH, "main", TITLE)
        assert TAG in request["body"]
        assert [word for word in ATTRIBUTION if word in request["title"] + request["body"]] == []
        assert request["head_sha"] == world.origin_git("rev-parse", f"{world.head()}^2")

    def test_the_branch_is_pushed_then_the_pull_request_opened_then_merged_after_the_checks(
        self, world: World,
    ) -> None:
        world.configure(pr_checks=["pending", "green"])
        assert world.release(VERSION).returncode == 0
        opened, merged = _first(world, "gh pr-create"), _first(world, "gh pr-merge ")
        assert _first(world, PUSH_OF_BRANCH) < opened < _first(world, "gh pr-checks pending") < merged
        assert _first(world, "gh pr-checks green") < merged
        assert [line for line in world.events() if line.startswith("gh pr-checks")][-2:] == ["gh pr-checks green"] * 2, (
            "green has to hold on two polls running before the merge"
        )
        assert _count(world, "gh pr-merge ") == 1

    def test_the_merge_uses_the_merge_commit_method_and_no_way_around_the_rules(self, world: World) -> None:
        assert world.release(VERSION).returncode == 0
        assert _count(world, "gh pr-merge-flag") == 0
        assert _count(world, "gh pr-merge-refused") == 0
        assert _states(world) == ["MERGED"]

    def test_nothing_is_pushed_to_main_by_the_script(self, world: World) -> None:
        base = world.state_base()
        assert world.release(VERSION).returncode == 0
        pushes = [line for line in world.events() if line.startswith("hook pre-push ")]
        assert pushes == [PUSH_OF_BRANCH, f"hook pre-push refs/tags/{TAG}"]
        assert world.origin_git("rev-parse", "main^1") == base, "main moved by the merge commit alone"

    def test_a_check_that_was_skipped_counts_as_passed(self, world: World) -> None:
        world.configure(pr_checks=["skipped"])
        done = world.release(VERSION)
        assert done.returncode == 0, _out(done)
        assert _states(world) == ["MERGED"]


class TestTheChecksDecideTheMerge:
    def test_a_red_check_stops_before_the_merge_and_says_how_to_continue(self, world: World) -> None:
        world.configure(pr_checks=["red"])
        first = world.release(VERSION)
        assert first.returncode != 0
        assert "the pull request" in _out(first)
        assert "cannot be merged" in _out(first)
        assert RESUME in _out(first)
        assert "Nothing was uploaded to PyPI." in _out(first)
        assert (world.dispatches(), _states(world), world.origin_git("tag", "--list")) == ([], ["OPEN"], "")
        assert world.origin_git("rev-parse", "main") == world.state_base()

    def test_a_rerun_after_the_red_check_is_fixed_resumes_without_a_new_commit_branch_or_pull_request(
        self, world: World,
    ) -> None:
        world.configure(pr_checks=["red"])
        assert world.release(VERSION).returncode != 0
        commits = world.commit_count()

        world.configure(pr_checks=["green"])
        rerun = world.release(VERSION)

        assert rerun.returncode == 0, _out(rerun)
        assert world.commit_count() == commits
        assert (_count(world, PUSH_OF_BRANCH), _count(world, "gh pr-create"), _count(world, "gh pr-merge ")) == (1, 1, 1)
        assert world.dispatches() == [DISPATCH]

    def test_a_required_check_that_is_still_running_holds_the_merge_back_until_the_deadline(self, world: World) -> None:
        world.configure(pr_checks=["pending"])
        done = world.release(VERSION, **SHORT)
        assert done.returncode != 0
        assert "STOPPED: the required checks of the pull request" in _out(done)
        assert "did not finish before the deadline" in _out(done)
        assert RESUME in _out(done)
        assert (world.dispatches(), _states(world), _count(world, "gh pr-merge")) == ([], ["OPEN"], 0)

        world.configure(pr_checks=["green"])
        assert world.release(VERSION).returncode == 0
        assert (_states(world), world.dispatches()) == (["MERGED"], [DISPATCH])

    def test_a_pull_request_closed_meanwhile_is_not_merged_and_a_rerun_opens_a_new_one(self, world: World) -> None:
        world.configure(pr_checks=["red"])
        assert world.release(VERSION).returncode != 0
        store = world.state / "prs.json"
        store.write_text(json.dumps([{**row, "state": "CLOSED"} for row in world.pull_requests()]), encoding="utf-8")

        world.configure(pr_checks=["green"])
        rerun = world.release(VERSION)

        assert rerun.returncode == 0, _out(rerun)
        assert _states(world) == ["CLOSED", "MERGED"]
        assert _count(world, PUSH_OF_BRANCH) == 1, "the branch was there, it is not pushed again"

    @pytest.mark.parametrize(
        ("kind", "word"),
        [
            ("pr-list", "GitHub could not be asked for the pull requests"),
            ("pr-checks", "GitHub could not be asked for the required checks"),
        ],
    )
    def test_github_not_answering_is_a_stop_and_never_a_pass(self, world: World, kind: str, word: str) -> None:
        world.configure(gh_fail_on={kind: 1})
        done = world.release(VERSION)
        assert done.returncode != 0
        assert f"STOPPED: {word}" in _out(done)
        assert (world.dispatches(), _count(world, "gh pr-merge")) == ([], 0)
        assert world.origin_git("rev-parse", "main") == world.state_base()

        world.configure(gh_fail_on={})
        assert world.release(VERSION).returncode == 0
        assert len(world.pull_requests()) == 1


class TestTheMergeIsGitHubsToRefuse:
    def test_a_merge_that_github_refuses_stops_with_its_reason_and_the_rerun_merges_once(self, world: World) -> None:
        world.configure(pr_merge_fail="Pull request is not mergeable: a conversation is not resolved")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "STOPPED: GitHub did not merge the pull request" in _out(done)
        assert "a conversation is not resolved" in _out(done)
        assert RESUME in _out(done)
        assert (world.dispatches(), _states(world), world.origin_git("tag", "--list")) == ([], ["OPEN"], "")
        assert world.origin_git("rev-parse", "main") == world.state_base()

        world.configure(pr_merge_fail="")
        rerun = world.release(VERSION)

        assert rerun.returncode == 0, _out(rerun)
        assert (_count(world, "gh pr-create"), _states(world)) == (1, ["MERGED"])

    def test_a_pull_request_that_cannot_be_opened_stops_with_the_branch_pushed_and_the_rerun_opens_it(
        self, world: World,
    ) -> None:
        world.configure(pr_create_fail="gh: HTTP 422: Validation Failed")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "STOPPED: the pull request of release/0.2.0 could not be opened" in _out(done)
        assert RESUME in _out(done)
        assert world.origin_git("rev-parse", f"refs/heads/{BRANCH}") == world.head()
        assert (world.pull_requests(), world.dispatches()) == ([], [])

        world.configure(pr_create_fail="")
        assert world.release(VERSION).returncode == 0
        assert (_count(world, PUSH_OF_BRANCH), _states(world)) == (1, ["MERGED"])

    def test_a_squash_gives_the_commit_another_hash_and_no_tag_is_made(self, world: World) -> None:
        world.configure(pr_merge_as="squash")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "no merge commit of the release commit" in _out(done)
        assert "squash or rebase" in _out(done)
        assert (world.dispatches(), world.origin_git("tag", "--list")) == ([], "")

    def test_a_merge_commit_with_another_tree_than_the_release_commit_is_not_tagged(self, world: World) -> None:
        world.configure(pr_merge_as="tree")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "is not what was built" in _out(done)
        assert (world.dispatches(), world.origin_git("tag", "--list")) == ([], "")

    def test_a_rerun_after_the_merge_finds_it_and_opens_merges_and_pushes_nothing_again(self, world: World) -> None:
        world.configure(ci_release=["red", "green"])
        first = world.release(VERSION)
        assert first.returncode != 0
        assert "CI is not green on the merge commit" in _out(first)
        merge = world.merge_commit()

        rerun = world.release(VERSION)

        assert rerun.returncode == 0, _out(rerun)
        assert world.git("rev-parse", "HEAD") == merge == world.origin_git("rev-parse", f"{TAG}^{{commit}}")
        assert (_count(world, PUSH_OF_BRANCH), _count(world, "gh pr-create"), _count(world, "gh pr-merge ")) == (1, 1, 1)
        assert "is already on" in rerun.stdout


class TestWhatIsLeftOverIsNeverMovedOrDeleted:
    def test_a_branch_of_the_release_that_no_release_commit_explains_is_refused_before_any_change(
        self, world: World,
    ) -> None:
        world.origin_git("branch", BRANCH, world.state_base())
        done = world.release(VERSION)
        assert done.returncode != 0
        assert f"the branch {BRANCH} exists on" in _out(done)
        assert "this script never deletes one" in _out(done)
        assert not [line for line in world.events() if line.startswith(("gate", "build", "hook", "gh pr", "gh workflow"))]
        assert world.git("status", "--porcelain", "--untracked-files=no") == ""

    def test_a_branch_at_another_commit_than_the_release_commit_is_not_moved(self, world: World) -> None:
        world.configure(pr_create_fail="gh: HTTP 422: Validation Failed")
        assert world.release(VERSION).returncode != 0
        world.origin_git("update-ref", f"refs/heads/{BRANCH}", world.state_base())
        world.configure(pr_create_fail="")

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "this script never moves or deletes a branch" in _out(done)
        assert world.origin_git("rev-parse", f"refs/heads/{BRANCH}") == world.state_base()
        assert (world.pull_requests(), world.dispatches()) == ([], [])

    def test_a_main_that_moved_past_the_base_is_never_rebased_over_and_nothing_is_pushed(self, world: World) -> None:
        world.configure(pr_checks=["red"])
        assert world.release(VERSION).returncode != 0
        pushes = _count(world, "hook pre-push")
        moved = world.advance_origin()

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "has moved past the base of the release commit" in _out(done)
        assert "Nothing was tagged and the version is not spent" in _out(done)
        assert world.origin_git("rev-parse", "main") == moved
        assert (_count(world, "hook pre-push"), _states(world), world.dispatches()) == (pushes, ["OPEN"], [])

    def test_a_main_that_moves_while_the_gates_run_is_refused_before_the_branch_is_pushed(self, world: World) -> None:
        """The first read of main is hours old when the gates and the hooks are done: it is read again before the push."""
        world.configure(gh_hooks=[{"on": "ci", "nth": 1, "cmd": world.advance_origin_later()}])

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "has moved past the base of the release commit" in _out(done)
        assert "Nothing was tagged and the version is not spent" in _out(done)
        assert (_count(world, "hook pre-push"), world.pull_requests(), world.dispatches()) == (0, [], [])

    def test_a_main_that_moves_while_the_checks_run_is_refused_before_the_merge_with_the_way_back(
        self, world: World,
    ) -> None:
        """The strict rule refuses a pull request that main has moved past: the script says why instead of GitHub's words."""
        world.configure(pr_checks=["pending", "green"], gh_hooks=[{"on": "pr-checks", "nth": 1, "cmd": world.advance_origin_later()}])

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "has moved past the base of the release commit" in _out(done)
        assert "git reset --hard axiumine/main" in _out(done)
        assert (_count(world, "gh pr-merge"), _states(world), world.dispatches()) == (0, ["OPEN"], [])
        assert world.origin_git("tag", "--list") == ""

    def test_a_pull_request_merged_by_hand_while_the_checks_ran_is_not_a_stale_base(self, world: World) -> None:
        """Somebody merged it meanwhile: the merge step finds it merged, and the release goes on to the merge commit."""
        head = f"$(git --git-dir {world.origin} rev-parse refs/heads/{BRANCH})"
        merge = f"gh pr merge 1 -R {GITHUB_REPO} --merge --match-head-commit {head}"
        world.configure(pr_checks=["pending", "green"], gh_hooks=[{"on": "pr-checks", "nth": 1, "cmd": merge}])

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert (_count(world, "gh pr-merge "), _states(world), world.dispatches()) == (1, ["MERGED"], [DISPATCH])

    def test_a_stale_base_is_refused_before_the_branch_is_pushed(self, world: World) -> None:
        world.configure(gh_fail_on={"pr-list": 1})
        assert world.release(VERSION).returncode != 0
        world.advance_origin()
        world.configure(gh_fail_on={})

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "has moved past the base of the release commit" in _out(done)
        assert _count(world, "hook pre-push") == 0
        assert world.origin_git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines() == ["refs/heads/main"]
