# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""``scripts/release-testpypi.sh`` rehearses the upload of a release on TestPyPI, and touches nothing else.

The real script runs in a scratch world (``tests/release_support/world.py``) against a fake origin, ``gh`` and ``curl``: the
release ``scripts/release-pypi.sh`` made up to the approval of the environment ``pypi`` (release commit pushed, tag pushed,
signing run green), and then the rehearsal. The cases pin what the design says of it: it refuses to run unless the
repository is set up for the environment ``testpypi``, before it reads or dispatches anything; it dispatches
``publish-testpypi.yml`` on the tag, once, and never ``publish.yml``; it reads ``test.pypi.org`` and
``test-files.pythonhosted.org`` and never ``pypi.org`` or ``files.pythonhosted.org``; it makes no release (no commit, tag, push,
build or changelog stamp) and leaves HEAD, the branches and the tags alone; and it runs beside a release that holds its own lock.
``test_release_testpypi_resume.py`` holds what a re-run continues from, ``test_release_testpypi_scripts_text.py`` the text of the scripts.
"""

import fcntl
import shutil
from pathlib import Path

import pytest

from tests.release_support.testpypi_script import (
    FIRST_LINE,
    LAST_LINE,
    PYPI_RUN_URL,
    RESUME,
    SDIST,
    TAG,
    TESTPYPI_DISPATCH,
    TESTPYPI_RUN_URL,
    VERSION,
    WHEEL,
    assert_pypi_untouched,
    dispatches,
    held_by_testpypi,
    hosts,
    make_rehearse,
    make_released,
    out,
    recorded,
    run_script,
    since,
)
from tests.release_support.world import World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


@pytest.fixture
def released(world: World) -> World:
    return make_released(world)


@pytest.fixture
def rehearse(released: World):
    return make_rehearse(released)


class TestTheRehearsal:
    def test_the_signed_files_go_to_testpypi_through_its_workflow_and_pypi_is_not_touched(
        self, released: World, rehearse,
    ) -> None:
        mark = len(released.events())

        done = rehearse()

        assert done.returncode == 0, out(done)
        assert dispatches(released, "publish-testpypi.yml") == [TESTPYPI_DISPATCH]
        assert held_by_testpypi(released) == recorded(released)
        assert set(recorded(released)) == {WHEEL, SDIST}
        assert_pypi_untouched(released)
        assert hosts(since(released, mark)) == {"test.pypi.org", "test-files.pythonhosted.org"}

    def test_the_first_line_says_what_it_is_and_the_last_says_what_is_left(self, released: World, rehearse) -> None:
        lines = rehearse().stdout.splitlines()

        assert lines[0] == FIRST_LINE
        assert lines[-1] == LAST_LINE

    def test_the_guard_has_spoken_before_the_dispatch(self, released: World, rehearse) -> None:
        lines = rehearse().stdout.splitlines()
        dispatching = next(n for n, line in enumerate(lines) if line.startswith("Dispatching publish-testpypi.yml"))

        assert lines.index("release guard: all 5 conditions hold") < dispatching

    def test_the_run_it_waited_for_and_the_run_to_approve_next_are_named(self, released: World, rehearse) -> None:
        done = rehearse()

        assert done.returncode == 0, out(done)
        assert f"Dispatched: {TESTPYPI_RUN_URL}" in done.stdout
        assert "TestPyPI lists every file of 0.2.0 with the manifest's digests." in done.stdout
        assert f"TestPyPI only; PyPI was not touched. axmem {VERSION} is on test.pypi.org with the manifest's digests" in done.stdout
        assert (
            f"The publish.yml run of {TAG} to approve in the environment pypi: {PYPI_RUN_URL}" in done.stdout.splitlines()
        )

    def test_the_settings_are_read_before_anything_else_is_read_or_dispatched(self, released: World, rehearse) -> None:
        mark = len(released.events())

        assert rehearse().returncode == 0
        events = since(released, mark)
        settings = [n for n, line in enumerate(events) if line.startswith("gh api ") and "testpypi" in line]
        first = min(n for n, line in enumerate(events) if not line.startswith("gh api "))

        assert settings
        assert max(settings) < first

    def test_the_settings_it_reads_are_those_of_the_rehearsal_and_not_those_of_the_upload_to_pypi(
        self, released: World, rehearse,
    ) -> None:
        mark = len(released.events())

        assert rehearse().returncode == 0
        api = [line for line in since(released, mark) if line.startswith("gh api ")]

        assert any("environments/testpypi" in line for line in api)
        assert any("actions/workflows/publish-testpypi.yml" in line for line in api)
        assert [line for line in api if "environments/pypi" in line or "workflows/publish.yml" in line] == []

    def test_testpypi_is_read_before_the_dispatch_and_after_the_guard(self, released: World, rehearse) -> None:
        mark = len(released.events())

        assert rehearse().returncode == 0
        events = since(released, mark)
        first_read = next(n for n, line in enumerate(events) if line.startswith("curl https://test.pypi.org/"))
        dispatch = events.index(TESTPYPI_DISPATCH)
        downloads = [n for n, line in enumerate(events) if line.startswith("gh release-download")]

        assert max(downloads) < first_read < dispatch
        assert any(line.startswith("curl https://test-files.pythonhosted.org/") for line in events[dispatch:])

    def test_nothing_of_the_repository_changes_but_the_remote_tracking_ref_the_guard_fetches(
        self, released: World, rehearse,
    ) -> None:
        def snapshot() -> dict[str, str]:
            return {
                "head": released.git("rev-parse", "HEAD"),
                "branches": released.git("for-each-ref", "refs/heads"),
                "tags": released.git("for-each-ref", "refs/tags"),
                "origin": released.origin_git("for-each-ref"),
                "status": released.git("status", "--porcelain"),
                "log": released.git("rev-list", "--count", "--all"),
                "tree": released.git("rev-parse", "HEAD^{tree}"),
            }

        before = snapshot()

        assert rehearse().returncode == 0
        assert snapshot() == before

    def test_it_makes_no_release_of_its_own(self, released: World, rehearse) -> None:
        mark = len(released.events())

        assert rehearse().returncode == 0
        events = since(released, mark)

        assert not [line for line in events if line.startswith(("uv ", "hook ", "gate "))]
        assert not [line for line in events if line.startswith("gh ") and "dispatch" in line and "testpypi" not in line]
        assert released.pypi() == {}
        assert not (released.state / "builds").exists() or (released.state / "builds").read_text(encoding="utf-8") == "1"

    def test_it_runs_beside_a_release_that_holds_its_own_lock(self, released: World, rehearse) -> None:
        lock = Path(released.git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "release-pypi.lock"
        with lock.open("w", encoding="utf-8") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)

            done = rehearse()

        assert done.returncode == 0, out(done)

    def test_a_second_rehearsal_is_refused_while_one_runs(self, released: World, rehearse) -> None:
        lock = Path(released.git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "release-testpypi.lock"
        with lock.open("w", encoding="utf-8") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)

            done = rehearse()

        assert done.returncode == 1
        assert "another rehearsal is running in this repository" in done.stderr
        assert dispatches(released, "publish-testpypi.yml") == []


class TestWhatItRefuses:
    def test_a_repository_that_is_not_set_up_for_the_environment_testpypi_is_refused_before_anything_is_read(
        self, released: World, rehearse,
    ) -> None:
        released.configure(publish_ready_patch={"testpypi_environment": {"can_admins_bypass": True}})
        mark = len(released.events())

        done = rehearse()

        assert done.returncode == 1
        assert "the repository is not set up for the TestPyPI rehearsal" in done.stderr
        assert "can_admins_bypass" in out(done) or "bypass" in out(done)
        assert not [line for line in since(released, mark) if not line.startswith("gh api ")]
        assert dispatches(released, "publish-testpypi.yml") == []

    def test_a_missing_environment_is_refused_whatever_state_the_pypi_environment_is_in(
        self, released: World, rehearse,
    ) -> None:
        released.configure(publish_ready={"testpypi_environment": {"__http": 404}})

        done = rehearse()

        assert done.returncode == 1
        assert "testpypi" in out(done)
        assert dispatches(released, "publish-testpypi.yml") == []

    @pytest.mark.parametrize("args", [(), (VERSION, "0.3.0")])
    def test_exactly_one_argument_is_required(self, released: World, rehearse, args: tuple[str, ...]) -> None:
        done = run_script(released, *args)

        assert done.returncode == 1
        assert "exactly one argument, the version" in done.stderr
        assert done.stdout == ""

    @pytest.mark.parametrize("version", ["1.0", "v0.2.0", "0.2.0-rc1", "0.2.0 ", "../0.2.0"])
    def test_a_version_that_is_not_canonical_is_refused_before_anything_is_printed(
        self, released: World, version: str,
    ) -> None:
        mark = len(released.events())

        done = run_script(released, version)

        assert done.returncode == 1
        assert "invalid version" in done.stderr
        assert done.stdout == ""
        assert since(released, mark) == []

    @pytest.mark.parametrize("name", ["RELEASE_WAIT_TIMEOUT_S", "RELEASE_POLL_INTERVAL_S"])
    def test_a_wait_that_is_not_a_number_above_zero_is_refused(self, released: World, name: str) -> None:
        done = run_script(released, VERSION, **{name: "0"})

        assert done.returncode == 1
        assert f"{name} must be" in done.stderr

    def test_a_remote_that_is_not_on_github_is_refused_before_anything_is_read(
        self, released: World, rehearse,
    ) -> None:
        released.git("remote", "set-url", released.remote, "https://gitlab.com/Axiumine/axmem.git")
        mark = len(released.events())

        done = rehearse()

        assert done.returncode == 1
        assert "is not on github.com" in done.stderr
        assert since(released, mark) == []

    def test_a_version_that_has_no_release_commit_is_sent_to_the_release_script(self, world: World) -> None:
        done = run_script(world, VERSION)

        assert done.returncode == 1
        assert f"no release commit of {VERSION} in the history of HEAD" in done.stderr
        assert f"bash scripts/release-pypi.sh {VERSION}" in done.stderr
        assert world.dispatches() == []
        assert held_by_testpypi(world) == {}
        assert world.commit_count() == 1

    def test_a_tree_that_is_not_the_release_commit_s_is_refused(self, released: World, rehearse) -> None:
        (released.root / "release-digests.json").write_text("{}\n", encoding="utf-8")

        done = rehearse()

        assert done.returncode == 1
        assert "tracked files have uncommitted changes" in done.stderr
        assert dispatches(released, "publish-testpypi.yml") == []

    def test_a_release_commit_that_ci_has_not_passed_is_refused_by_the_guard_before_testpypi_is_read(
        self, released: World, rehearse,
    ) -> None:
        released.configure(ci_release=["red"])
        mark = len(released.events())

        done = rehearse()

        assert done.returncode == 1
        assert "the release guard refused the rehearsal" in done.stderr
        assert RESUME in done.stderr
        assert "PyPI was not touched" in done.stderr
        assert [line for line in since(released, mark) if line.startswith("curl ")] == []
        assert dispatches(released, "publish-testpypi.yml") == []

    def test_a_branch_named_like_the_tag_stops_the_rehearsal_before_the_dispatch(
        self, released: World, rehearse,
    ) -> None:
        released.git("push", released.remote, f"HEAD:refs/heads/{TAG}")

        done = rehearse()

        assert done.returncode == 1
        assert f"a branch named {TAG} exists on" in done.stderr
        assert RESUME in done.stderr
        assert dispatches(released, "publish-testpypi.yml") == []
