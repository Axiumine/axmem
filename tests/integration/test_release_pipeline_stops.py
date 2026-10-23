"""The other ways a release stops, run on the real script against the scratch world.

`test_release_pipeline.py` covers the stops the invariant names (commit, push, CI, signing run, bundles, digests, the
dispatch, PyPI). These are the rest: the arguments, PyPI or GitHub not answering, a release commit that cannot be
continued from, and state an earlier run left on this machine. The refusals of the repository come in `_bypasses_c.py`
(the branch, CI of HEAD), `_bypasses_e.py` (the publication settings) and the stops a re-run picks up again in
`_bypasses_d.py`. Each case asserts that the publish workflow was not dispatched (or, after the dispatch, that the release
says so), and that the script names the cause.
"""

import shutil
from pathlib import Path

import pytest

from tests.release_support.world import PACKAGE, World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
TAG = f"v{VERSION}"
DISPATCH = f"gh workflow-run publish.yml tag={TAG}"
RESUME = f"release-pypi.sh {VERSION}"
JSON_URL = f"curl https://pypi.org/pypi/{PACKAGE}/json"
#: the events of the steps that come after the first read of PyPI and the check of the local branch
LATER_STEPS = ("gate", "build")


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


def _later_steps(world: World) -> list[str]:
    """The events of the gate and the build."""
    return [line for line in world.events() if line.startswith(LATER_STEPS)]


def _commit_locally(world: World) -> None:
    """One commit on the local branch that the remote does not have."""
    (world.root / "local.txt").write_text("local\n", encoding="utf-8")
    world.git("add", "local.txt")
    world.git("commit", "-q", "-m", "local only")


class TestTheArguments:
    def test_without_a_version_it_prints_the_last_published_version_and_changes_nothing(
        self, world: World,
    ) -> None:
        world.add_old_release("0.1.0", signed=True)
        world.add_old_release("0.1.10", signed=True)
        base = world.head()
        done = world.run_script()
        assert done.returncode == 0, _out(done)
        assert "Last published version: 0.1.10" in done.stdout
        assert world.head() == base
        assert world.origin_git("rev-parse", "main") == base
        assert world.dispatches() == []
        assert not (world.root / ".git" / "release-pypi.lock").exists()

    def test_the_last_version_is_the_highest_one_that_is_not_yanked(self, world: World) -> None:
        world.add_old_release("0.1.0", signed=True)
        yanked = world.add_old_release("0.1.10", signed=True)
        world.configure(yanked=yanked)
        done = world.run_script()
        assert done.returncode == 0, _out(done)
        assert "Last published version: 0.1.0" in done.stdout

    def test_a_project_that_is_not_on_pypi_yet_has_no_last_version(self, world: World) -> None:
        done = world.run_script()
        assert done.returncode == 0, _out(done)
        assert f"No published versions of {PACKAGE} on PyPI yet." in done.stdout

    def test_the_query_reads_pypi_with_curl_alone_and_never_asks_github_or_opens_an_environment_file(
        self, world: World,
    ) -> None:
        world.add_old_release("0.1.0", signed=True)
        world.trap_env()
        (world.state / "events.log").unlink(missing_ok=True)
        done = world.run_script()
        assert done.returncode == 0, _out(done)
        assert world.events() == [JSON_URL]

    def test_a_pypi_that_does_not_answer_is_an_error_not_an_empty_project(
        self, world: World,
    ) -> None:
        world.configure(json_raw=503)
        done = world.run_script()
        assert done.returncode != 0
        assert "could not read" in _out(done) or "failed to read" in _out(done)
        assert "No published versions" not in _out(done)

    def test_more_than_one_argument_is_a_usage_error_before_anything_runs(
        self, world: World,
    ) -> None:
        done = world.run_script(VERSION, "extra")
        assert done.returncode != 0
        assert "too many arguments" in _out(done)
        assert world.events() == []

    def test_the_index_and_version_form_of_the_old_release_is_refused(self, world: World) -> None:
        done = world.run_script("prod", VERSION)
        assert done.returncode != 0
        assert "too many arguments" in _out(done)
        assert world.events() == []

    @pytest.mark.parametrize("argument", ["dev", "prod", "--help", "0.2", "v0.2.0"])
    def test_one_argument_that_is_not_a_version_is_refused_before_anything_runs(
        self, world: World, argument: str,
    ) -> None:
        done = world.run_script(argument)
        assert done.returncode != 0
        assert "invalid version" in _out(done)
        assert world.events() == []


class TestPypiNotAnsweringWithTheProjectsJson:
    @pytest.mark.parametrize(
        ("answer", "word"),
        [
            (503, "failed to read"),
            ("<html><body><h1>Maintenance</h1></body></html>", "is not the JSON of a project"),
            ('{"info": {"name": "another"}, "releases": {}}', "is not the JSON of axmem"),
        ],
        ids=["a 503", "a maintenance page", "another project"],
    )
    def test_an_answer_that_is_not_the_project_stops_the_release_before_anything_runs(
        self, world: World, answer: object, word: str,
    ) -> None:
        world.configure(json_raw=answer)
        done = world.release(VERSION)
        assert done.returncode != 0
        assert word in _out(done)
        assert _later_steps(world) == []
        assert world.dispatches() == []
        assert world.origin_git("tag", "--list") == ""

    def test_an_answer_that_fails_at_the_last_read_before_the_dispatch_stops_with_the_release_signed(
        self, world: World,
    ) -> None:
        # the first read is the start of the release, the second is the one right before the dispatch
        world.configure(json_raw=[None, 503])
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "failed to read" in _out(done)
        assert world.dispatches() == []
        assert world.origin_git("tag", "--list") == TAG
        world.configure(json_raw=None)
        rerun = world.release(VERSION)
        assert rerun.returncode == 0, _out(rerun)
        assert world.dispatches() == [DISPATCH]


class TestAReleaseCommitThatCannotBeContinuedFrom:
    def test_a_manifest_edited_after_the_commit_is_refused(self, world: World) -> None:
        world.configure(ci_release=["red", "green"])
        assert world.release(VERSION).returncode != 0
        (world.root / "release-digests.json").write_text("{}\n", encoding="utf-8")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "release-digests.json differs" in _out(done)
        assert world.dispatches() == []

    def test_a_branch_that_the_remote_moved_past_while_the_commit_was_unpushed_is_refused(
        self, world: World,
    ) -> None:
        world.mark("refuse-push-main")
        assert world.release(VERSION).returncode != 0
        world.advance_origin()
        world.mark("refuse-push-main", on=False)
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "DIVERGED" in _out(done)
        assert world.dispatches() == []

    def test_a_second_commit_on_top_of_an_unpushed_release_commit_is_refused(
        self, world: World,
    ) -> None:
        world.mark("refuse-push-main")
        assert world.release(VERSION).returncode != 0
        _commit_locally(world)
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "is not on axiumine/main, and it is not the only commit" in _out(done)
        assert world.dispatches() == []

    def test_a_tracked_file_changed_since_the_release_commit_stops_before_the_dispatch(
        self, world: World,
    ) -> None:
        world.configure(ci_release=["red", "green"])
        assert world.release(VERSION).returncode != 0
        builds = len([line for line in world.events() if line.startswith("build")])
        (world.root / "app" / "__init__.py").write_text("x = 2\n", encoding="utf-8")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "tracked files have uncommitted changes" in _out(done)
        assert len([line for line in world.events() if line.startswith("build")]) == builds
        assert world.dispatches() == []


class TestGitHubNotAnswering:
    def test_while_waiting_for_ci_it_stops_with_the_resume_command(self, world: World) -> None:
        # the first look at CI is the check of HEAD before the release commit, the second is the wait for the release commit
        world.configure(gh_fail_on={"ci": 2})
        done = world.release(VERSION)
        assert done.returncode != 0
        assert world.dispatches() == []
        assert "GitHub could not be asked for the CI runs" in _out(done)
        assert RESUME in _out(done)

    def test_while_waiting_for_the_signing_run_it_stops_with_the_resume_command(
        self, world: World,
    ) -> None:
        # the first look at release.yml, after the tag is pushed
        world.configure(gh_fail_on={"release-runs": 1})
        done = world.release(VERSION)
        assert done.returncode != 0
        assert world.dispatches() == []
        assert world.origin_git("tag", "--list") == TAG
        assert "GitHub could not be asked for the signing run" in _out(done)
        assert RESUME in _out(done)

    def test_the_list_of_publish_runs_not_read_stops_before_the_dispatch(
        self, world: World,
    ) -> None:
        world.configure(gh_fail_on={"publish-runs": 1})
        done = world.release(VERSION)
        assert done.returncode != 0
        assert world.dispatches() == []
        assert "could not read the publish.yml runs of" in _out(done)
        assert "Nothing was uploaded to PyPI." in _out(done)
        assert RESUME in _out(done)

    def test_a_run_that_cannot_be_looked_at_after_the_dispatch_stops_with_the_run_possibly_live(
        self, world: World,
    ) -> None:
        world.configure(gh_fail_on={"publish-view": 1})
        done = world.release(VERSION)
        assert done.returncode != 0
        assert world.dispatches() == [DISPATCH]
        assert "GitHub could not be asked for the publish.yml run" in _out(done)
        assert "may be live or done" in _out(done)
        assert RESUME in _out(done)


class TestStateAnEarlierRunLeftOnThisMachine:
    def test_a_local_tag_at_another_commit_is_not_moved(self, world: World) -> None:
        world.configure(ci_release=["red", "green"])
        assert world.release(VERSION).returncode != 0
        world.git("tag", "-a", TAG, world.state_base(), "-m", "somebody else's tag")
        elsewhere = world.git("rev-parse", f"{TAG}^{{commit}}")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "never moves a tag" in _out(done)
        assert world.git("rev-parse", f"{TAG}^{{commit}}") == elsewhere
        assert world.origin_git("tag", "--list") == ""
        assert world.dispatches() == []

    def test_a_tag_on_the_remote_at_another_commit_is_not_moved(self, world: World) -> None:
        world.configure(ci_release=["red", "green"])
        assert world.release(VERSION).returncode != 0
        world.origin_git("tag", "-a", TAG, world.state_base(), "-m", "somebody else's tag")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "never moved" in _out(done)
        assert world.origin_git("rev-parse", f"{TAG}^{{commit}}") == world.state_base()
        assert world.dispatches() == []
