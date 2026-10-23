"""``publish_release`` dispatches the publish workflow only after a passing guard, once, and the waits keep their own bounds.

The dispatch of ``publish.yml`` is the one step of a release that cannot be undone: PyPI refuses a second upload of a file
name for good. The function text of ``scripts/lib/release-flow.sh`` is run here, as written, in a bash where ``rf_py`` (the
checks of ``scripts/release``) answers from a script and logs what it was asked, ``date`` and ``sleep`` are a clock the test
moves, and ``rf_load_pypi`` and ``rf_pypi_state`` are the readings of PyPI under test. What the stubs cannot show (the guard's
five conditions, the workflow, the fake PyPI) is run in ``test_release_guard_*.py`` and ``test_release_pipeline*.py``.

* ``publish_release``: a guard that refuses or cannot run ends the script through ``rf_stop`` before anything else is asked.
* ``rf_dispatch_publish``: ``publish-state`` live, recent or none gives 0, 0 or 1 dispatches; a branch named like the tag
  stops a dispatch, and a recent run starts the clock of the PyPI wait at the end of that run.
* ``rf_wait_publish_run``: the exit codes of ``publish-wait`` (0 continues, 1, 3 and 2 stop through ``rf_stop_dispatched``
  with the run's URL).
* ``rf_wait_pypi``: the loop that reads PyPI until it lists the files, within a window of its own (``RF_PYPI_LAG_S`` from the
  green run) that the deadline of the other waits does not shorten. A read that fails counts as a read that listed nothing.
"""

import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

from tests.release_support.script import run_functions

FUNCTIONS = (
    "rf_stop",
    "rf_stop_dispatched",
    "rf_set_deadline",
    "rf_wait_publish_run",
    "rf_dispatch_publish",
    "rf_require_no_branch_of_tag",
    "rf_wait_pypi",
    "publish_release",
)
URL = "https://github.example/run/publish-1000"

#: the scripted answers of ``rf_py``, the clock and the readings of PyPI; each is read from the environment of the run
HARNESS = r"""
log() { echo "$*" >> "$LOG"; }
rf_py() {
  log "py $*"
  case "$1 $2" in
    "guard check") return "${GUARD_RC:-0}" ;;
    "guard publish-state") printf '%s' "${STATE_OUT-none}"; return "${STATE_RC:-0}" ;;
    "guard publish-dispatch") printf '%s' "${DISPATCH_OUT-1000	__URL__}"; return "${DISPATCH_RC:-0}" ;;
    "guard publish-wait") return "${WAIT_RC:-0}" ;;
  esac
}
date() { cat "$CLOCK"; }
sleep() { log "sleep $1"; echo $(( $(cat "$CLOCK") + ${1%.*} )) > "$CLOCK"; }
rf_net() { log "net $*"; printf '%s' "${BRANCH_OUT-}"; return "${NET_RC:-0}"; }
rf_try_load_pypi() {
  local next
  next="$(head -n 1 "$LOADS")"
  [[ -z "$next" ]] || sed -i 1d "$LOADS"
  log "load"
  return "${next:-0}"
}
rf_pypi_state() {
  local next
  next="$(head -n 1 "$STATES")"
  [[ -z "$next" ]] || sed -i 1d "$STATES"
  log "state ${next:-4}"
  echo "a.whl"
  return "${next:-4}"
}
GH_REPO=o/r RELEASE_SHA=abc TAG=v1.2.3 VERSION=1.2.3 PKG=axmem RELEASE_REMOTE=axiumine REPO_ROOT=/r
RF_TMP=/tmp/rf MANIFEST=/r/release-digests.json PUBLISH_WORKFLOW=publish.yml PUBLISH_ENVIRONMENT=pypi
PYPI_JSON_URL=https://pypi.org/pypi/axmem/json
RF_PYPI_LAG_S="${LAG:-90}" RELEASE_POLL_INTERVAL_S=30 RELEASE_WAIT_TIMEOUT_S=600
RF_DEADLINE="${DEADLINE-}" RF_GREEN_AT="${GREEN_AT-}"
"""


class Ran(NamedTuple):
    """What one run did: the process, the calls it logged and the globals the function left behind."""

    done: subprocess.CompletedProcess[str]
    log: list[str]

    def calls(self, prefix: str) -> list[str]:
        """The logged lines that start with ``prefix``."""
        return [line for line in self.log if line.startswith(prefix)]

    @property
    def dispatches(self) -> int:
        """How many times ``publish-dispatch`` was asked: each one starts a workflow run."""
        return len(self.calls("py guard publish-dispatch"))

    def final(self, name: str) -> str:
        """The value ``name`` had when the run ended without a stop (printed as ``name=value``)."""
        (line,) = [ln for ln in self.done.stdout.splitlines() if ln.startswith(f"{name}=")]
        return line.partition("=")[2]


def _run(
    tmp_path: Path, call: str, states: str = "", clock: int = 1000, loads: str = "", **env: str,
) -> Ran:
    """Run ``call`` with the real ``FUNCTIONS`` and the stubs; ``env`` scripts the stubs (see ``HARNESS``)."""
    log, states_file, clock_file = tmp_path / "calls.log", tmp_path / "states", tmp_path / "clock"
    loads_file = tmp_path / "loads"
    loads_file.write_text(loads, encoding="utf-8")
    states_file.write_text(states, encoding="utf-8")
    clock_file.write_text(f"{clock}\n", encoding="utf-8")
    program = f"{HARNESS.replace('__URL__', URL)}\n{call}\n"
    done = run_functions(
        FUNCTIONS,
        program,
        {
            "PATH": "/usr/bin:/bin",
            "LOG": str(log),
            "CLOCK": str(clock_file),
            "STATES": str(states_file),
            "LOADS": str(loads_file),
            **env,
        },
        tmp_path,
    )
    return Ran(done, log.read_text(encoding="utf-8").splitlines() if log.exists() else [])


class TestAGuardThatRefusesOrCannotRun:
    @pytest.mark.parametrize("guard", ["1", "2", "127"])
    def test_it_stops_the_release_through_rf_stop_before_anything_else_is_asked(
        self, guard: str, tmp_path: Path,
    ) -> None:
        ran = _run(tmp_path, "publish_release", GUARD_RC=guard)

        assert ran.done.returncode == 1
        assert "STOPPED: the release guard refused the publication" in ran.done.stderr
        assert "Nothing was uploaded to PyPI" in ran.done.stderr
        assert "bash scripts/release-pypi.sh 1.2.3" in ran.done.stderr
        assert [line.split()[:3] for line in ran.log] == [["py", "guard", "check"]]
        assert ran.dispatches == 0

    def test_a_guard_that_passes_is_asked_about_this_release_and_the_signed_files_first(
        self, tmp_path: Path,
    ) -> None:
        ran = _run(tmp_path, "publish_release", STATE_OUT="recent\t7\thttps://x/7\t5")

        assert ran.done.returncode == 0, ran.done.stderr
        assert ran.log[0] == (
            "py guard check --repo o/r --sha abc --tag v1.2.3 --root /r --remote axiumine --dist /tmp/rf/signed"
        )
        assert ran.log[1].startswith("py guard publish-state --repo o/r --sha abc --tag v1.2.3 --lag 90")


class TestThePublishState:
    def test_none_dispatches_once_finds_the_run_and_waits_for_it(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_dispatch_publish", STATE_OUT="none")

        assert ran.done.returncode == 0, ran.done.stderr
        assert ran.dispatches == 1
        assert [line.split()[2] for line in ran.calls("py guard publish-")] == [
            "publish-state", "publish-dispatch", "publish-wait",
        ]
        assert f"Dispatched: {URL}" in ran.done.stdout
        assert "--run 1000" in ran.calls("py guard publish-wait")[0]

    def test_a_live_run_is_waited_for_and_no_second_one_is_dispatched(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_dispatch_publish", STATE_OUT="live\t77\thttps://x/77")

        assert ran.done.returncode == 0, ran.done.stderr
        assert ran.dispatches == 0
        (waited,) = ran.calls("py guard publish-wait")
        assert "--run 77" in waited
        assert "is live (https://x/77)" in ran.done.stdout

    def test_a_recent_green_run_dispatches_nothing_and_starts_the_clock_of_the_pypi_wait(
        self, tmp_path: Path,
    ) -> None:
        ran = _run(
            tmp_path,
            'rf_dispatch_publish; echo "RF_GREEN_AT=$RF_GREEN_AT"',
            clock=4321,
            STATE_OUT="recent\t5\thttps://x/5\t40",
        )

        assert ran.done.returncode == 0, ran.done.stderr
        assert ran.dispatches == 0
        assert ran.calls("py guard publish-wait") == []
        assert ran.final("RF_GREEN_AT") == "4281", "the clock started when that run ended, 40 s before now"

    @pytest.mark.parametrize("age", ["", "-", "soon", "1.5"])
    def test_a_recent_run_whose_age_cannot_be_read_stops_the_release(self, tmp_path: Path, age: str) -> None:
        ran = _run(tmp_path, "rf_dispatch_publish", STATE_OUT=f"recent\t5\thttps://x/5\t{age}")

        assert ran.done.returncode == 1
        assert "the age of the publish.yml run https://x/5 could not be read" in ran.done.stderr
        assert ran.dispatches == 0

    def test_a_branch_named_like_the_tag_stops_a_dispatch(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_dispatch_publish", STATE_OUT="none\t-\t-\t-", BRANCH_OUT="abc\trefs/heads/v1.2.3\n")

        assert ran.done.returncode == 1
        assert "STOPPED: a branch named v1.2.3 exists on axiumine" in ran.done.stderr
        assert "Nothing was uploaded to PyPI" in ran.done.stderr
        assert ran.dispatches == 0
        assert ran.calls("net") == ["net git -C /r ls-remote --heads axiumine refs/heads/v1.2.3"]

    def test_a_remote_that_cannot_be_asked_about_branches_stops_a_dispatch(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_dispatch_publish", STATE_OUT="none", NET_RC="124")

        assert ran.done.returncode == 1
        assert "STOPPED: git ls-remote axiumine failed" in ran.done.stderr
        assert ran.dispatches == 0

    def test_a_live_or_recent_run_does_not_ask_about_branches(self, tmp_path: Path) -> None:
        live = _run(tmp_path, "rf_dispatch_publish", STATE_OUT="live\t77\thttps://x/77")
        recent = _run(tmp_path, "rf_dispatch_publish", STATE_OUT="recent\t5\thttps://x/5\t0")

        assert live.calls("net") == recent.calls("net") == []

    @pytest.mark.parametrize(
        ("kind", "dispatches"), [("live\t1\tu", 0), ("recent\t1\tu\t0", 0), ("none", 1)],
        ids=["live", "recent", "none"],
    )
    def test_the_number_of_dispatches_follows_the_state(
        self, tmp_path: Path, kind: str, dispatches: int,
    ) -> None:
        assert _run(tmp_path, "rf_dispatch_publish", STATE_OUT=kind).dispatches == dispatches

    def test_a_state_that_cannot_be_read_stops_the_release_before_a_dispatch(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_dispatch_publish", STATE_RC="2")

        assert ran.done.returncode == 1
        assert "STOPPED: could not read the publish.yml runs of v1.2.3" in ran.done.stderr
        assert "Nothing was uploaded to PyPI" in ran.done.stderr
        assert ran.dispatches == 0

    def test_a_state_nobody_knows_stops_the_release_before_a_dispatch(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_dispatch_publish", STATE_OUT="maybe\t1\tu")

        assert ran.done.returncode == 1
        assert "could not be told apart (got 'maybe')" in ran.done.stderr
        assert ran.dispatches == 0

    @pytest.mark.parametrize("rc", ["1", "2"])
    def test_a_dispatch_that_fails_or_whose_run_is_not_seen_stops_without_waiting(
        self, tmp_path: Path, rc: str,
    ) -> None:
        ran = _run(tmp_path, "rf_dispatch_publish", STATE_OUT="none", DISPATCH_RC=rc)

        assert ran.done.returncode == 1
        assert "STOPPED: the publish.yml dispatch of v1.2.3 failed or its run was not seen in time" in ran.done.stderr
        assert "may be live or done" in ran.done.stderr
        assert ran.dispatches == 1
        assert ran.calls("py guard publish-wait") == []


class TestTheWaitForTheRun:
    def test_success_continues_and_starts_the_clock_of_the_pypi_wait(self, tmp_path: Path) -> None:
        ran = _run(
            tmp_path, f'rf_wait_publish_run 9 {URL}; echo "RF_GREEN_AT=$RF_GREEN_AT"', clock=555, WAIT_RC="0",
        )

        assert ran.done.returncode == 0, ran.done.stderr
        assert ran.final("RF_GREEN_AT") == "555"

    @pytest.mark.parametrize(
        ("code", "reason"),
        [
            ("1", f"STOPPED: the publish.yml run {URL} did not succeed"),
            ("3", f"STOPPED: the publish.yml run did not finish before the deadline (RELEASE_WAIT_TIMEOUT_S=600): {URL}"),
            ("2", f"STOPPED: GitHub could not be asked for the publish.yml run {URL}"),
        ],
        ids=["red", "deadline", "error"],
    )
    def test_anything_but_success_stops_with_the_run_url_and_the_way_back(
        self, tmp_path: Path, code: str, reason: str,
    ) -> None:
        ran = _run(tmp_path, f"rf_wait_publish_run 9 {URL}", WAIT_RC=code)

        assert ran.done.returncode == 1
        assert reason in ran.done.stderr
        assert "may be live or done" in ran.done.stderr
        assert "Nothing was uploaded" not in ran.done.stderr, "the run may have uploaded: it is not rf_stop"

    def test_the_deadline_is_set_once_and_handed_to_the_wait(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, f"rf_wait_publish_run 9 {URL}; rf_wait_publish_run 9 {URL}", clock=1000)

        waits = ran.calls("py guard publish-wait")
        assert len(waits) == 2
        assert all("--deadline 1600 --interval 30" in line for line in waits)

    def test_the_deadline_already_set_is_not_moved(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, f"rf_wait_publish_run 9 {URL}", clock=1000, DEADLINE="1200")

        assert "--deadline 1200 " in ran.calls("py guard publish-wait")[0]


class TestTheWaitForPypi:
    def test_a_cdn_that_lags_is_read_again_until_it_lists_the_files(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_wait_pypi", states="4\n5\n4\n0\n", GREEN_AT="1000")

        assert ran.done.returncode == 0, ran.done.stderr
        assert ran.calls("load") == ["load"] * 4
        assert ran.calls("sleep") == ["sleep 30"] * 3
        assert "PyPI lists every file of 1.2.3 with the manifest's digests." in ran.done.stdout

    def test_files_listed_at_the_first_read_need_no_wait(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_wait_pypi", states="0\n", GREEN_AT="1000")

        assert ran.done.returncode == 0, ran.done.stderr
        assert ran.calls("sleep") == []

    def test_beyond_the_window_it_stops_saying_the_run_succeeded_and_pypi_does_not_list_the_files(
        self, tmp_path: Path,
    ) -> None:
        ran = _run(tmp_path, "rf_wait_pypi", states="", GREEN_AT="1000")

        assert ran.done.returncode == 1
        assert "STOPPED: the publish run succeeded but PyPI does not list the files yet (90 s since the green run)" in (
            ran.done.stderr
        )
        assert "may be live or done" in ran.done.stderr
        assert len(ran.calls("load")) == 4, "reads at 0, 30, 60 and 90 s: the last one is at the end of the window"
        assert len(ran.calls("sleep")) == 3

    def test_a_listing_at_the_last_read_of_the_window_still_counts(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_wait_pypi", states="4\n4\n4\n0\n", GREEN_AT="1000")

        assert ran.done.returncode == 0, ran.done.stderr

    def test_without_a_green_clock_the_window_starts_when_the_wait_does(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_wait_pypi", clock=5000, states="")

        assert ran.done.returncode == 1
        assert len(ran.calls("load")) == 4

    def test_the_window_is_not_the_deadline_of_the_other_waits(self, tmp_path: Path) -> None:
        """A deadline long past stops no read of PyPI, and the wait does not move it."""
        ran = _run(
            tmp_path,
            'rf_wait_pypi; echo "RF_DEADLINE=$RF_DEADLINE"',
            states="4\n4\n4\n0\n",
            GREEN_AT="1000",
            DEADLINE="1001",
        )

        assert ran.done.returncode == 0, ran.done.stderr
        assert ran.calls("sleep") == ["sleep 30"] * 3
        assert ran.final("RF_DEADLINE") == "1001"

    def test_a_deadline_far_off_does_not_stretch_the_window(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_wait_pypi", states="", GREEN_AT="1000", DEADLINE="999999")

        assert ran.done.returncode == 1
        assert len(ran.calls("load")) == 4

    def test_the_window_is_the_one_the_script_sets(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_wait_pypi", states="", GREEN_AT="1000", LAG="300")

        assert ran.done.returncode == 1
        assert "(300 s since the green run)" in ran.done.stderr
        assert len(ran.calls("load")) == 11

    def test_other_bytes_on_pypi_end_it_at_once_as_a_spent_version(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_wait_pypi", states="4\n1\n", GREEN_AT="1000")

        assert ran.done.returncode == 1
        assert "ERROR: axmem 1.2.3 is on PyPI with other bytes than the manifest's" in ran.done.stderr
        assert "that version is spent" in ran.done.stderr
        assert "a.whl" in ran.done.stderr, "the lines the state printed are shown"
        assert ran.calls("sleep") == ["sleep 30"], "no further read after the state 1"

    def test_a_read_that_fails_is_a_read_that_listed_nothing_and_is_tried_again(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_wait_pypi", loads="0\n1\n1\n0\n", states="4\n0\n", GREEN_AT="1000")

        assert ran.done.returncode == 0, ran.done.stderr
        assert ran.calls("load") == ["load"] * 4
        assert ran.calls("state") == ["state 4", "state 0"], "a failed read is not asked what it holds"
        assert ran.done.stderr.count("WARNING: could not read https://pypi.org/pypi/axmem/json") == 2

    def test_reads_that_keep_failing_end_it_like_absence_with_the_command_that_resumes(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_wait_pypi", loads="1\n" * 10, GREEN_AT="1000")

        assert ran.done.returncode == 1
        assert "STOPPED: the publish run succeeded but PyPI does not list the files yet" in ran.done.stderr
        assert "bash scripts/release-pypi.sh 1.2.3" in ran.done.stderr
        assert ran.calls("state") == []

    def test_an_answer_that_cannot_be_told_is_tried_again_not_taken_for_a_verdict(self, tmp_path: Path) -> None:
        ran = _run(tmp_path, "rf_wait_pypi", states="2\n0\n", GREEN_AT="1000")

        assert ran.done.returncode == 0, ran.done.stderr
        assert "the answer of PyPI could not be read as the project's JSON" in ran.done.stderr
        assert ran.calls("sleep") == ["sleep 30"]
