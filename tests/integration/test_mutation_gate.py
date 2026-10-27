"""The mutation gate: the base a push is compared with, the modules it mutates, and what it says when mutmut cannot finish."""

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from app.config import settings as settings_module
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


@pytest.fixture(autouse=True)
def _no_model_cache_of_the_user(tmp_path, monkeypatch):
    """A gate run mirrors the model cache of the data directory: these tests look at an empty one, not at the real one."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))


def _mutmut(monkeypatch, run_result, results_text, results_code=0):
    monkeypatch.setenv("MUTATION_TARGETS", "app.utils.*")
    monkeypatch.delenv("MUTATION_MAX_SURVIVORS", raising=False)
    monkeypatch.delenv("MUTATION_TIMEOUT_S", raising=False)

    def fake_run(argv, **_kwargs):
        return run_result if "run" in argv else (results_code, results_text)

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
    assert _mutmut(monkeypatch, (0, ""), "    app.utils.a.x_f__mutmut_1: killed\n") == (True, "")


# What `mutmut results --all true` prints for the statuses of mutmut 3.8 (mutmut/stats.py), one line per mutant
SURVIVING_STATUSES = [
    "survived", "timeout", "no tests", "suspicious", "skipped", "caught by type check", "segfault",
    "check was interrupted by user",
]
LISTING = "    app.utils.a.x_f__mutmut_1: {status}\n    app.utils.a.x_f__mutmut_2: killed\n"


@pytest.mark.parametrize("status", SURVIVING_STATUSES)
def test_every_status_that_is_neither_killed_nor_untested_is_a_survivor_and_a_killed_mutant_is_not(monkeypatch, status):
    ok, out = _mutmut(monkeypatch, (0, ""), LISTING.format(status=status))

    assert not ok
    assert out == f"1 mutants not killed (tolerated 0):\napp.utils.a.x_f__mutmut_1: {status}"  # not the killed mutant_2

    monkeypatch.setenv("MUTATION_MAX_SURVIVORS", "1")

    assert testing.gate_mutation(core.Ctx(stage="manual", files=None)) == (True, "")


def test_a_listing_of_killed_mutants_alone_passes_even_with_nothing_tolerated(monkeypatch):
    killed = "".join(f"    app.utils.a.x_f__mutmut_{n}: killed\n" for n in range(1, 4))

    assert _mutmut(monkeypatch, (0, ""), killed) == (True, "")


def test_a_mutant_that_was_never_tested_fails_the_gate_whatever_the_tolerance(monkeypatch):
    monkeypatch.setenv("MUTATION_MAX_SURVIVORS", "100")
    listing = "    app.utils.a.x_f__mutmut_1: not checked\n    app.utils.a.x_f__mutmut_2: not checked\n"

    ok, out = _mutmut(monkeypatch, (0, "tail of the run"), listing)

    assert not ok
    assert out.startswith("2 targeted mutants were never tested (first: app.utils.a.x_f__mutmut_1)")
    assert testing.gate_mutation(core.Ctx(stage="manual", files=None))[0] is False  # the tolerance is read again: 100


def test_untested_mutants_are_reported_before_survivors_and_the_survivors_are_not_hidden_by_them(monkeypatch):
    listing = "    app.utils.a.x_f__mutmut_1: survived\n    app.utils.a.x_f__mutmut_2: not checked\n"

    ok, out = _mutmut(monkeypatch, (0, ""), listing)

    assert not ok
    assert out.startswith("1 targeted mutants were never tested (first: app.utils.a.x_f__mutmut_2)")


def test_mutants_outside_the_targeted_modules_are_ignored_whatever_their_status(monkeypatch):
    listing = (
        "    app.utils.a.x_f__mutmut_1: killed\n"
        "    app.other.pagination.x_page__mutmut_1: not checked\n"
        "    app.other.pagination.x_page__mutmut_2: survived\n"
        "    app.utilsx.b.x_g__mutmut_1: not checked\n"  # 'app.utils.*' does not reach 'app.utilsx'
    )

    assert _mutmut(monkeypatch, (0, ""), listing) == (True, "")


def test_the_gate_asks_mutmut_for_every_status_not_for_the_default_listing(monkeypatch):
    seen = []

    def fake_run(argv, **_kwargs):
        seen.append(argv)
        return 0, "app.utils.a.x_f__mutmut_1: killed\n"

    monkeypatch.setenv("MUTATION_TARGETS", "app.utils.*")
    monkeypatch.setattr(testing, "run", fake_run)

    assert testing.gate_mutation(core.Ctx(stage="manual", files=None)) == (True, "")

    assert [argv[-3:] for argv in seen if "results" in argv] == [["results", "--all", "true"]]


def test_a_failed_mutmut_results_fails_the_gate_instead_of_reading_as_no_survivor(monkeypatch):
    ok, out = _mutmut(monkeypatch, (0, ""), "Traceback (most recent call last):\nFileNotFoundError: mutants\n", 1)

    assert not ok
    assert "could not list its results (exit 1)" in out
    assert "FileNotFoundError: mutants" in out

    ok, out = _mutmut(monkeypatch, (0, ""), "    app.utils.a.x_f__mutmut_1: killed\n", 2)  # a listing does not excuse the exit code

    assert not ok
    assert "exit 2" in out


@pytest.mark.parametrize("listing", ["", "\n", "mutants/ is empty\n", "no mutants found: nothing to list"])
def test_a_listing_without_one_mutant_fails_the_gate_instead_of_reading_as_all_killed(monkeypatch, listing):
    ok, out = _mutmut(monkeypatch, (0, "run tail"), listing)

    assert not ok
    assert "listed no mutant at all" in out
    assert out.endswith(listing.strip() or "(no output)")


# ---- the data directory of a mutation run ----------------------------------------------------------------------------
#
# The tests load the models and the TensorRT engines from ~/.local/share/axmem/models. A mutant that changes a path
# constant made directories of other names there (128 MB of model and engine each), and the engine cache prunes the
# fingerprints it does not use, so a mutant of the fingerprint removes the engine that the next run loads.

STARTED_WITH = os.environ.get("XDG_DATA_HOME")  # what app.config.settings read when conftest imported it
BIG = b"0123456789"  # longer than the 8 bytes that SMALL_FILE_BYTES is set to in these tests


@pytest.fixture
def user_models(monkeypatch):
    """The model cache of the user, in the data directory that XDG_DATA_HOME names (as platformdirs reads it)."""
    monkeypatch.setattr(testing, "SMALL_FILE_BYTES", 8)
    models = testing.models_dir()
    files = {
        "embedder/BAAI--bge/onnx/model.onnx": BIG,
        "embedder/BAAI--bge/tokenizer.json": b"{}",
        "embedder/BAAI--bge/manifest.json": b'{"s":1}',
        "embedder/BAAI--bge/.cache/huggingface/.gitignore": b"*",
        "tensorrt/BAAI--bge/.lock": b"",
        "tensorrt/BAAI--bge/2108/engine.plan": BIG,
        "tensorrt/BAAI--bge/2108/fingerprint.json": b"{}",
    }
    for name, content in files.items():
        (models / name).parent.mkdir(parents=True, exist_ok=True)
        (models / name).write_bytes(content)
    return models


def _tree(root):
    """Every path under ``root``: a directory, the target of a link, or the content of a file (nothing a run changes is missed)."""
    return {
        str(path.relative_to(root)): (
            ("link", os.readlink(path)) if path.is_symlink() else "dir" if path.is_dir() else path.read_bytes()
        )
        for path in sorted(root.rglob("*"))
    }


def _a_run_that(monkeypatch, during=lambda _home: None, outcome=(0, "")):
    """Make ``mutmut run`` call ``during(data_home)`` and end with ``outcome``; ``mutmut results`` lists a killed mutant.

    Returns what the run saw: ``seen["home"]`` is the data home it was given.
    """
    seen = {}

    def fake_run(argv, **kwargs):
        if "run" not in argv:
            return 0, "app.utils.a.x_f__mutmut_1: killed\n"
        seen["env"] = kwargs["env"]
        seen["home"] = Path(kwargs["env"]["XDG_DATA_HOME"])
        during(seen["home"])
        return outcome

    monkeypatch.setenv("MUTATION_TARGETS", "app.utils.*")
    monkeypatch.setattr(testing, "run", fake_run)
    return seen


def _gate():
    return testing.gate_mutation(core.Ctx(stage="manual", files=None))


def test_the_models_dir_follows_the_data_home_that_the_environment_names(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "elsewhere"))

    assert testing.models_dir() == tmp_path / "elsewhere" / "axmem" / "models"
    assert testing.models_dir(tmp_path / "other") == tmp_path / "other" / "axmem" / "models"


def test_the_default_model_and_engine_directories_of_the_app_are_the_ones_that_a_mutation_run_mirrors(monkeypatch):
    if STARTED_WITH is None:  # the settings read the data directory when they were imported, not the one of this test
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_DATA_HOME", STARTED_WITH)
    fields = settings_module.Settings.model_fields
    models = testing.models_dir()

    assert fields["EMBEDDING_MODEL_DIR"].default == str(models / "embedder")
    assert fields["RERANKING_MODEL_DIR"].default == str(models / "reranker")
    assert fields["EMBEDDING_ENGINE_CACHE_DIR"].default == str(models / "tensorrt")
    assert fields["RERANKING_ENGINE_CACHE_DIR"].default == str(models / "tensorrt")


def test_a_mutation_run_gets_a_data_home_of_its_own_that_holds_a_mirror_of_the_model_cache(user_models, monkeypatch):
    mirror = {}
    seen = _a_run_that(monkeypatch, lambda home: mirror.update(_tree(testing.models_dir(home))))

    assert _gate() == (True, "")

    assert seen["env"] == {"XDG_DATA_HOME": str(seen["home"])}  # nothing else of the environment is touched
    assert seen["home"].name.startswith("axmem-mutation-data-")
    assert seen["home"] != user_models.parents[1]
    large = lambda name: ("link", str(user_models / name))  # noqa: E731 - the large files stay where they are
    assert mirror == {
        "embedder": "dir",
        "embedder/BAAI--bge": "dir",
        "embedder/BAAI--bge/onnx": "dir",
        "embedder/BAAI--bge/onnx/model.onnx": large("embedder/BAAI--bge/onnx/model.onnx"),
        "embedder/BAAI--bge/tokenizer.json": b"{}",
        "embedder/BAAI--bge/manifest.json": b'{"s":1}',
        "embedder/BAAI--bge/.cache": "dir",
        "embedder/BAAI--bge/.cache/huggingface": "dir",
        "embedder/BAAI--bge/.cache/huggingface/.gitignore": b"*",
        "tensorrt": "dir",
        "tensorrt/BAAI--bge": "dir",
        "tensorrt/BAAI--bge/.lock": b"",
        "tensorrt/BAAI--bge/2108": "dir",
        "tensorrt/BAAI--bge/2108/engine.plan": large("tensorrt/BAAI--bge/2108/engine.plan"),
        "tensorrt/BAAI--bge/2108/fingerprint.json": b"{}",
    }


def test_what_a_mutant_writes_and_removes_in_its_data_home_leaves_the_cache_of_the_user_as_it_was(user_models, monkeypatch):
    before = _tree(user_models)

    def mutant(home):
        models = testing.models_dir(home)
        (models / "embedder" / "BAAIXX--XXbge").mkdir()  # a model under another name: the download lands there
        (models / "embedder" / "BAAIXX--XXbge" / "model.onnx").write_bytes(BIG)
        shutil.rmtree(models / "tensorrt/BAAI--bge/2108")  # the prune of the engines of other fingerprints
        (models / "tensorrt/BAAI--bge/2108e").mkdir()  # an engine under another fingerprint
        (models / "tensorrt/BAAI--bge/2108e/engine.plan").write_bytes(BIG)
        (models / "embedder/BAAI--bge/manifest.json").write_text("rewritten")
        (models / "tensorrt/BAAI--bge/.lock").write_text("held")
        (models / "embedder/BAAI--bge/onnx/model.onnx.tmp").write_bytes(BIG)
        os.replace(models / "embedder/BAAI--bge/onnx/model.onnx.tmp", models / "embedder/BAAI--bge/onnx/model.onnx")

    _a_run_that(monkeypatch, mutant)

    assert _gate() == (True, "")

    assert _tree(user_models) == before


def test_the_mutation_data_home_does_not_outlive_the_run(user_models, monkeypatch):
    seen = _a_run_that(monkeypatch, lambda home: (home / "left-behind").write_text("x"))

    _gate()

    assert not seen["home"].exists()


def test_the_mutation_data_home_is_removed_after_a_run_that_hit_the_cap_too(user_models, monkeypatch):
    seen = _a_run_that(monkeypatch, outcome=(124, "timed out"))

    ok, out = _gate()

    assert not ok
    assert "mutation exceeded" in out
    assert not seen["home"].exists()


def test_the_mutation_data_home_is_removed_after_a_run_that_raised_too(user_models, monkeypatch):
    def fail(_home):
        raise KeyboardInterrupt

    seen = _a_run_that(monkeypatch, fail)

    with pytest.raises(KeyboardInterrupt):
        _gate()

    assert not seen["home"].exists()


def test_a_user_without_a_model_cache_gets_an_empty_data_home_not_a_failed_gate(monkeypatch):
    seen_inside = {}

    def during(home):
        seen_inside["home"] = home.is_dir()
        seen_inside["models"] = testing.models_dir(home).exists()

    _a_run_that(monkeypatch, during)

    assert _gate() == (True, "")

    assert seen_inside == {"home": True, "models": False}


def test_the_mirror_copies_a_file_of_the_limit_size_and_links_a_file_of_one_byte_more(tmp_path, monkeypatch):
    monkeypatch.setattr(testing, "SMALL_FILE_BYTES", 8)
    source = tmp_path / "source"
    source.mkdir()
    (source / "limit").write_bytes(b"12345678")
    (source / "over").write_bytes(b"123456789")
    (source / "empty").write_bytes(b"")

    testing.mirror_cache(source, tmp_path / "mirror")

    assert _tree(tmp_path / "mirror") == {"limit": b"12345678", "over": ("link", str(source / "over")), "empty": b""}


def test_the_mirror_makes_real_directories_of_linked_ones_and_leaves_out_a_link_to_nothing(tmp_path):
    disk = tmp_path / "other-disk"
    (disk / "engines").mkdir(parents=True)
    (disk / "engines" / "fingerprint.json").write_bytes(b"{}")
    source = tmp_path / "source"
    source.mkdir()
    (source / "tensorrt").symlink_to(disk / "engines")  # a cache moved to another disk and linked back
    (source / "gone").symlink_to(disk / "missing")

    testing.mirror_cache(source, tmp_path / "mirror")

    assert _tree(tmp_path / "mirror") == {"tensorrt": "dir", "tensorrt/fingerprint.json": b"{}"}
