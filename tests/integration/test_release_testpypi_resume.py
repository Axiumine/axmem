# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""``scripts/release-testpypi.sh`` continues from what TestPyPI and GitHub hold, and follows the run it dispatched.

A re-run reads GitHub and TestPyPI first: a version TestPyPI holds whole is verified with no dispatch, a half upload is finished by a
dispatch, a version it holds with other bytes (or under a name it has used) is spent on TestPyPI only, a live run is waited for and never
followed by a second one, and a run that fails, is cancelled or is refused stops with the command that resumes. The scratch world and the
release it rehearses are those of ``test_release_testpypi_script.py`` (``tests/release_support/testpypi_script.py``).
"""

import shutil
from pathlib import Path

import pytest

from tests.release_support.testpypi_script import (
    LAST_LINE,
    PYPI_DISPATCH,
    RESUME,
    TAG,
    TESTPYPI_DISPATCH,
    TESTPYPI_RUN_URL,
    VERSION,
    WAIT,
    WHEEL,
    approve_testpypi,
    dispatches,
    held_by_testpypi,
    make_rehearse,
    make_released,
    out,
    recorded,
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


class TestWhatTestPyPIHoldsDecides:
    def test_a_version_it_holds_whole_is_verified_and_nothing_is_dispatched(self, released: World, rehearse) -> None:
        assert rehearse().returncode == 0
        dispatches = released.dispatches()

        again = rehearse()

        assert again.returncode == 0, out(again)
        assert released.dispatches() == dispatches
        assert f"TestPyPI holds every file of {VERSION} with the manifest's digests already" in again.stdout
        assert again.stdout.splitlines()[-1] == LAST_LINE
        assert held_by_testpypi(released) == recorded(released)

    def test_a_half_upload_is_finished_by_a_dispatch(self, released: World, rehearse) -> None:
        # the run that leaves the files that TestPyPI holds out of its upload is the workflow's; the fake puts every file
        (released.state / "testpypi").mkdir(exist_ok=True)
        wanted = {entry["name"] for entry in released.manifest()["artifacts"]}
        built = released.state / "built"
        name = min(wanted)
        (released.state / "testpypi" / name).write_bytes((built / name).read_bytes())

        done = rehearse()

        assert done.returncode == 0, out(done)
        assert dispatches(released, "publish-testpypi.yml") == [TESTPYPI_DISPATCH]
        assert held_by_testpypi(released) == recorded(released)

    def test_a_file_it_holds_with_other_bytes_makes_the_version_spent_on_testpypi_only(
        self, released: World, rehearse,
    ) -> None:
        (released.state / "testpypi").mkdir(exist_ok=True)
        (released.state / "testpypi" / WHEEL).write_bytes(b"other bytes\n")

        done = rehearse()

        assert done.returncode == 1
        assert "that version is spent on TestPyPI and cannot be rehearsed again; PyPI is unaffected" in done.stderr
        assert "release-superseded" not in out(done)
        assert dispatches(released, "publish-testpypi.yml") == []
        assert released.pypi() == {}

    def test_testpypi_that_does_not_answer_is_refused_before_the_dispatch(self, released: World, rehearse) -> None:
        released.configure(testpypi_json_raw="<html>not the json</html>")

        done = rehearse()

        assert done.returncode == 1
        assert "could not tell what TestPyPI holds" in done.stderr or "failed to read" in done.stderr
        assert dispatches(released, "publish-testpypi.yml") == []


class TestTheRunIsFollowed:
    def test_a_run_that_waits_for_its_reviewer_stops_at_the_deadline_with_the_run_address_and_the_resume_command(
        self, released: World, rehearse,
    ) -> None:
        released.configure(testpypi_runs=["waiting"])

        done = rehearse(**WAIT)

        assert done.returncode == 1
        assert dispatches(released, "publish-testpypi.yml") == [TESTPYPI_DISPATCH]
        assert f"a reviewer must approve the deployment to environment testpypi: {TESTPYPI_RUN_URL}" in done.stdout
        assert "approve the deployment of environment testpypi if it waits" in done.stderr
        assert "PyPI was not touched" in done.stderr
        assert RESUME in done.stderr
        assert held_by_testpypi(released) == {}

    def test_the_re_run_waits_on_that_run_and_dispatches_no_second_one(self, released: World, rehearse) -> None:
        released.configure(testpypi_runs=["waiting"])
        assert rehearse(**WAIT).returncode == 1
        approve_testpypi(released)

        again = rehearse()

        assert again.returncode == 0, out(again)
        assert dispatches(released, "publish-testpypi.yml") == [TESTPYPI_DISPATCH]
        assert f"A publish-testpypi.yml run of {TAG} is live ({TESTPYPI_RUN_URL}) - waiting for it, no second dispatch." in again.stdout
        assert held_by_testpypi(released) == recorded(released)

    def test_a_refused_name_says_that_the_version_is_spent_on_testpypi_and_pypi_is_unaffected(
        self, released: World, rehearse,
    ) -> None:
        released.configure(testpypi_runs=["failure"], testpypi_run_log=["400 File already exists"])

        done = rehearse()

        assert done.returncode == 1
        assert "ERROR: TestPyPI refused a name already used: this version is spent on TestPyPI, PyPI is unaffected" in done.stderr
        assert RESUME in done.stderr
        assert "did not succeed" in done.stderr
        assert released.pypi() == {}
        assert dispatches(released, "publish.yml") == [PYPI_DISPATCH]

    def test_a_cancelled_run_is_a_failure_that_names_the_resume_command(self, released: World, rehearse) -> None:
        released.configure(testpypi_runs=["in_progress", "cancelled"])

        done = rehearse()

        assert done.returncode == 1
        assert "did not succeed" in done.stderr
        assert RESUME in done.stderr
        assert dispatches(released, "publish-testpypi.yml") == [TESTPYPI_DISPATCH]

    def test_a_dispatch_that_github_refuses_stops_with_the_resume_command(self, released: World, rehearse) -> None:
        released.configure(gh_fail_on={"testpypi-dispatch": 1})

        done = rehearse()

        assert done.returncode == 1
        assert RESUME in done.stderr
        assert dispatches(released, "publish-testpypi.yml") == []

    def test_a_listing_that_lags_behind_the_green_run_is_read_again(self, released: World, rehearse) -> None:
        released.configure(testpypi_cdn_lag=2)

        done = rehearse()

        assert done.returncode == 0, out(done)
        assert "TestPyPI does not list all files of 0.2.0 yet" in done.stdout
        assert held_by_testpypi(released) == recorded(released)

    def test_files_that_testpypi_serves_with_other_bytes_are_not_the_rehearsal_s_success(
        self, released: World, rehearse,
    ) -> None:
        released.configure(testpypi_effect="wrong_bytes")

        done = rehearse()

        assert done.returncode == 1
        assert "spent on TestPyPI" in done.stderr
        assert LAST_LINE not in done.stdout
