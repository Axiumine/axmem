"""The signing facts, part one: the tag's own run and the GitHub release as ``gh`` describes it.

Condition 4 of the release guard and the integrity check both ask ``facts.signing_problems``. These cases cover what it
reads first: the ``release.yml`` runs that the push of a tag started (another run of the same commit, from a second tag
or a dispatch, must never stand in for it or hide its red run), the shape of ``gh release view``, and the verdict over
the runs. ``test_release_facts_signing_b.py`` covers the bundles, the digests and the whole proof. The command runner is
replaced where the case is a shape of answer.
"""

import json
import subprocess

import pytest

from scripts.release import facts

TAG = "v1.0.0"


def _completed(code, out="", err=""):
    return subprocess.CompletedProcess(["x"], code, out, err)


def _run(status="completed", conclusion="success", created="2026-01-01", **fields):
    """A ``release.yml`` run row; ``fields`` (``event``, ``branch``, ``sha``) replace what the tag's own push has."""
    row = {
        "event": "push",
        "headBranch": fields.pop("branch", TAG),
        "headSha": fields.pop("sha", "abc"),
    }
    return {
        "status": status,
        "conclusion": conclusion,
        "createdAt": created,
        "url": f"u-{created}-{conclusion or status}",
        **row,
        **fields,
    }


# --- release_runs ---------------------------------------------------------------------------------


def test_the_runs_are_asked_for_by_the_push_of_the_tag_on_the_commit(monkeypatch):
    seen = []

    def fake(args):
        seen.append(args)
        return [_run()]

    monkeypatch.setattr(facts, "gh_json", fake)

    assert facts.release_runs("o/r", "abc", TAG) == [_run()]
    argv = seen[0]
    assert argv[argv.index("--workflow") + 1] == "release.yml"
    assert argv[argv.index("--commit") + 1] == "abc"
    assert argv[argv.index("--event") + 1] == "push"
    assert argv[argv.index("--branch") + 1] == TAG
    assert argv[argv.index("-R") + 1] == "o/r"
    assert {"event", "headBranch", "headSha"} <= set(argv[argv.index("--json") + 1].split(","))


def test_only_the_run_the_push_of_the_tag_started_is_kept(monkeypatch):
    own = _run(created="2026-01-01")
    rows = [
        _run(created="2026-01-02", branch="v1-hotfix"),
        _run(created="2026-01-03", event="workflow_dispatch", branch="main"),
        _run(created="2026-01-04", event="release"),
        _run(created="2026-01-05", sha="def"),
        own,
    ]
    monkeypatch.setattr(facts, "gh_json", lambda args: rows)

    assert facts.release_runs("o/r", "abc", TAG) == [own]


# --- release_view ---------------------------------------------------------------------------------

VIEW_ANSWER = json.dumps(
    {
        "isDraft": False,
        "assets": [
            {"name": "a", "size": 7, "state": "uploaded", "digest": "sha256:" + "AB" * 32},
            {"name": "b", "size": 0, "state": "starter"},
            {"name": "c", "size": 3, "state": "uploaded", "digest": "md5:12"},
        ],
    },
)


def test_the_release_lists_assets_with_size_state_digest_and_the_draft_flag(monkeypatch):
    monkeypatch.setattr(facts, "run_text", lambda argv, **_k: _completed(0, VIEW_ANSWER))

    found = facts.release_view("o/r", "v1")

    assert found.draft is False
    assert found.assets == {
        "a": facts.Asset(7, "uploaded", "ab" * 32),
        "b": facts.Asset(0, "starter", None),
        "c": facts.Asset(3, "uploaded", None),
    }


def test_gh_is_asked_for_the_assets_and_the_draft_flag(monkeypatch):
    seen = []

    def fake(argv, **_k):
        seen.append(argv)
        return _completed(0, VIEW_ANSWER)

    monkeypatch.setattr(facts, "run_text", fake)

    facts.release_view("o/r", "v1")

    assert seen[0][seen[0].index("--json") + 1] == "assets,isDraft"


def test_a_tag_with_no_release_is_none(monkeypatch):
    monkeypatch.setattr(
        facts, "run_text", lambda argv, **_k: _completed(1, "", "release not found"),
    )

    assert facts.release_view("o/r", "v1") is None


def test_any_other_failure_of_the_release_read_is_a_facts_error(monkeypatch):
    monkeypatch.setattr(facts, "run_text", lambda argv, **_k: _completed(1, "", "HTTP 502"))

    with pytest.raises(facts.FactsError, match="HTTP 502"):
        facts.release_view("o/r", "v1")


def test_a_silent_failure_of_the_release_read_is_a_facts_error_too(monkeypatch):
    monkeypatch.setattr(facts, "run_text", lambda argv, **_k: _completed(1))

    with pytest.raises(facts.FactsError, match="exit 1"):
        facts.release_view("o/r", "v1")


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        '{"assets": 3, "isDraft": false}',
        '{"files": [], "isDraft": false}',
        '{"assets": [{}], "isDraft": false}',
        '{"assets": []}',
        '{"assets": [{"name": "a", "size": "big", "state": "uploaded"}], "isDraft": false}',
        '{"assets": ["a"], "isDraft": false}',
    ],
)
def test_an_unreadable_release_answer_is_a_facts_error(monkeypatch, answer):
    monkeypatch.setattr(facts, "run_text", lambda argv, **_k: _completed(0, answer))

    with pytest.raises(facts.FactsError, match="unreadable answer"):
        facts.release_view("o/r", "v1")


# --- run_verdict ----------------------------------------------------------------------------------


def test_no_run_is_none():
    state, why = facts.run_verdict([])

    assert state == "none"
    assert "no release.yml run of the tag push is recorded" in why


def test_a_successful_run_is_green():
    assert facts.run_verdict([_run()])[0] == "green"


def test_an_unfinished_run_is_pending():
    verdict = facts.run_verdict([_run("in_progress", "")])

    assert verdict == ("pending", "release.yml is in_progress — u-2026-01-01-in_progress")


def test_a_failed_run_is_red():
    verdict = facts.run_verdict([_run(conclusion="failure")])

    assert verdict == ("red", "release.yml concluded failure — u-2026-01-01-failure")


def test_one_red_run_is_red_whatever_else_is_green_and_whatever_is_newer():
    old_fail = _run(conclusion="failure", created="2026-01-01")
    new_pass = _run(created="2026-01-02")

    for runs in ([old_fail, new_pass], [new_pass, old_fail]):
        assert facts.run_verdict(runs)[0] == "red"


def test_a_run_that_has_not_finished_holds_the_verdict_back():
    running = _run("in_progress", "", created="2026-01-01")
    newer_pass = _run(created="2026-01-02")

    assert facts.run_verdict([running, newer_pass])[0] == "pending"
    assert facts.run_verdict([newer_pass, running])[0] == "pending"


def test_a_red_run_wins_over_an_unfinished_one():
    runs = [_run("in_progress", ""), _run(conclusion="failure")]

    assert facts.run_verdict(runs)[0] == "red"


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped", "timed_out", "neutral", ""])
def test_only_success_is_green(conclusion):
    assert facts.run_verdict([_run(conclusion=conclusion)])[0] == "red"
