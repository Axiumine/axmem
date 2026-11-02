# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""How the release judges the check runs of its pull request: ``scripts/release/pull_request.py``, on rows as GitHub gives them.

A required check is green when every run of its name is done and passed (success, skipped or neutral), red when one run is
done and did not pass, pending when one is not done, and ``none`` while no run of the name exists. The whole is red if one
required check is red, else pending if one is not green, else green. A run of a name the rules do not require counts for
nothing. ``test_release_pipeline_pull_request.py`` runs the script on these rules.
"""

import pytest

from scripts.release.pull_request import context_state, judge

#: two of the required checks
NAMES = ["lint (py3.14)", "trivy"]


class TestTheJudgmentOfTheChecks:
    @staticmethod
    def _run(name: str, status: str = "completed", conclusion: str | None = "success") -> dict[str, object]:
        return {"name": name, "status": status, "conclusion": conclusion, "html_url": f"https://github.example/{name}"}

    @pytest.mark.parametrize("conclusion", ["success", "skipped", "neutral"])
    def test_a_passed_run_is_green(self, conclusion: str) -> None:
        assert context_state([self._run("trivy", conclusion=conclusion)]) == "green"

    @pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "action_required", "stale", None])
    def test_a_finished_run_that_did_not_pass_is_red(self, conclusion: str | None) -> None:
        assert context_state([self._run("trivy", conclusion=conclusion)]) == "red"

    def test_no_run_is_none_and_a_run_in_progress_is_pending(self) -> None:
        assert context_state([]) == "none"
        assert context_state([self._run("trivy", status="in_progress", conclusion=None)]) == "pending"

    def test_two_runs_of_one_name_are_green_only_when_both_are_done_and_red_when_one_failed(self) -> None:
        running = self._run("trivy", status="queued", conclusion=None)
        assert context_state([self._run("trivy"), running]) == "pending"
        assert context_state([self._run("trivy", conclusion="failure"), running]) == "red"
        assert context_state([self._run("trivy"), self._run("trivy")]) == "green"

    def test_all_green_is_green_and_nothing_that_is_not_required_counts(self) -> None:
        runs = [self._run("lint (py3.14)"), self._run("trivy"), self._run("optional", conclusion="failure")]
        assert judge(NAMES, runs) == ("green", [])

    def test_a_required_check_without_a_run_is_pending_not_green(self) -> None:
        verdict, lines = judge(NAMES, [self._run("lint (py3.14)")])
        assert (verdict, lines) == ("pending", ["trivy: none"])

    def test_red_wins_over_pending_and_names_the_run_that_failed(self) -> None:
        runs = [self._run("lint (py3.14)", conclusion="failure"), self._run("trivy", status="in_progress", conclusion=None)]
        verdict, lines = judge(NAMES, runs)
        assert verdict == "red"
        assert lines == ["lint (py3.14): red — https://github.example/lint (py3.14)", "trivy: pending"]
