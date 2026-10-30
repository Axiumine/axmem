# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""``guard testpypi-run``: finding or starting the ``publish-testpypi.yml`` run of the rehearsal, and following it.

The rehearsal workflow runs only when ``scripts/release-testpypi.sh`` dispatches it on the tag. The command adopts a live run of
that tag (a re-run after a Ctrl-C never dispatches a second), else dispatches exactly
``gh workflow run publish-testpypi.yml -R R --ref T -f tag=T``, finds the run it started by its id, and follows it: 0 when it
succeeded, 1 for any other end (a cancelled run too: the rehearsal is advisory and is run again by hand) or a run that did not
show in time, 2 when GitHub cannot be asked, 3 at the deadline. A run of ``publish.yml`` is never the run it looks for, and
the other way round: the two workflows are told apart by their name, in the list GitHub is asked for.
"""

import time

import pytest

from scripts.release import guard, testpypi
from scripts.release.facts import FactsError
from tests.integration.release_guard_world import REPO, TAG, World

URL = "https://github.example/run/testpypi-"
PYPI_URL = "https://github.example/run/publish-"


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


@pytest.fixture
def run(world, capsys):
    """``run(command, *more)``: ``guard <command>`` of this tag and commit; ``(code, out, err)``."""

    def call(command, *more, tag=TAG, sha=""):
        argv = [
            command,
            "--repo",
            REPO,
            "--sha",
            sha or world.sha,
            "--tag",
            tag,
            "--root",
            str(world.repo),
            *more,
        ]
        code = int(guard.main(argv))
        seen = capsys.readouterr()
        return code, seen.out, seen.err

    return call


def _rehearse(run, *more, deadline=None, **where):
    stop = str(deadline if deadline is not None else time.time() + 30)
    return run(
        "testpypi-run",
        "--deadline",
        stop,
        "--interval",
        "0.01",
        "--timeout",
        "5",
        *more,
        **where,
    )


def _dispatches(world):
    log = world.state / "events.log"
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return [line for line in lines if line.startswith("gh workflow-run ")]


# --- the dispatch ---------------------------------------------------------------------------------


def test_the_dispatch_is_exactly_the_rehearsal_workflow_on_the_tag_with_the_tag_as_its_input(
    monkeypatch, capsys,
):
    seen = []
    monkeypatch.setattr(testpypi, "run_text", lambda argv, **_k: seen.append(argv))
    rows = iter([[], [{"databaseId": 7, "url": "u7"}]])
    monkeypatch.setattr(testpypi, "publish_runs", lambda *_a: next(rows))
    monkeypatch.setattr(
        testpypi,
        "gh_json",
        lambda argv: {
            "databaseId": 7,
            "status": "completed",
            "conclusion": "success",
            "url": "u7",
            "headSha": "abc",
            "headBranch": "v1.0.0",
            "event": "workflow_dispatch",
        },
    )
    args = [
        "testpypi-run",
        "--repo",
        "o/r",
        "--sha",
        "abc",
        "--tag",
        "v1.0.0",
        "--deadline",
        str(time.time() + 30),
        "--interval",
        "0.01",
    ]

    assert guard.main(args) == 0

    assert seen == [
        [
            "gh",
            "workflow",
            "run",
            "publish-testpypi.yml",
            "-R",
            "o/r",
            "--ref",
            "v1.0.0",
            "-f",
            "tag=v1.0.0",
        ],
    ]
    assert capsys.readouterr().out.splitlines()[:2] == [
        "Dispatching publish-testpypi.yml on v1.0.0 (it runs in the environment testpypi, whose reviewers must approve)...",
        "Dispatched: u7",
    ]


def test_the_runs_are_asked_for_with_the_closed_key_of_the_rehearsal_index(monkeypatch):
    asked = []
    rows = iter([[], [{"databaseId": 1, "url": "u1"}]])
    monkeypatch.setattr(testpypi, "run_text", lambda argv, **_k: None)
    monkeypatch.setattr(
        testpypi, "publish_runs", lambda *call: asked.append(call) or next(rows),
    )
    monkeypatch.setattr(
        testpypi,
        "gh_json",
        lambda argv: {
            "databaseId": 1,
            "status": "completed",
            "conclusion": "success",
            "url": "u1",
            "headSha": "abc",
            "headBranch": "v1.0.0",
            "event": "workflow_dispatch",
        },
    )
    args = [
        "testpypi-run",
        "--repo",
        "o/r",
        "--sha",
        "abc",
        "--tag",
        "v1.0.0",
        "--deadline",
        str(time.time() + 30),
        "--interval",
        "0.01",
    ]

    assert guard.main(args) == 0

    assert asked
    assert set(asked) == {("o/r", "abc", "v1.0.0", "testpypi")}


def test_a_run_started_by_the_dispatch_is_followed_to_its_success_and_the_workflow_is_dispatched_once(
    world, run,
):
    world.script(testpypi_runs=["queued", "success"])

    code, out, err = _rehearse(run)

    assert (code, err) == (0, "")
    assert _dispatches(world) == [f"gh workflow-run publish-testpypi.yml tag={TAG}"]
    assert out.splitlines() == [
        f"Dispatching publish-testpypi.yml on {TAG} (it runs in the environment testpypi, whose reviewers must approve)...",
        f"Dispatched: {URL}2000",
        f"publish-testpypi.yml run 2000 is queued — {URL}2000",
        f"publish-testpypi.yml run 2000 completed: success — {URL}2000",
    ]
    assert not [line for line in world.events() if "refused" in line]


def test_a_waiting_run_says_that_a_reviewer_of_the_environment_testpypi_must_approve_it(
    world, run,
):
    world.script(testpypi_runs=["waiting", "waiting", "in_progress", "success"])

    code, out, _ = _rehearse(run)

    assert code == 0
    assert out.splitlines()[2:4] == [
        f"publish-testpypi.yml run 2000 is waiting — {URL}2000",
        f"  a reviewer must approve the deployment to environment testpypi: {URL}2000",
    ]
    assert out.count("a reviewer must approve") == 1
    assert "environment pypi" not in out


def test_each_change_of_status_is_printed_once(world, run):
    world.script(
        testpypi_runs=["queued", "queued", "in_progress", "in_progress", "success"],
    )

    _, out, _ = _rehearse(run)

    assert [
        line
        for line in out.splitlines()
        if "run 2000" in line and "Dispatched" not in line
    ] == [
        f"publish-testpypi.yml run 2000 is queued — {URL}2000",
        f"publish-testpypi.yml run 2000 is in_progress — {URL}2000",
        f"publish-testpypi.yml run 2000 completed: success — {URL}2000",
    ]


def test_a_run_that_shows_in_the_list_late_is_waited_for(world, run):
    world.script(testpypi_hidden_lists=3)

    code, out, _ = _rehearse(run)

    assert code == 0
    assert f"Dispatched: {URL}2000" in out


def test_a_run_that_does_not_show_in_time_is_exit_one_and_names_the_workflow(
    world, run,
):
    world.script(testpypi_hidden_lists=10**6)

    code, out, err = run(
        "testpypi-run",
        "--deadline",
        str(time.time() + 30),
        "--interval",
        "0.05",
        "--timeout",
        "0.3",
    )

    assert code == 1
    assert out.startswith("Dispatching publish-testpypi.yml on ")
    assert (
        f"ERROR: no publish-testpypi.yml run of {TAG} at {world.sha[:8]} appeared in time"
        in err
    )
    assert len(_dispatches(world)) == 1


def test_a_dispatch_gh_refuses_is_exit_two_and_starts_nothing(world, run):
    world.script()

    code, out, err = _rehearse(run, tag="v9.9.9")

    assert code == 2
    assert out.startswith("Dispatching publish-testpypi.yml on v9.9.9")
    assert "ERROR: " in err
    assert _dispatches(world) == []
    assert any("workflow-run-refused" in line for line in world.events())


def test_a_gh_that_cannot_list_is_exit_two_and_dispatches_nothing(world, run):
    world.script(fail="gh: HTTP 502")

    code, out, err = _rehearse(run)

    assert (code, out) == (2, "")
    assert "ERROR: " in err
    assert not (world.state / "events.log").exists()


# --- a run that is there already ------------------------------------------------------------------


def test_a_live_run_of_the_tag_is_adopted_and_no_second_one_is_dispatched(world, run):
    world.script(testpypi_existing=[{"script": ["in_progress", "success"]}])

    code, out, err = _rehearse(run)

    assert (code, err) == (0, "")
    assert _dispatches(world) == []
    assert out.splitlines()[0] == (
        f"A publish-testpypi.yml run of {TAG} is live ({URL}2000) - waiting for it, no second dispatch."
    )
    assert out.splitlines()[-1] == f"publish-testpypi.yml run 2000 completed: success — {URL}2000"


@pytest.mark.parametrize("state", ["queued", "waiting", "in_progress"])
def test_a_run_that_is_not_completed_is_live_and_is_followed(world, run, state):
    world.script(testpypi_existing=[{"script": [state, "success"]}])

    code, out, _ = _rehearse(run)

    assert code == 0
    assert out.startswith(f"A publish-testpypi.yml run of {TAG} is live")
    assert _dispatches(world) == []


def test_the_newest_of_several_live_runs_is_the_one_followed(world, run):
    world.script(
        testpypi_existing=[
            {"script": ["in_progress", "failure"]},
            {"script": ["in_progress", "success"]},
        ],
    )

    code, out, _ = _rehearse(run)

    assert code == 0
    assert f"({URL}2001)" in out.splitlines()[0]


@pytest.mark.parametrize("state", ["success", "failure", "cancelled"])
def test_a_completed_run_is_not_adopted_and_the_workflow_is_dispatched_again(
    world, run, state,
):
    world.script(
        testpypi_existing=[{"state": state, "age_s": 5}], testpypi_runs=["success"],
    )

    code, out, _ = _rehearse(run)

    assert code == 0
    assert len(_dispatches(world)) == 1
    assert f"Dispatched: {URL}2001" in out


@pytest.mark.parametrize(
    "row", [{"event": "push"}, {"headBranch": "v9.9.9"}, {"headSha": "0" * 40}],
)
def test_a_run_that_reads_as_another_event_tag_or_commit_is_not_adopted(
    world, run, row,
):
    world.script(
        testpypi_existing=[{"state": "in_progress", "row": row}],
        testpypi_runs=["success"],
    )

    code, out, _ = _rehearse(run)

    assert code == 0
    assert out.startswith("Dispatching publish-testpypi.yml")


def test_a_live_run_of_publish_yml_is_not_adopted_and_the_two_workflows_do_not_see_each_other(
    world, run, capsys,
):
    world.script(publish_existing=[{"state": "in_progress"}], testpypi_runs=["success"])

    code, out, _ = _rehearse(run)

    assert code == 0
    assert out.startswith("Dispatching publish-testpypi.yml")
    assert _dispatches(world) == [f"gh workflow-run publish-testpypi.yml tag={TAG}"]
    # and the state that the release to PyPI reads is its own run alone, with the rehearsal's run in the list
    code, shown, _ = run("publish-state", "--lag", "1800")
    assert code == 0
    assert shown.split("\t")[:3] == ["live", "1000", f"{PYPI_URL}1000"]


def test_a_live_run_of_the_rehearsal_is_not_a_live_run_of_publish_yml(world, run):
    world.script(testpypi_existing=[{"state": "in_progress"}])

    code, out, _ = run("publish-state", "--lag", "1800")

    assert (code, out) == (0, "none\t-\t-\t-\n")


def test_a_successful_run_of_the_rehearsal_is_not_a_recent_run_of_publish_yml(
    world, run,
):
    world.script(testpypi_existing=[{"state": "success", "age_s": 30}])

    assert run("publish-state", "--lag", "1800")[1] == "none\t-\t-\t-\n"


# --- the end of a run -----------------------------------------------------------------------------


def test_a_failed_run_is_exit_one_with_the_tail_of_its_log(world, run):
    log = [f"line {n}" for n in range(1, 41)]
    world.script(testpypi_runs=["in_progress", "failure"], testpypi_run_log=log)

    code, out, err = _rehearse(run)

    assert code == 1
    assert "completed: failure" in out
    assert err.splitlines() == [f"  line {n}" for n in range(11, 41)]


@pytest.mark.parametrize(
    "text",
    ["400 File already exists", "ERROR  Filename Was PREVIOUSLY USED for a file"],
)
def test_a_name_testpypi_refused_as_used_says_that_the_version_is_spent_on_testpypi_and_pypi_is_not(
    world, run, text,
):
    world.script(testpypi_runs=["failure"], testpypi_run_log=["uploading", text])

    code, _, err = _rehearse(run)

    assert code == 1
    assert f"  {text}" in err
    assert (
        err.splitlines()[-1]
        == "ERROR: TestPyPI refused a name already used: this version is spent on TestPyPI, PyPI is unaffected"
    )
    assert "release-superseded" not in err


def test_a_refusal_older_than_the_tail_gives_no_hint_and_a_log_without_it_none(
    world, run,
):
    log = ["400 File already exists", *[f"line {n}" for n in range(30)]]
    world.script(testpypi_runs=["failure"], testpypi_run_log=log)

    code, _, err = _rehearse(run)

    assert code == 1
    assert "spent" not in err


def test_a_failure_with_no_log_is_exit_one_with_nothing_to_show(world, run):
    world.script(testpypi_runs=["failure"])

    code, _, err = _rehearse(run)

    assert code == 1
    assert "spent" not in err


def test_a_cancelled_run_is_a_failure_and_its_successor_is_not_followed(world, run):
    world.script(
        testpypi_runs=["in_progress", "cancelled"],
        testpypi_successor=["in_progress", "success"],
        testpypi_run_log=["Canceling since a higher priority run"],
    )

    code, out, err = _rehearse(run)

    assert code == 1
    assert "completed: cancelled" in out
    assert "waiting for run" not in out
    assert "  Canceling since a higher priority run" in err


def test_a_deadline_that_passed_is_exit_three_naming_the_last_status(world, run):
    world.script(testpypi_runs=["waiting"])

    code, out, err = _rehearse(run, deadline=time.time() - 1)

    assert code == 3
    assert f"publish-testpypi.yml run 2000 is waiting — {URL}2000" in out.splitlines()
    assert err.startswith(
        "ERROR: publish-testpypi.yml did not finish before the deadline: publish-testpypi.yml run 2000 is waiting",
    )


@pytest.mark.parametrize(
    "row", [{"headSha": "0" * 40}, {"headBranch": "v9.9.9"}, {"event": "push"}],
)
def test_a_run_that_is_not_the_dispatch_of_this_tag_at_this_commit_is_exit_two(
    world, run, monkeypatch, row,
):
    world.script(testpypi_existing=[{"script": ["in_progress", "success"]}])
    shown = {
        "databaseId": 2000,
        "status": "completed",
        "conclusion": "success",
        "url": "u",
        "headSha": world.sha,
        "headBranch": TAG,
        "event": "workflow_dispatch",
        **row,
    }
    monkeypatch.setattr(testpypi, "gh_json", lambda argv: shown)

    code, out, err = _rehearse(run)

    assert code == 2
    assert out.startswith("A publish-testpypi.yml run of ")
    assert err.startswith("ERROR: run 2000 is (")
    assert err.endswith(f"not a dispatch of {TAG} at {world.sha}\n")


def test_a_gh_that_fails_while_the_run_is_followed_is_exit_two(world, run, monkeypatch):
    world.script(testpypi_existing=[{"script": ["queued", "success"]}])

    def broken(argv):
        raise FactsError("gh run view: HTTP 502: bad gateway")

    monkeypatch.setattr(testpypi, "gh_json", broken)

    code, _, err = _rehearse(run)

    assert code == 2
    assert err == "ERROR: gh run view: HTTP 502: bad gateway\n"
    assert _dispatches(world) == []


# --- the arguments --------------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--deadline", "--interval", "--timeout"])
@pytest.mark.parametrize("value", ["-1", "soon", "nan", "inf"])
def test_a_wait_that_is_not_a_number_of_seconds_is_refused_by_the_parser(
    run, flag, value,
):
    more = [] if flag == "--deadline" else ["--deadline", "1"]
    with pytest.raises(SystemExit) as stop:
        run("testpypi-run", flag, value, *more)
    assert stop.value.code == 2


def test_the_deadline_is_required(run):
    with pytest.raises(SystemExit) as stop:
        run("testpypi-run")
    assert stop.value.code == 2


def test_the_command_takes_no_option_that_names_an_index_a_workflow_or_an_environment(
    run,
):
    for option in ("--index", "--workflow", "--environment", "--host"):
        with pytest.raises(SystemExit) as stop:
            run("testpypi-run", "--deadline", "1", option, "pypi")
        assert stop.value.code == 2


def test_the_constants_of_the_module_are_the_row_of_the_table():
    from scripts.release import facts

    assert (testpypi.TESTPYPI_WORKFLOW, testpypi.TESTPYPI_ENVIRONMENT) == (
        "publish-testpypi.yml",
        "testpypi",
    )
    assert facts.INDEXES[
        "testpypi"
    ][2:] == (testpypi.TESTPYPI_WORKFLOW, testpypi.TESTPYPI_ENVIRONMENT)
