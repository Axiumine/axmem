"""The mutation gate: the base a push is compared with, the modules it mutates, and what it says when mutmut cannot finish."""

import os
import signal
import subprocess
import time

import pytest

from scripts.quality import core, security, testing


@pytest.fixture(autouse=True)
def _no_inherited_git_environment(monkeypatch):
    """A Git hook exports GIT_DIR/GIT_INDEX_FILE; tests that init repos in tmp dirs must not inherit them."""
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(name)
    monkeypatch.delenv("QUALITY_PUSH_REMOTE", raising=False)
    # nor the configuration of whoever runs them: a global commit.gpgsign=true would sign the commits of the temporary repositories
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _mutmut(monkeypatch, run_result, results_text):
    monkeypatch.setenv("MUTATION_TARGETS", "app.utils.*")
    monkeypatch.delenv("MUTATION_MAX_SURVIVORS", raising=False)
    monkeypatch.delenv("MUTATION_TIMEOUT_S", raising=False)

    def fake_run(argv, **_kwargs):
        return run_result if "run" in argv else (0, results_text)

    monkeypatch.setattr(testing, "run", fake_run)
    return testing.gate_mutation(core.Ctx(stage="manual", files=None))


def _git(repo, *args):
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-C", str(repo), *args],  # noqa: S607
        check=True, capture_output=True,
    )


def _track(repo, remote, branch):
    """Make ``remote/branch`` (at ``HEAD``) the upstream of ``work``; the remote has an address that nothing ever opens."""
    _git(repo, "remote", "add", remote, f"/nonexistent/{remote}.git")
    _git(repo, "update-ref", f"refs/remotes/{remote}/{branch}", "HEAD")
    _git(repo, "config", "branch.work.remote", remote)
    _git(repo, "config", "branch.work.merge", f"refs/heads/{branch}")


def _clone_of_a_branch(tmp_path, monkeypatch, *, upstream=None, origin_main=False):
    """A repository on the branch ``work`` with a commit, optionally tracking ``upstream`` and holding ``origin/main``.

    Both remote-tracking refs are made by hand at the first commit: nothing here needs a second repository or the network.
    """
    _git(tmp_path, "init", "-q", "-b", "work")
    (tmp_path / "README").write_text("x", encoding="utf-8")
    _git(tmp_path, "add", "README")
    _git(tmp_path, "commit", "-q", "-m", "first")
    if origin_main:
        _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    if upstream:
        remote, _, branch = upstream.partition("/")
        _track(tmp_path, remote, branch)
    monkeypatch.setattr(core, "ROOT", tmp_path)


def test_base_ref_is_the_upstream_of_the_branch_even_when_origin_main_exists(tmp_path, monkeypatch):
    _clone_of_a_branch(tmp_path, monkeypatch, upstream="axiumine/main", origin_main=True)

    assert core.base_ref() == "axiumine/main"


def test_base_ref_is_origin_main_for_a_branch_without_an_upstream(tmp_path, monkeypatch):
    _clone_of_a_branch(tmp_path, monkeypatch, origin_main=True)

    assert core.base_ref() == "origin/main"


def test_base_ref_is_main_of_the_remote_being_pushed_to_for_a_branch_without_an_upstream(tmp_path, monkeypatch):
    _clone_of_a_branch(tmp_path, monkeypatch, origin_main=True)
    _git(tmp_path, "update-ref", "refs/remotes/axiumine/main", "HEAD")
    monkeypatch.setenv("QUALITY_PUSH_REMOTE", "axiumine")

    assert core.base_ref() == "axiumine/main"


def test_base_ref_ignores_a_pushed_remote_that_holds_no_main_and_the_upstream_still_wins(tmp_path, monkeypatch):
    _clone_of_a_branch(tmp_path, monkeypatch, origin_main=True)
    monkeypatch.setenv("QUALITY_PUSH_REMOTE", "git@example.org:team/project.git")  # a push to a URL names no remote

    assert core.base_ref() == "origin/main"

    _track(tmp_path, "mirror", "main")
    _git(tmp_path, "update-ref", "refs/remotes/axiumine/main", "HEAD")
    monkeypatch.setenv("QUALITY_PUSH_REMOTE", "axiumine")

    assert core.base_ref() == "mirror/main"


def test_base_ref_is_the_commit_before_head_without_an_upstream_and_without_origin_main(tmp_path, monkeypatch):
    _clone_of_a_branch(tmp_path, monkeypatch)

    assert core.base_ref() == "HEAD~1"


def _push_changing(tmp_path, *names):
    """Commit the given files on top of the upstream the repository was made with."""
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "change")


def test_mutation_targets_are_the_app_modules_that_the_push_adds_to_the_upstream(tmp_path, monkeypatch):
    _clone_of_a_branch(tmp_path, monkeypatch, upstream="axiumine/main", origin_main=True)
    _push_changing(tmp_path, "app/utils/clock.py", "app/note.txt", "tests/test_clock.py", "scripts/tool.py")
    monkeypatch.delenv("MUTATION_TARGETS", raising=False)

    assert testing.mutation_targets(core.Ctx(stage="pre-push", files=None)) == ["app.utils.clock.*"]


def test_mutation_targets_ignore_what_an_older_origin_main_lacks_when_the_upstream_has_it(tmp_path, monkeypatch):
    """`origin` can be another remote than the one the branch tracks: the diff must not hold the history before the upstream."""
    _clone_of_a_branch(tmp_path, monkeypatch, origin_main=True)  # origin/main is at the first commit
    _push_changing(tmp_path, "app/old.py")
    _track(tmp_path, "axiumine", "main")  # the upstream already holds app/old.py
    _push_changing(tmp_path, "app/new.py")
    monkeypatch.delenv("MUTATION_TARGETS", raising=False)

    assert testing.mutation_targets(core.Ctx(stage="pre-push", files=None)) == ["app.new.*"]


def test_mutation_targets_ignore_what_only_the_upstream_changed_since_the_branch_left_it(tmp_path, monkeypatch):
    _clone_of_a_branch(tmp_path, monkeypatch)
    _push_changing(tmp_path, "app/shared.py")
    _git(tmp_path, "checkout", "-q", "-b", "side")
    (tmp_path / "app" / "shared.py").write_text("x = 2\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "upstream moves on")
    _git(tmp_path, "checkout", "-q", "work")
    _track(tmp_path, "axiumine", "main")
    _git(tmp_path, "update-ref", "refs/remotes/axiumine/main", "side")  # the upstream holds a change that the branch lacks
    _push_changing(tmp_path, "docs/page.md")
    monkeypatch.delenv("MUTATION_TARGETS", raising=False)

    with pytest.raises(core.Skip, match="no changed app/ modules"):
        testing.mutation_targets(core.Ctx(stage="pre-push", files=None))


def test_mutation_targets_fail_when_git_cannot_diff_instead_of_reading_as_no_change(tmp_path, monkeypatch):
    _clone_of_a_branch(tmp_path, monkeypatch)  # one commit, no upstream, no origin/main: the base HEAD~1 does not exist
    monkeypatch.delenv("MUTATION_TARGETS", raising=False)

    with pytest.raises(core.MissingToolError, match="git diff"):
        testing.mutation_targets(core.Ctx(stage="pre-push", files=None))


def test_mutation_targets_skip_when_the_push_changes_no_app_module_and_name_the_way_out(tmp_path, monkeypatch):
    _clone_of_a_branch(tmp_path, monkeypatch, upstream="axiumine/main")
    _push_changing(tmp_path, "docs/page.md", "tests/test_x.py")
    monkeypatch.delenv("MUTATION_TARGETS", raising=False)

    with pytest.raises(core.Skip, match="MUTATION_TARGETS"):
        testing.mutation_targets(core.Ctx(stage="pre-push", files=None))


def test_an_explicit_scope_overrides_the_diff(tmp_path, monkeypatch):
    _clone_of_a_branch(tmp_path, monkeypatch, upstream="axiumine/main")
    _push_changing(tmp_path, "app/changed.py")
    monkeypatch.setenv("MUTATION_TARGETS", "app.utils.* app.other.*")

    assert testing.mutation_targets(core.Ctx(stage="pre-push", files=None)) == ["app.utils.*", "app.other.*"]


def test_a_staged_commit_is_scoped_to_the_staged_app_modules_not_to_the_upstream(tmp_path, monkeypatch):
    _clone_of_a_branch(tmp_path, monkeypatch, upstream="axiumine/main")
    _push_changing(tmp_path, "app/pushed.py")
    monkeypatch.delenv("MUTATION_TARGETS", raising=False)

    ctx = core.Ctx(stage="pre-commit", files=["app/staged.py", "tests/test_staged.py"])

    assert testing.mutation_targets(ctx) == ["app.staged.*"]


def test_the_gitnexus_gate_compares_a_push_with_the_base_of_the_push(tmp_path, monkeypatch):
    _clone_of_a_branch(tmp_path, monkeypatch, upstream="axiumine/main", origin_main=True)
    (tmp_path / ".gitnexus").mkdir()
    (tmp_path / ".gitnexus" / "run.cjs").write_text("", encoding="utf-8")
    seen = []
    monkeypatch.setattr(security, "ROOT", tmp_path)
    monkeypatch.setattr(security.shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(security, "run", lambda argv, **_kwargs: seen.append(argv) or (0, "no changes"))

    assert security.gate_gitnexus(core.Ctx(stage="pre-push", files=None)) == (True, "no changes")

    assert seen[0][seen[0].index("--base-ref") + 1] == "axiumine/main"
    assert seen[0][seen[0].index("--scope") + 1] == "compare"


def test_mutant_statuses_keep_only_targeted_modules():
    results = "Survived:\n    app.utils.a.x_f__mutmut_1: survived\n    app.other.b.x_g__mutmut_2: survived\n"
    assert testing.mutant_statuses(results, ["app.utils.*"]) == {"app.utils.a.x_f__mutmut_1": "survived"}


def test_mutation_gate_fails_when_the_suite_cannot_run(monkeypatch):
    ok, out = _mutmut(monkeypatch, (1, "failed to collect stats. runner returned 1"), "")
    assert not ok
    assert "could not run the test suite" in out


def _timed_out(monkeypatch, targets="app.a.* app.b.*"):
    """Run the gate over a ``mutmut run`` that hits its cap; returns the gate's answer and the keywords of the run."""
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return 124, f"timed out after {kwargs['timeout']}s: " + " ".join(argv)

    monkeypatch.setenv("MUTATION_TARGETS", targets)
    monkeypatch.setattr(testing, "run", fake_run)
    return testing.gate_mutation(core.Ctx(stage="manual", files=None)), seen


def test_the_mutation_cap_is_24_hours_unless_the_environment_names_another(monkeypatch):
    monkeypatch.delenv("MUTATION_TIMEOUT_S", raising=False)

    assert testing.MUTATION_TIMEOUT_S_DEFAULT == 86400
    assert testing.mutation_timeout() == 86400

    monkeypatch.setenv("MUTATION_TIMEOUT_S", "172800")

    assert testing.mutation_timeout() == 172800


def test_mutmut_runs_under_the_default_cap_and_under_the_one_the_environment_names(monkeypatch):
    monkeypatch.delenv("MUTATION_TIMEOUT_S", raising=False)

    _, seen = _timed_out(monkeypatch)

    assert (seen["timeout"], seen["own_session"]) == (86400, True)

    monkeypatch.setenv("MUTATION_TIMEOUT_S", "259200")

    _, seen = _timed_out(monkeypatch)

    assert (seen["timeout"], seen["own_session"]) == (259200, True)


def test_mutation_gate_says_that_it_ran_out_of_time_under_the_cap_in_force(monkeypatch):
    monkeypatch.setenv("MUTATION_TIMEOUT_S", "5400")

    (ok, out), seen = _timed_out(monkeypatch)

    assert not ok
    assert "mutation exceeded 5400 s over 2 module patterns" in out  # the cap in force, not the default
    assert "86400" not in out
    assert "app.a.*" not in out  # not the command line, which holds every pattern
    assert seen["timeout"] == 5400


def test_mutation_gate_names_the_ways_out_of_a_run_that_ran_out_of_time_and_that_a_rerun_resumes(monkeypatch):
    monkeypatch.setenv("MUTATION_TIMEOUT_S", "5400")

    (_, out), _ = _timed_out(monkeypatch)

    for way_out in ("MUTATION_TIMEOUT_S", "MUTATION_TARGETS", "SKIP=mutation"):
        assert way_out in out
    assert "mutants/ keeps the mutants already tested, and a rerun continues from them" in out


def test_mutation_gate_names_the_default_cap_when_it_is_the_one_that_ran_out(monkeypatch):
    monkeypatch.delenv("MUTATION_TIMEOUT_S", raising=False)

    (ok, out), _ = _timed_out(monkeypatch)

    assert not ok
    assert "mutation exceeded 86400 s over 2 module patterns" in out


@pytest.mark.parametrize("value", ["", " ", "0", "00", "-5", "+5", "1.5", "2h", "5 ", " 5", "1_000", "\u0665", "\u00b2",
                                   "2147484", "99999999999999999999", "9" * 5000])
def test_an_invalid_cap_fails_the_gate_with_the_variable_and_never_falls_back(monkeypatch, value):
    ran = []
    monkeypatch.setenv("MUTATION_TARGETS", "app.utils.*")
    monkeypatch.setenv("MUTATION_TIMEOUT_S", value)
    monkeypatch.setattr(testing, "run", lambda argv, **_kwargs: ran.append(argv) or (0, ""))

    ok, out = testing.gate_mutation(core.Ctx(stage="manual", files=None))

    assert not ok
    assert "MUTATION_TIMEOUT_S" in out
    assert "positive whole number of seconds" in out
    assert ran == []  # mutmut never started under a cap that nobody chose


def test_the_longest_cap_is_accepted_and_leading_zeros_are_still_a_whole_number(monkeypatch):
    monkeypatch.setenv("MUTATION_TIMEOUT_S", str(testing.MUTATION_TIMEOUT_S_MAX))
    assert testing.mutation_timeout() == testing.MUTATION_TIMEOUT_S_MAX

    monkeypatch.setenv("MUTATION_TIMEOUT_S", "0000086400")

    assert testing.mutation_timeout() == 86400


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX")
def test_a_command_in_its_own_session_leaves_no_worker_behind_when_it_times_out(tmp_path):
    pidfile = tmp_path / "worker.pid"

    code, out = core.run(
        ["sh", "-c", f"sleep 300 & echo $! > {pidfile}; wait"], timeout=2, own_session=True,  # noqa: S604
    )

    assert code == 124
    assert "timed out after 2s" in out
    worker = int(pidfile.read_text(encoding="utf-8"))
    for _ in range(50):  # the killed worker is reaped a moment after the signal
        try:
            os.kill(worker, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(worker, signal.SIGKILL)
        pytest.fail("the worker of the timed out command is still running")


def test_mutation_gate_never_passes_on_untested_mutants(monkeypatch):
    ok, out = _mutmut(monkeypatch, (0, ""), "app.utils.a.x_f__mutmut_1: not checked\n")
    assert not ok
    assert "never tested" in out


def test_mutation_gate_counts_survivors_against_the_tolerance(monkeypatch):
    ok, out = _mutmut(monkeypatch, (0, ""), "app.utils.a.x_f__mutmut_1: survived\n")
    assert not ok
    assert "1 mutants not killed" in out
    monkeypatch.setenv("MUTATION_MAX_SURVIVORS", "1")
    assert testing.gate_mutation(core.Ctx(stage="manual", files=None)) == (True, "")


def test_mutation_gate_passes_when_everything_is_killed(monkeypatch):
    assert _mutmut(monkeypatch, (0, ""), "") == (True, "")
