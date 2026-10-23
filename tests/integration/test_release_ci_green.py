"""A release is refused for a commit that CI has not fully passed.

``scripts.release.ci_green`` asks GitHub for every push-triggered run of the exact commit. These cases pin what is
green, red, pending and unjudgeable, the four answers of ``verdict`` that a wait relies on (stop on red, keep polling
on pending), the bounded wait with its deadline, and the exit code of each outcome. ``gh`` is replaced by a stub:
nothing here reaches the network.
"""

import json
import runpy
import subprocess
import sys

import pytest

from scripts.release import ci_green

REPO_SHA = ["--repo", "o/r", "--sha", "abc123def"]


def run(name, status="completed", conclusion="success"):
    return {
        "workflowName": name,
        "status": status,
        "conclusion": conclusion,
        "url": f"u/{name}",
        "databaseId": 7,
    }


def queued(name):
    return run(name, status="queued", conclusion="")


def fake_gh(monkeypatch, runs):
    """``gh`` answers with ``runs``; the returned list collects the argv of each call."""
    calls = []

    def _run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(runs), "")

    monkeypatch.setattr(ci_green.subprocess, "run", _run)
    return calls


def wait_args(sha="abc", *, deadline_in=60):
    return ["--repo", "o/r", "--sha", sha, "--wait", "--deadline", str(ci_green.time.time() + deadline_in)]


# --- what blocks ----------------------------------------------------------------------------------------------


def test_all_green_is_clean():
    assert ci_green.problems_with([run("Test"), run("Lint", conclusion="skipped")]) == []


def test_no_runs_is_not_green():
    assert "no CI runs" in ci_green.problems_with([])[0]


def test_a_pending_run_blocks():
    assert ci_green.problems_with([queued("E2E")]) == ["E2E: still queued"]


def test_failed_and_cancelled_runs_block_with_their_url():
    problems = ci_green.problems_with(
        [run("Test"), run("Build", conclusion="failure"), run("E2E", conclusion="cancelled")],
    )

    assert problems[0] == "Build: failure — u/Build"
    assert problems[1].startswith("E2E: cancelled (a newer push to main cancels the runs of an older commit)")
    assert problems[1].endswith("with 'gh run rerun 7' or hold new pushes — u/E2E")


# --- the exit code of each outcome ----------------------------------------------------------------------------


def test_green_exits_zero_and_asks_for_the_push_runs_of_the_exact_sha(monkeypatch, capsys):
    calls = fake_gh(monkeypatch, [run("Test")])

    assert ci_green.main(REPO_SHA) == 0

    argv = calls[0]
    assert argv[argv.index("--commit") + 1] == "abc123def"
    assert argv[argv.index("--event") + 1] == "push"
    assert "1 CI run(s) green" in capsys.readouterr().out


def test_red_exits_one_listing_the_blockers(monkeypatch, capsys):
    fake_gh(monkeypatch, [run("Build", conclusion="failure")])

    assert ci_green.main(REPO_SHA) == 1
    assert "Build: failure" in capsys.readouterr().err


def test_a_query_failure_exits_two(monkeypatch, capsys):
    def boom(argv, **_kwargs):
        raise subprocess.CalledProcessError(1, argv, stderr="gh: not logged in")

    monkeypatch.setattr(ci_green.subprocess, "run", boom)

    assert ci_green.main(REPO_SHA) == 2
    assert "not logged in" in capsys.readouterr().err


# --- red, pending, none and green -----------------------------------------------------------------------------


def test_no_run_is_none():
    assert ci_green.verdict([]) == "none"


def test_a_completed_failure_is_red_even_while_another_run_is_pending():
    runs = [run("Test", status="in_progress", conclusion=""), run("Lint", conclusion="failure")]

    assert ci_green.verdict(runs) == "red"


def test_a_cancelled_run_is_red():
    assert ci_green.verdict([run("E2E", conclusion="cancelled")]) == "red"


def test_an_unfinished_run_is_pending():
    assert ci_green.verdict([run("Test"), queued("E2E")]) == "pending"


def test_success_skipped_and_neutral_are_green():
    runs = [run("A"), run("B", conclusion="skipped"), run("C", conclusion="neutral")]

    assert ci_green.verdict(runs) == "green"


# --- nothing was verified -------------------------------------------------------------------------------------

SKIPPED = [run("Test", conclusion="skipped"), run("Lint", conclusion="neutral")]


def test_runs_that_were_all_skipped_or_neutral_are_a_problem():
    assert ci_green.problems_with(SKIPPED) == [
        "no run concluded success (every run was skipped or neutral) — nothing was verified",
    ]


def test_one_success_among_skipped_runs_is_enough():
    assert ci_green.problems_with([*SKIPPED, run("Build")]) == []


def test_runs_that_all_finished_and_none_succeeded_are_red_so_a_wait_stops():
    assert ci_green.verdict(SKIPPED) == "red"


def test_a_skipped_run_beside_a_pending_one_is_still_pending():
    assert ci_green.verdict([run("Test", conclusion="skipped"), queued("E2E")]) == "pending"


def test_main_exits_one_for_runs_that_were_all_skipped(monkeypatch, capsys):
    fake_gh(monkeypatch, SKIPPED)

    assert ci_green.main(REPO_SHA) == 1
    assert "nothing was verified" in capsys.readouterr().err


# --- what cannot be judged ------------------------------------------------------------------------------------


def test_as_many_runs_as_the_limit_cannot_be_judged_because_some_may_be_missing(monkeypatch, capsys):
    fake_gh(monkeypatch, [run(f"W{n}") for n in range(ci_green.RUN_LIMIT)])

    assert ci_green.main(REPO_SHA) == 2
    assert "the most this check reads" in capsys.readouterr().err


def test_one_run_fewer_than_the_limit_is_judged(monkeypatch):
    fake_gh(monkeypatch, [run(f"W{n}") for n in range(ci_green.RUN_LIMIT - 1)])

    assert ci_green.main(REPO_SHA) == 0


def test_a_gh_that_does_not_answer_is_exit_two_not_a_wait(monkeypatch, capsys):
    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(ci_green.subprocess, "run", hang)

    assert ci_green.main(REPO_SHA) == 2
    assert "could not query GitHub Actions" in capsys.readouterr().err


def test_gh_is_given_a_time_limit(monkeypatch):
    limits = []

    def spy(argv, **kwargs):
        limits.append(kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "[]", "")

    monkeypatch.setattr(ci_green.subprocess, "run", spy)
    ci_green.fetch_runs("o/r", "abc")

    assert limits == [ci_green.RUN_TIMEOUT_S]


@pytest.mark.parametrize("flag", ["--deadline", "--interval"])
@pytest.mark.parametrize("value", ["nan", "inf", "-1"])
def test_a_wait_that_is_not_a_finite_number_of_seconds_is_refused(capsys, flag, value):
    argv = ["--repo", "o/r", "--sha", "abc", "--wait", "--deadline", "1", "--interval", "1"]
    argv[argv.index(flag) + 1] = value

    with pytest.raises(SystemExit) as stop:
        ci_green.main(argv)

    assert stop.value.code == 2
    assert "seconds" in capsys.readouterr().err


# --- the branch filter ----------------------------------------------------------------------------------------


def test_the_branch_is_passed_to_gh_only_when_given(monkeypatch):
    calls = fake_gh(monkeypatch, [run("Test")])

    ci_green.fetch_runs("o/r", "abc")
    ci_green.fetch_runs("o/r", "abc", "main")

    assert "--branch" not in calls[0]
    assert calls[1][calls[1].index("--branch") + 1] == "main"


def test_main_passes_the_branch_on(monkeypatch):
    calls = fake_gh(monkeypatch, [run("Test")])

    assert ci_green.main(["--repo", "o/r", "--sha", "abc", "--branch", "main"]) == 0
    assert calls[0][calls[0].index("--branch") + 1] == "main"


# --- the wait -------------------------------------------------------------------------------------------------


def sequence(monkeypatch, answers):
    """``fetch_runs`` answers in turn (the last repeats) and the polls do not sleep; the list counts the calls."""
    seen = []

    def fetch(repo, sha, branch=None):
        seen.append(1)
        return answers[min(len(seen) - 1, len(answers) - 1)]

    monkeypatch.setattr(ci_green, "fetch_runs", fetch)
    monkeypatch.setattr(ci_green.time, "sleep", lambda _seconds: None)
    return seen


def test_it_polls_until_every_run_is_done(monkeypatch, capsys):
    pending = [run("Test"), queued("E2E")]
    seen = sequence(monkeypatch, [pending, pending, [run("Test"), run("E2E")]])
    deadline = str(ci_green.time.time() + 60)

    code = ci_green.main(["--repo", "o/r", "--sha", "abc12345", "--wait", "--deadline", deadline])

    assert code == 0
    assert len(seen) == 4, "green once is not the end: the same runs must be green on a second poll"
    assert capsys.readouterr().out.count("waiting for CI on abc12345: E2E: still queued") == 1


def test_a_workflow_that_starts_late_is_waited_for_not_missed(monkeypatch, capsys):
    """A commit starts its workflows over several seconds: the first poll shows only the fast ones."""
    early = [run("Test")]
    later = [run("Test"), run("Build", conclusion="failure")]
    seen = sequence(monkeypatch, [early, later])

    assert ci_green.main(wait_args()) == 1

    assert len(seen) == 2
    assert "Build: failure" in capsys.readouterr().err


def test_green_with_more_runs_than_before_starts_the_count_again(monkeypatch):
    one, two = [run("Test")], [run("Test"), run("Lint")]
    seen = sequence(monkeypatch, [one, two, two])

    assert ci_green.main(wait_args()) == 0
    assert len(seen) == 3


def test_a_red_run_stops_the_wait_at_once(monkeypatch, capsys):
    seen = sequence(monkeypatch, [[run("Test", conclusion="failure")]])

    assert ci_green.main(wait_args()) == 1

    assert len(seen) == 1
    assert "Test: failure" in capsys.readouterr().err


def test_the_deadline_is_exit_three(monkeypatch, capsys):
    sequence(monkeypatch, [[queued("Test")]])

    assert ci_green.main(wait_args(deadline_in=-1)) == 3
    assert "did not finish before the deadline" in capsys.readouterr().err


def test_no_run_at_all_is_waited_for_and_then_red(monkeypatch, capsys):
    seen = sequence(monkeypatch, [[], [], [run("Test")]])

    assert ci_green.main(wait_args()) == 0
    assert len(seen) == 4

    monkeypatch.setattr(ci_green, "NO_RUN_GRACE_S", 0)
    sequence(monkeypatch, [[]])

    assert ci_green.main(wait_args()) == 1
    assert "no CI runs recorded" in capsys.readouterr().err


def test_a_wait_without_a_deadline_is_refused(capsys):
    with pytest.raises(SystemExit) as stop:
        ci_green.main(["--repo", "o/r", "--sha", "abc", "--wait"])

    assert stop.value.code == 2
    assert "a wait has no unbounded form" in capsys.readouterr().err


def test_a_query_failure_while_waiting_is_exit_two(monkeypatch, capsys):
    def boom(repo, sha, branch=None):
        raise subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 502")

    monkeypatch.setattr(ci_green, "fetch_runs", boom)

    assert ci_green.main(wait_args()) == 2
    assert "HTTP 502" in capsys.readouterr().err


# --- the command line -----------------------------------------------------------------------------------------


def test_the_module_runs_as_a_plain_program(monkeypatch, capsys):
    fake_gh(monkeypatch, [run("Test")])
    monkeypatch.delitem(sys.modules, "scripts.release.ci_green")
    monkeypatch.setattr(sys, "argv", ["ci_green", *REPO_SHA])

    with pytest.raises(SystemExit) as stop:
        runpy.run_module("scripts.release.ci_green", run_name="__main__")

    assert stop.value.code == 0
    assert "1 CI run(s) green" in capsys.readouterr().out
