# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""``guard publish-state``, ``publish-dispatch`` and ``publish-wait``: finding, starting and following the ``publish.yml`` run.

``publish.yml`` runs only when the release script dispatches it on the tag. The script asks first whether a run of that tag at
that commit is live (wait for it) or green and recent (PyPI's listing may lag: dispatch nothing), then dispatches and finds the
run it started by its id, then follows it. The fake ``gh`` plays the runs from ``publish_runs`` (the run a dispatch starts) and
``publish_existing`` (runs that are there before), and logs the dispatch it accepted; it refuses a dispatch that is not exactly
``gh workflow run publish.yml --ref T -f tag=T`` on a tag the remote has.
"""

import time

import pytest

from scripts.release import guard, publish
from tests.integration.release_guard_world import REPO, TAG, World

LAG = 1800
URL = "https://github.example/run/publish-"


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


@pytest.fixture
def run(world, capsys):
    """``run(command, *more)``: ``guard <command>`` of this tag and commit; ``(code, out, err)``."""

    def call(command, *more, tag=TAG, sha=""):
        argv = [command, "--repo", REPO, "--sha", sha or world.sha, "--tag", tag, "--root", str(world.repo), *more]
        code = int(guard.main(argv))
        seen = capsys.readouterr()
        return code, seen.out, seen.err

    return call


@pytest.fixture
def begin(world, run):
    """``begin(**scenario)``: script the fake and let it list the runs once, so the ``publish_existing`` runs exist."""

    def start(**scenario):
        world.script(**scenario)
        run("publish-state", "--lag", str(LAG))

    return start


def _state(run, lag=LAG):
    code, out, _ = run("publish-state", "--lag", str(lag))
    assert code == 0
    return out.rstrip("\n").split("\t")


def _wait(run, number="1000", *more, deadline=None):
    stop = str(deadline if deadline is not None else time.time() + 30)
    return run("publish-wait", "--run", number, "--deadline", stop, "--interval", "0.01", *more)


# --- publish-state --------------------------------------------------------------------------------


def test_no_run_of_the_tag_is_none(world, run):
    world.script()

    assert _state(run) == ["none", "-", "-", "-"]


@pytest.mark.parametrize("state", ["queued", "waiting", "in_progress"])
def test_a_run_that_is_not_completed_is_live(world, run, state):
    world.script(publish_existing=[{"state": state}])

    assert _state(run) == ["live", "1000", f"{URL}1000", "-"]  # a live run has no age


def test_a_success_younger_than_the_lag_is_recent(world, run):
    world.script(publish_existing=[{"state": "success", "age_s": 600}])

    kind, number, url, age = _state(run)

    assert (kind, number, url) == ("recent", "1000", f"{URL}1000")
    assert 599 <= int(age) <= 605, "the age of the end of the run, in whole seconds: the PyPI wait counts from it"


def test_the_lag_boundary_counts_the_age_of_the_end_of_the_run(world, run):
    world.script(publish_existing=[{"state": "success", "age_s": LAG - 20}])
    assert _state(run)[0] == "recent"

    world.script(publish_existing=[{"state": "success", "age_s": LAG + 20}])
    assert _state(run)[0] == "none"

    world.script(publish_existing=[{"state": "success", "age_s": 60}])
    assert _state(run, lag=30)[0] == "none"
    assert _state(run, lag=0)[0] == "none"


@pytest.mark.parametrize("state", ["failure", "cancelled"])
def test_a_run_that_did_not_succeed_is_none_however_recent(world, run, state):
    world.script(publish_existing=[{"state": state, "age_s": 5}])

    assert _state(run)[0] == "none"


def test_a_live_run_comes_before_a_recent_one_and_the_newest_of_a_kind_is_named(world, run):
    world.script(
        publish_existing=[
            {"state": "success", "age_s": 10},
            {"state": "in_progress"},
            {"state": "queued"},
        ],
    )

    assert _state(run)[:2] == ["live", "1002"]

    world.script(publish_existing=[{"state": "success", "age_s": 10}, {"state": "success", "age_s": 20}])
    assert _state(run)[:2] == ["recent", "1001"]


@pytest.mark.parametrize(
    "row",
    [{"event": "push"}, {"headBranch": "v9.9.9"}, {"headSha": "0" * 40}],
)
def test_a_run_that_reads_as_another_event_tag_or_commit_is_ignored(world, run, row):
    world.script(publish_existing=[{"state": "in_progress", "row": row}, {"state": "success", "age_s": 5, "row": row}])

    assert _state(run) == ["none", "-", "-", "-"]


def test_a_time_that_cannot_be_read_is_an_old_run(world, run):
    world.script(publish_existing=[{"state": "success", "age_s": 5}], publish_row={"updatedAt": "yesterday"})

    assert _state(run)[0] == "none"


def test_a_gh_that_cannot_list_is_exit_two(world, run):
    world.script(fail="gh: HTTP 502")

    code, out, err = run("publish-state", "--lag", str(LAG))

    assert (code, out) == (2, "")
    assert "ERROR: " in err


@pytest.mark.parametrize("lag", ["-1", "soon", "nan"])
def test_a_lag_that_is_not_a_number_of_seconds_is_refused_by_the_parser(run, lag):
    with pytest.raises(SystemExit) as stop:
        run("publish-state", "--lag", lag)
    assert stop.value.code == 2


# --- publish-dispatch -----------------------------------------------------------------------------


def _dispatch(run, *more, **where):
    return run("publish-dispatch", "--timeout", "5", "--interval", "0.01", *more, **where)


def test_the_dispatch_is_exactly_the_workflow_on_the_tag_with_the_tag_as_its_input(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(publish, "run_text", lambda argv, **_k: seen.append(argv))
    runs = iter([[], [{"databaseId": 7, "url": "u7"}]])
    monkeypatch.setattr(publish, "publish_runs", lambda *_a: next(runs))
    args = guard._parser().parse_args(["publish-dispatch", "--repo", "o/r", "--sha", "abc", "--tag", "v1.0.0"])

    assert publish.cmd_publish_dispatch(args) == 0

    assert seen == [["gh", "workflow", "run", "publish.yml", "-R", "o/r", "--ref", "v1.0.0", "-f", "tag=v1.0.0"]]
    assert capsys.readouterr().out == "7\tu7\n"


def test_the_run_the_dispatch_started_is_printed_with_its_url(world, run):
    world.script(publish_runs=["queued", "success"])

    code, out, err = _dispatch(run)

    assert (code, out, err) == (0, f"1000\t{URL}1000\n", "")
    assert f"gh workflow-run publish.yml tag={TAG}" in world.events()
    assert not [line for line in world.events() if "refused" in line]


def test_runs_that_were_there_before_are_not_taken_for_the_new_one(world, run):
    world.script(publish_existing=[{"state": "failure", "age_s": 900}, {"state": "success", "age_s": 600}])
    run("publish-state", "--lag", "1")

    code, out, _ = _dispatch(run)

    assert (code, out) == (0, f"1002\t{URL}1002\n")


def test_a_run_that_shows_in_the_list_late_is_waited_for(world, run):
    world.script(publish_hidden_lists=3)

    code, out, _ = _dispatch(run)

    assert (code, out) == (0, f"1000\t{URL}1000\n")


def test_a_run_that_does_not_show_in_time_is_exit_one(world, run):
    world.script(publish_hidden_lists=10**6)

    code, out, err = run("publish-dispatch", "--timeout", "0.3", "--interval", "0.05")

    assert (code, out) == (1, "")
    assert f"ERROR: no publish.yml run of {TAG} at {world.sha[:8]} appeared in time" in err
    assert f"gh workflow-run publish.yml tag={TAG}" in world.events()


def test_a_dispatch_gh_refuses_is_exit_two_and_starts_nothing(world, run):
    world.script()

    code, out, err = _dispatch(run, tag="v9.9.9")

    assert (code, out) == (2, "")
    assert "ERROR: " in err
    assert "gh workflow-run publish.yml tag=v9.9.9" not in world.events()
    assert any("workflow-run-refused" in line for line in world.events())


def test_a_gh_that_cannot_list_before_the_dispatch_is_exit_two_and_dispatches_nothing(world, run):
    world.script(fail="gh: HTTP 502")

    assert _dispatch(run)[0] == 2
    assert not (world.state / "events.log").exists()


def test_the_newest_of_several_new_runs_is_the_one_printed(monkeypatch, capsys):
    monkeypatch.setattr(publish, "run_text", lambda argv, **_k: None)
    rows = iter([[], [{"databaseId": 8, "url": "u8"}, {"databaseId": 9, "url": "u9"}]])
    monkeypatch.setattr(publish, "publish_runs", lambda *_a: next(rows))
    args = guard._parser().parse_args(["publish-dispatch", "--repo", "o/r", "--sha", "abc", "--tag", "v1.0.0"])

    assert publish.cmd_publish_dispatch(args) == 0

    assert capsys.readouterr().out == "9\tu9\n"


# --- publish-wait: the course of a run ------------------------------------------------------------


def test_each_change_of_status_is_printed_once_until_success(begin, run):
    begin(publish_existing=[{"script": ["queued", "queued", "in_progress", "in_progress", "success"]}])

    code, out, err = _wait(run)

    assert code == 0
    assert out.splitlines() == [
        f"publish.yml run 1000 is queued — {URL}1000",
        f"publish.yml run 1000 is in_progress — {URL}1000",
        f"publish.yml run 1000 completed: success — {URL}1000",
    ]
    assert err == ""


def test_a_waiting_run_says_who_must_approve_the_deployment(begin, run):
    begin(publish_existing=[{"script": ["waiting", "waiting", "in_progress", "success"]}])

    code, out, _ = _wait(run)

    assert code == 0
    assert out.splitlines()[:2] == [
        f"publish.yml run 1000 is waiting — {URL}1000",
        f"  a reviewer must approve the deployment to environment pypi: {URL}1000",
    ]
    assert out.count("must approve") == 1


def test_a_run_dispatched_by_the_fake_is_followed_from_its_dispatch_to_its_success(world, run):
    world.script(publish_runs=["queued", "waiting", "in_progress", "success"])
    _dispatch(run)

    code, out, _ = _wait(run)

    assert code == 0
    assert out.splitlines()[-1] == f"publish.yml run 1000 completed: success — {URL}1000"


def test_a_failed_run_is_exit_one_with_the_tail_of_its_log(begin, run):
    log = [f"line {n}" for n in range(1, 41)]
    begin(publish_existing=[{"script": ["in_progress", "failure"]}], run_log=log)

    code, out, err = _wait(run)

    assert code == 1
    assert "completed: failure" in out
    assert err.splitlines() == [f"  line {n}" for n in range(11, 41)]


@pytest.mark.parametrize("text", ["400 File already exists", "ERROR  Filename Was PREVIOUSLY USED for a file"])
def test_a_name_pypi_refused_as_used_says_that_the_version_is_spent(begin, run, text):
    begin(publish_existing=[{"script": ["failure"]}], run_log=["uploading", text])

    code, _, err = _wait(run)

    assert code == 1
    assert f"  {text}" in err
    assert err.splitlines()[-1] == "ERROR: PyPI refused a name already used: this version is spent"


def test_a_refusal_older_than_the_tail_gives_no_hint_and_a_log_without_it_none(begin, run):
    log = ["400 File already exists", *[f"line {n}" for n in range(30)]]
    begin(publish_existing=[{"script": ["failure"]}], run_log=log)

    code, _, err = _wait(run)

    assert code == 1
    assert "spent" not in err
    assert "already exists" not in err


def test_a_failure_with_no_log_is_exit_one_with_nothing_to_show(begin, run):
    begin(publish_existing=[{"script": ["failure"]}])

    code, _, err = _wait(run)

    assert code == 1
    assert "spent" not in err


# --- publish-wait: a run cancelled by a newer dispatch --------------------------------------------


def test_a_run_cancelled_while_a_newer_one_replaces_it_moves_the_wait_to_the_newer_one(begin, run):
    begin(
        publish_existing=[{"script": ["in_progress", "cancelled"]}],
        publish_successor=["in_progress", "success"],
    )

    code, out, err = _wait(run)

    assert code == 0
    assert out.splitlines() == [
        f"publish.yml run 1000 is in_progress — {URL}1000",
        f"publish.yml run 1000 completed: cancelled — {URL}1000",
        "run 1000 was cancelled; waiting for run 1001 of the same tag",
        f"publish.yml run 1001 is in_progress — {URL}1001",
        f"publish.yml run 1001 completed: success — {URL}1001",
    ]
    assert err == ""


def test_a_newer_run_that_is_already_completed_is_followed_too(begin, run):
    begin(publish_existing=[{"script": ["cancelled"]}], publish_successor=["failure"], run_log=["boom"])

    code, out, err = _wait(run)

    assert code == 1
    assert "run 1000 was cancelled; waiting for run 1001" in out
    assert "  boom" in err


def test_a_cancelled_run_with_no_other_run_is_a_failure(begin, run):
    begin(publish_existing=[{"script": ["in_progress", "cancelled"]}], run_log=["Canceling since a higher priority run"])

    code, out, err = _wait(run)

    assert code == 1
    assert "waiting for run" not in out
    assert "  Canceling since a higher priority run" in err


def test_an_older_run_is_not_a_successor(begin, run):
    begin(publish_existing=[{"state": "success", "age_s": 900}, {"script": ["cancelled"]}])

    code, out, _ = _wait(run, "1001")

    assert code == 1
    assert "waiting for run" not in out


def test_a_run_that_is_not_the_cancelled_one_and_is_live_is_a_successor_even_when_older(begin, run):
    begin(publish_existing=[{"state": "in_progress"}, {"script": ["cancelled"]}])

    code, out, _ = _wait(run, "1001", deadline=time.time() - 1)

    assert code == 3
    assert "run 1001 was cancelled; waiting for run 1000 of the same tag" in out


# --- publish-wait: what stops it ------------------------------------------------------------------


def test_a_deadline_that_passed_is_exit_three_naming_the_last_status(begin, run):
    begin(publish_existing=[{"script": ["waiting"]}])

    code, out, err = _wait(run, deadline=time.time() - 1)

    assert code == 3
    assert out.splitlines()[0] == f"publish.yml run 1000 is waiting — {URL}1000"
    assert err.startswith("ERROR: publish.yml did not finish before the deadline: publish.yml run 1000 is waiting")


@pytest.mark.parametrize(
    "row",
    [{"headSha": "0" * 40}, {"headBranch": "v9.9.9"}, {"event": "push"}],
)
def test_a_run_that_is_not_the_dispatch_of_this_tag_at_this_commit_is_exit_two(world, begin, run, row):
    begin(publish_existing=[{"script": ["success"], "row": row}])

    code, out, err = _wait(run)

    assert code == 2
    assert out == ""
    assert err.startswith("ERROR: run 1000 is (")
    assert err.endswith(f"not a dispatch of {TAG} at {world.sha}\n")


def test_a_run_gh_does_not_know_is_exit_two(world, run):
    world.script()

    code, _, err = _wait(run, "4242")

    assert code == 2
    assert "ERROR: " in err


@pytest.mark.parametrize("number", ["abc", "1x", "", " 1", "1.0"])
def test_a_run_id_that_is_not_digits_is_refused_by_the_parser(run, number):
    with pytest.raises(SystemExit) as stop:
        _wait(run, f"{number}")
    assert stop.value.code == 2
