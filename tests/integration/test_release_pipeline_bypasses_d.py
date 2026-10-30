# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The guard re-reads git and GitHub at the last moment, so a world that changed during the waits is caught at the dispatch.

The release waits for CI and for the signing run, and a bundle that was whole at the end of the wait may not be at the
guard. These cases change the world while the script waits (a hook of the fake ``gh`` fires on a given call): ``main``
rewritten, the tag moved, CI turned red, the bundles replaced, GitHub not answering, a file among the signed files that
the manifest does not list, and PyPI changed after the guard. Each must stop with ``publish.yml`` never dispatched (or
dispatched once, for the last). The stops after which a re-run picks the release up again close the file: a refused
release commit, a dispatch that failed, a signing run that stays red or is run again, a publish run that failed.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.release_support.world import GITHUB_REPO, World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
TAG = f"v{VERSION}"
DISPATCH = f"gh workflow-run publish.yml tag={TAG}"
RESUME = f"release-pypi.sh {VERSION}"


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


def _gh_rerun_signing_run(world: World) -> None:
    """What the maintainer does for a flaky signing run: `gh run rerun` of the run the push of the tag started."""
    done = subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
        ["gh", "run", "rerun", "1", "-R", GITHUB_REPO],  # noqa: S607 - a program of the test setup, run without a shell
        cwd=world.root,
        env=world._env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, _out(done)


def _edit_config(world: World, **changes: Any) -> str:
    """A shell command that changes the fakes' scenario, for a hook that fires while the script waits."""
    code = (
        "import json, sys; p = sys.argv[1]; c = json.load(open(p)); "
        "c.update(json.loads(sys.argv[2])); json.dump(c, open(p, 'w'))"
    )
    return (
        f"'{sys.executable}' -c \"{code}\" '{world.state / 'config.json'}' "
        f"'{json.dumps(changes)}'"
    )


class TestTheBundlesAreReadAgainByTheGuard:
    def test_a_release_that_was_whole_at_the_end_of_the_wait_and_is_not_at_the_guard_is_refused(
        self, world: World,
    ) -> None:
        world.configure(
            gh_hooks=[
                {
                    "on": "release-download",
                    "nth": 1,
                    "cmd": _edit_config(world, assets="foreign_bundle"),
                },
            ],
        )

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert "[4]" in _out(done)
        assert "signed sha256" in _out(done)


class TestTheGuardIsAskedAgainAtTheLastMoment:
    def test_main_rewritten_while_the_signing_run_is_awaited_is_refused(
        self, world: World,
    ) -> None:
        base = world.state_base()
        hook = f"git --git-dir '{world.origin}' update-ref refs/heads/main {base}"
        world.configure(gh_hooks=[{"on": "release-download", "nth": 1, "cmd": hook}])

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert "[1]" in _out(done)
        assert "REFUSED" in _out(done)

    def test_the_tag_moved_while_the_signing_run_is_awaited_is_refused(
        self, world: World,
    ) -> None:
        base = world.state_base()
        hook = (
            f"git --git-dir '{world.origin}' tag -d {TAG} >/dev/null; "
            f"git --git-dir '{world.origin}' tag {TAG} {base}"
        )
        # the guard's own first question to GitHub is the fourth look at CI: the tag moves just before its third condition
        world.configure(gh_hooks=[{"on": "ci", "nth": 4, "cmd": hook}])

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert f"tag {TAG} points at" in _out(done)

    def test_ci_turning_red_between_the_last_wait_and_the_guard_is_refused(
        self, world: World,
    ) -> None:
        red = [
            {
                "workflowName": "Static Analysis",
                "status": "completed",
                "conclusion": "failure",
                "url": "u",
            },
        ]
        world.configure(
            gh_hooks=[
                {
                    "on": "release-download",
                    "nth": 1,
                    "cmd": _edit_config(world, ci_raw=red),
                },
            ],
        )

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert "Static Analysis: failure" in _out(done)

    @pytest.mark.parametrize("kind", ["release-view", "release-download", "ci"])
    def test_github_not_answering_at_the_guard_is_never_a_pass(
        self, world: World, kind: str,
    ) -> None:
        # the first call of a kind is the wait's, the second is the guard's; the CI kind has one more before them
        nth = {"release-view": 2, "release-download": 2, "ci": 4}[kind]
        world.configure(gh_fail_on={kind: nth})

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert "HTTP 502" in _out(done)

    def test_a_file_among_the_signed_files_that_the_manifest_does_not_list_is_refused(
        self, world: World,
    ) -> None:
        world.configure(release_extra=["stray-0.2.0.zip"])

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert "signed/stray-0.2.0.zip is not an artefact of the manifest" in _out(done)


class TestPyPIChangingAfterTheGuard:
    def test_other_bytes_that_appear_on_pypi_after_the_guard_make_the_version_spent(
        self, world: World,
    ) -> None:
        sdist = world.state / "pypi" / f"axmem-{VERSION}.tar.gz"
        # the first look at the publish.yml runs is the one right after the guard
        hook = f"printf somebody-else > '{sdist}'"
        world.configure(
            publish_effect="none", gh_hooks=[{"on": "publish-runs", "nth": 1, "cmd": hook}],
        )

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "that version is spent" in _out(done)
        assert "Release complete" not in _out(done)
        assert world.dispatches() == [f"gh workflow-run publish.yml tag={TAG}"]


class TestAStopThatAReRunPicksUp:
    """The state is read from git, GitHub and PyPI, so the re-run continues at the first step that is not done."""

    def test_a_refused_release_commit_leaves_nothing_behind(self, world: World) -> None:
        base = world.head()
        world.mark("refuse-commit")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "the release commit was refused" in _out(done)
        assert world.head() == base
        assert world.git("status", "--porcelain", "--untracked-files=no") == ""
        assert world.origin_git("rev-parse", "main") == base
        assert world.origin_git("tag", "--list") == ""
        assert world.dispatches() == []

    def test_the_re_run_after_a_refused_release_commit_is_a_new_release(self, world: World) -> None:
        world.mark("refuse-commit")
        assert world.release(VERSION).returncode != 0
        world.mark("refuse-commit", on=False)
        rerun = world.release(VERSION)
        assert rerun.returncode == 0, _out(rerun)
        assert world.commit_count() == 2
        assert world.dispatches() == [DISPATCH]

    def test_a_dispatch_that_gh_refused_says_that_the_run_may_be_live(self, world: World) -> None:
        world.configure(gh_fail_on={"publish-dispatch": 1})
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "dispatch of v0.2.0 failed" in _out(done)
        assert "may be live or done" in _out(done)
        assert RESUME in _out(done)
        assert world.dispatches() == []
        assert world.pypi() == {}

    def test_the_re_run_after_a_refused_dispatch_dispatches_it_with_no_new_commit_or_tag(
        self, world: World,
    ) -> None:
        world.configure(gh_fail_on={"publish-dispatch": 1})
        assert world.release(VERSION).returncode != 0
        commits = world.commit_count()
        tagged = world.origin_git("rev-parse", TAG)
        world.configure(gh_fail_on={})
        rerun = world.release(VERSION)
        assert rerun.returncode == 0, _out(rerun)
        assert world.dispatches() == [DISPATCH]
        assert world.commit_count() == commits
        assert world.origin_git("rev-parse", TAG) == tagged

    def test_a_signing_run_that_stays_red_stops_every_re_run_at_the_same_place(
        self, world: World,
    ) -> None:
        world.configure(release_runs=["red"])
        assert world.release(VERSION).returncode != 0
        tagged = world.origin_git("rev-parse", TAG)
        again = world.release(VERSION)
        assert again.returncode != 0
        assert "release.yml concluded failure" in _out(again)
        assert "is not signed" in _out(again)
        assert world.origin_git("rev-parse", TAG) == tagged
        assert world.dispatches() == []
        assert world.pypi() == {}

    def test_a_signing_run_that_is_run_again_by_the_maintainer_lets_the_re_run_finish(
        self, world: World,
    ) -> None:
        world.configure(release_runs=["red"], release_runs_rerun=["pending", "green"])
        assert world.release(VERSION).returncode != 0
        tagged = world.origin_git("rev-parse", TAG)
        commits = world.commit_count()
        _gh_rerun_signing_run(world)
        rerun = world.release(VERSION)
        assert rerun.returncode == 0, _out(rerun)
        assert world.origin_git("rev-parse", TAG) == tagged
        assert world.commit_count() == commits
        assert world.dispatches() == [DISPATCH]

    def test_a_publish_run_that_failed_leaves_pypi_empty_and_the_re_run_dispatches_again(
        self, world: World,
    ) -> None:
        world.configure(
            publish_runs=[["queued", "failure"], ["queued", "success"]],
            run_log=["Run pypa/gh-action-pypi-publish", "HTTP 503 from the upload endpoint"],
        )
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "did not succeed" in _out(done)
        assert "HTTP 503 from the upload endpoint" in _out(done)
        assert "this version is spent" not in _out(done)
        assert world.pypi() == {}
        rerun = world.release(VERSION)
        assert rerun.returncode == 0, _out(rerun)
        assert world.dispatches() == [DISPATCH, DISPATCH]

    def test_a_publish_run_that_pypi_refused_for_a_used_name_says_the_version_is_spent(
        self, world: World,
    ) -> None:
        world.configure(
            publish_runs=["queued", "failure"],
            run_log=["Uploading axmem-0.2.0.tar.gz", "ERROR File already exists ('axmem-0.2.0.tar.gz')"],
        )
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "File already exists" in _out(done)
        assert "this version is spent" in _out(done)
        assert len(world.dispatches()) == 1
