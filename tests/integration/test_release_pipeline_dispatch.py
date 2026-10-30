# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The dispatch of ``publish.yml`` is judged by the rules of the real ``gh workflow run`` and by what GitHub and PyPI hold.

The release uploads nothing: its one publication step asks GitHub to run the workflow on the tag, and the workflow's job in
the environment ``pypi`` uploads the signed files with an OIDC identity. A dispatch that named another workflow, a branch
instead of the tag or no input would run the wrong code or none, so the fake ``gh`` refuses all of them as the real one
would, and the cases here pin the exact call the script makes: once, on the tag (``--ref`` is the tag, ``-f tag=`` is the
tag), after the guard, never after a refusal. What GitHub and PyPI already hold decides the rest: a run that is live is
waited on and a green one that is recent is not dispatched again, a red or cancelled run stops with its address, a version
PyPI holds in part is completed with a new dispatch, a version PyPI holds with other bytes is never dispatched, and a
version PyPI holds whole is verified and left alone.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.release_support.world import GITHUB_REPO, PACKAGE, World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
TAG = f"v{VERSION}"
WHEEL = f"{PACKAGE}-{VERSION}-py3-none-any.whl"
SDIST = f"{PACKAGE}-{VERSION}.tar.gz"
DISPATCH = f"gh workflow-run publish.yml tag={TAG}"
RUN_URL = "https://github.example/run/publish-1000"
RESUME = f"bash scripts/release-pypi.sh {VERSION}"
#: copies the files the release built to the fake PyPI, as a publish run that is done does
PUBLISH = 'cp "$FAKE_STATE"/built/* "$FAKE_STATE"/pypi/'


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


def _recorded(world: World) -> dict[str, str]:
    return {entry["name"]: entry["sha256"] for entry in world.manifest()["artifacts"]}


def _runs(world: World) -> list[dict[str, object]]:
    """The ``publish.yml`` runs the fake ``gh`` holds."""
    state = json.loads((world.state / "publish-runs.json").read_text(encoding="utf-8"))
    runs: list[dict[str, object]] = state["runs"]
    return runs


def _approve(world: World) -> None:
    """The reviewer approves: the run that waits goes on to succeed (its next look)."""
    path = world.state / "publish-runs.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    for run in state["runs"]:
        run["script"] = ["in_progress", "success"]
        run["seen"] = 0
    path.write_text(json.dumps(state), encoding="utf-8")


class TestTheContractOfTheDispatch:
    def test_one_dispatch_on_the_tag_at_the_release_commit(self, world: World) -> None:
        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]
        assert not [line for line in world.events() if line.startswith("gh workflow-run-refused")]
        (run,) = _runs(world)
        assert (run["tag"], run["sha"]) == (TAG, world.head())
        assert f"Dispatched: {RUN_URL}" in _out(done)

    def test_the_dispatch_comes_after_the_guard_has_held_all_five_conditions(
        self, world: World,
    ) -> None:
        done = world.release(VERSION)

        text = done.stdout
        assert text.index("release guard: all 5 conditions hold") < text.index("Dispatching publish.yml on v0.2.0")

    @pytest.mark.parametrize(
        ("argv", "why"),
        [
            (["workflow", "run", "release.yml", "--ref", TAG, "-f", f"tag={TAG}"], "release.yml"),
            (["workflow", "run", "publish.yml", "--ref", "main", "-f", f"tag={TAG}"], "dispatch on the tag itself"),
            (["workflow", "run", "publish.yml", "--ref", TAG], "required input 'tag' not provided"),
            (
                ["workflow", "run", "publish.yml", "--ref", TAG, "-f", f"tag={TAG}", "-f", "dry=1"],
                "unexpected inputs",
            ),
            (["workflow", "run", "publish.yml", "--ref", "v9.9.9", "-f", "tag=v9.9.9"], "No ref found"),
        ],
        ids=["other workflow", "ref is a branch", "no input", "extra input", "tag not on the remote"],
    )
    def test_the_fake_refuses_what_the_real_gh_would_refuse(
        self, world: World, argv: list[str], why: str,
    ) -> None:
        assert world.release(VERSION).returncode == 0
        before = world.dispatches()

        done = subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
            ["gh", *argv[:3], "-R", GITHUB_REPO, *argv[3:]],  # noqa: S607 - a program of the test setup, run without a shell
            cwd=world.tmp,
            env=world._env(),
            capture_output=True,
            text=True,
            check=False,
        )

        assert done.returncode == 1
        assert why in done.stderr
        assert world.dispatches() == before

    def test_a_dispatch_the_fake_accepts_is_the_one_the_script_makes(self, world: World) -> None:
        assert world.release(VERSION).returncode == 0

        done = subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
            ["gh", "workflow", "run", "publish.yml", "-R", GITHUB_REPO, "--ref", TAG, "-f", f"tag={TAG}"],  # noqa: S607 - a program of the test setup, run without a shell
            cwd=world.tmp,
            env=world._env(),
            capture_output=True,
            text=True,
            check=False,
        )

        assert done.returncode == 0, done.stderr
        assert world.dispatches() == [DISPATCH, DISPATCH]

    def test_a_guard_that_refuses_is_never_followed_by_a_dispatch(self, world: World) -> None:
        world.configure(release_extra=["stray-0.2.0.zip"])

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "release guard refused the publication" in _out(done)
        assert world.dispatches() == []
        assert not [line for line in world.events() if line.startswith("gh workflow-run")]

    def test_a_dispatch_that_fails_leaves_nothing_and_the_re_run_dispatches_once(
        self, world: World,
    ) -> None:
        world.configure(gh_fail_on={"publish-dispatch": 1})

        first = world.release(VERSION)

        assert first.returncode != 0
        assert world.dispatches() == []
        assert world.pypi() == {}
        assert "dispatch of v0.2.0 failed or its run was not seen in time" in _out(first)
        assert RESUME in _out(first)
        world.configure(gh_fail_on={})
        commits = world.commit_count()

        again = world.release(VERSION)

        assert again.returncode == 0, _out(again)
        assert world.dispatches() == [DISPATCH]
        assert world.commit_count() == commits


class TestALiveRunIsWaitedOnAndNeverDispatchedAgain:
    def test_a_run_that_waits_for_its_reviewer_stops_the_release_at_the_deadline_with_the_run_address(
        self, world: World,
    ) -> None:
        world.configure(publish_runs=["waiting"])

        done = world.release(VERSION, RELEASE_WAIT_TIMEOUT_S="4", RELEASE_POLL_INTERVAL_S="0.2")

        assert done.returncode != 0
        assert world.dispatches() == [DISPATCH]
        assert "a reviewer must approve the deployment to environment pypi" in _out(done)
        assert RUN_URL in _out(done)
        assert "approve the deployment of environment pypi if it waits" in _out(done)
        assert "may be live or done" in _out(done)
        assert RESUME in _out(done)
        assert world.pypi() == {}

    def test_the_re_run_waits_on_that_run_and_dispatches_nothing(self, world: World) -> None:
        world.configure(publish_runs=["waiting"])
        assert world.release(VERSION, RELEASE_WAIT_TIMEOUT_S="4", RELEASE_POLL_INTERVAL_S="0.2").returncode != 0
        _approve(world)

        again = world.release(VERSION)

        assert again.returncode == 0, _out(again)
        assert world.dispatches() == [DISPATCH]
        assert f"is live ({RUN_URL}) — waiting for it, no second dispatch" in _out(again)
        assert world.pypi() == _recorded(world)

    def test_a_run_that_is_live_before_the_script_asks_is_waited_on_not_dispatched(
        self, world: World,
    ) -> None:
        world.configure(
            publish_existing=[{"script": ["in_progress", "success"]}],
            gh_hooks=[{"on": "publish-view", "nth": 1, "cmd": PUBLISH}],
        )

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == []
        assert "no second dispatch" in _out(done)
        assert world.pypi() == _recorded(world)


class TestAGreenRunThatIsRecentIsNotDispatchedAgain:
    def test_the_release_goes_straight_to_reading_pypi_until_it_lists_the_files(
        self, world: World,
    ) -> None:
        world.configure(
            publish_existing=[{"state": "success", "age_s": 60}],
            gh_hooks=[{"on": "publish-runs", "nth": 1, "cmd": PUBLISH}],
        )

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == []
        assert "succeeded a moment ago" in _out(done)
        assert world.pypi() == _recorded(world)

    def test_a_green_run_older_than_the_window_does_not_count(self, world: World) -> None:
        world.configure(publish_existing=[{"state": "success", "age_s": 3600}])

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]
        assert "succeeded a moment ago" not in _out(done)

    def test_a_red_run_does_not_count_either(self, world: World) -> None:
        world.configure(publish_existing=[{"state": "failure", "age_s": 60}])

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]


class TestARunThatDidNotSucceedStops:
    def test_a_red_run_stops_with_its_address_and_the_tail_of_its_log(self, world: World) -> None:
        world.configure(
            publish_runs=["in_progress", "failure"],
            run_log=["verify: refused", "the last line of the log"],
        )

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == [DISPATCH]
        assert f"STOPPED: the publish.yml run {RUN_URL} did not succeed (see above)" in _out(done)
        assert RUN_URL in _out(done)
        assert "the last line of the log" in _out(done)
        assert "The publish.yml run of v0.2.0 may be live or done" in _out(done)
        assert RESUME in _out(done)
        assert "Nothing was uploaded to PyPI" not in _out(done)
        assert world.pypi() == {}

    def test_a_name_that_pypi_refused_as_used_says_that_the_version_is_spent(
        self, world: World,
    ) -> None:
        world.configure(
            publish_runs=["failure"],
            run_log="ERROR HTTPError: 400 Bad Request: This filename has been previously used",
        )

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "this version is spent" in _out(done)

    def test_a_run_replaced_by_a_newer_dispatch_is_followed_to_the_newer_one(
        self, world: World,
    ) -> None:
        world.configure(publish_runs=["cancelled"], publish_successor=["in_progress", "success"])

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]
        assert "was cancelled; waiting for run" in _out(done)
        assert world.pypi() == _recorded(world)

    def test_a_cancelled_run_with_no_newer_one_is_a_red_run(self, world: World) -> None:
        world.configure(publish_runs=["cancelled"])

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == [DISPATCH]
        assert "did not succeed" in _out(done)

    def test_a_re_run_after_a_red_run_dispatches_again(self, world: World) -> None:
        world.configure(publish_runs=[["failure"], ["success"]])
        assert world.release(VERSION).returncode != 0

        again = world.release(VERSION)

        assert again.returncode == 0, _out(again)
        assert world.dispatches() == [DISPATCH, DISPATCH]
        assert world.pypi() == _recorded(world)


class TestWhatPypiHoldsDecidesTheDispatch:
    def test_a_version_held_in_part_with_the_manifests_bytes_resumes_with_a_new_dispatch(
        self, world: World,
    ) -> None:
        world.configure(publish_runs=[["failure"], ["success"]])
        assert world.release(VERSION).returncode != 0
        (world.state / "pypi" / WHEEL).write_bytes((world.state / "built" / WHEEL).read_bytes())

        again = world.release(VERSION)

        assert again.returncode == 0, _out(again)
        assert world.dispatches() == [DISPATCH, DISPATCH]
        assert world.pypi() == _recorded(world)

    def test_a_version_held_with_other_bytes_is_never_dispatched(self, world: World) -> None:
        world.configure(publish_runs=["failure"])
        assert world.release(VERSION).returncode != 0
        (world.state / "pypi" / WHEEL).write_bytes(b"someone else's bytes\n")

        again = world.release(VERSION)

        assert again.returncode != 0
        assert world.dispatches() == [DISPATCH]
        assert "spent" in _out(again)
        assert "release-superseded.json" in _out(again)

    def test_a_version_held_whole_dispatches_nothing_even_when_no_run_is_known(
        self, world: World,
    ) -> None:
        assert world.release(VERSION).returncode == 0
        (world.state / "publish-runs.json").unlink()

        again = world.release(VERSION)

        assert again.returncode == 0, _out(again)
        assert world.dispatches() == [DISPATCH]
        assert "nothing to dispatch" in _out(again)
        assert world.pypi() == _recorded(world)

    def test_a_version_held_whole_with_a_red_run_on_record_dispatches_nothing(
        self, world: World,
    ) -> None:
        world.configure(publish_existing=[{"state": "failure", "age_s": 60}])
        assert world.release(VERSION).returncode == 0
        (world.state / "pypi" / WHEEL).write_bytes((world.state / "built" / WHEEL).read_bytes())

        again = world.release(VERSION)

        assert again.returncode == 0, _out(again)
        assert world.dispatches() == [DISPATCH]
