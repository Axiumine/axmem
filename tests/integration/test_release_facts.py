# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The release facts: what git, GitHub and the PyPI JSON of the project say about a release.

The release guard and the integrity check decide from these reads. They are tested against a real bare repository for
the tags, with the command runner replaced where the case is a shape of answer (GitHub printing text that is not JSON),
and against JSON written the way PyPI serves it. ``test_release_facts_signing_a.py`` and ``_b.py`` cover the signing facts.
"""

import json
import os
import subprocess
import sys

import pytest

from scripts.release import facts

PACKAGE = "axmem"
WHEEL = "axmem-0.1.0-py3-none-any.whl"
SDIST = "axmem-0.1.0.tar.gz"
DIGEST = "ab" * 32
SYSTEM_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": "/usr/bin:/bin",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t.invalid",
}


@pytest.fixture(autouse=True)
def _no_inherited_git_environment(monkeypatch):
    """A Git hook exports GIT_DIR/GIT_INDEX_FILE; tests that init repos in tmp dirs must not inherit them."""
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(name)


def _entry(name, digest=DIGEST, url=None, **more):
    """One file of ``releases[version]`` as PyPI serves it."""
    return {
        "filename": name,
        "digests": {"sha256": digest},
        "url": url or f"https://files.pythonhosted.org/packages/ab/cd/{name}",
        "yanked": False,
        **more,
    }


def _doc(releases, name=PACKAGE):
    """The text of ``/pypi/<name>/json`` with ``releases`` (version -> list of file entries)."""
    return json.dumps({"info": {"name": name, "version": "0"}, "releases": releases, "urls": []})


def _one(**changes):
    """A document with the sdist of 0.1.0, ``changes`` applied to its entry."""
    return _doc({"0.1.0": [{**_entry(SDIST), **changes}]})


def _completed(code, out="", err=""):
    return subprocess.CompletedProcess(["x"], code, out, err)


def _python(code):
    return [sys.executable, "-c", code]


# --- run_text -------------------------------------------------------------------------------------


def test_a_missing_command_is_a_facts_error():
    with pytest.raises(facts.FactsError, match="cannot run no-such-command-xyz"):
        facts.run_text(["no-such-command-xyz"])


def test_a_return_code_outside_ok_names_the_command_and_its_message():
    argv = _python("import sys; sys.stderr.write('boom'); sys.exit(3)")

    with pytest.raises(facts.FactsError, match=r"-c import sys; sys.stderr.write\(' failed: boom"):
        facts.run_text(argv)


def test_a_silent_failure_still_names_the_exit_code():
    with pytest.raises(facts.FactsError, match="exit 3"):
        facts.run_text(_python("import sys; sys.exit(3)"))


def test_the_stdout_of_a_failing_command_is_the_message_when_stderr_is_empty():
    with pytest.raises(facts.FactsError, match="said this"):
        facts.run_text(_python("import sys; print('said this'); sys.exit(1)"))


def test_a_listed_return_code_is_an_answer():
    done = facts.run_text(_python("import sys; sys.exit(1)"), ok=(0, 1))

    assert done.returncode == 1


def test_the_working_directory_is_honoured(tmp_path):
    done = facts.run_text(_python("import os; print(os.getcwd())"), cwd=str(tmp_path))

    assert os.path.realpath(done.stdout.strip()) == os.path.realpath(tmp_path)


def test_a_command_that_does_not_answer_is_a_facts_error_not_a_wait():
    with pytest.raises(facts.FactsError, match=r"gave no answer in 0.3 s"):
        facts.run_text(_python("import time; time.sleep(30)"), timeout=0.3)


def test_the_default_limit_is_a_few_minutes():
    assert 60 <= facts.RUN_TIMEOUT_S <= 600


# --- seconds --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "value"), [("0", 0.0), ("30", 30.0), ("0.2", 0.2)])
def test_a_finite_non_negative_number_of_seconds_is_accepted(text, value):
    assert facts.seconds(text) == value


@pytest.mark.parametrize("text", ["nan", "inf", "-inf", "-1", "soon", ""])
def test_anything_else_is_refused_as_seconds(text):
    with pytest.raises(ValueError, match="seconds"):
        facts.seconds(text)


# --- gh_json --------------------------------------------------------------------------------------


def test_gh_json_parses_what_gh_prints(monkeypatch):
    monkeypatch.setattr(facts, "run_text", lambda argv, **_k: _completed(0, '[{"a": 1}]'))

    assert facts.gh_json(["run", "list"]) == [{"a": 1}]


def test_gh_text_that_is_not_json_is_a_facts_error(monkeypatch):
    monkeypatch.setattr(facts, "run_text", lambda argv, **_k: _completed(0, "Welcome to gh"))

    with pytest.raises(facts.FactsError, match="run list printed text that is not JSON"):
        facts.gh_json(["run", "list"])


# --- origin_tags ----------------------------------------------------------------------------------


def _git(cwd, *args):
    """``git <args>`` run in ``cwd`` with a clean environment; its stdout."""
    done = subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
        ["git", *args],  # noqa: S607 - a program of the test setup, run without a shell
        cwd=cwd,
        env=SYSTEM_ENV,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _commit_of(work, ref):
    return _git(work, "rev-parse", f"{ref}^{{commit}}")


@pytest.fixture(name="remote")
def _remote(tmp_path):
    """``(work, bare)``: a bare repository with an annotated tag, a lightweight tag and a tag that the pattern does not match."""
    work, bare = tmp_path / "work", tmp_path / "remote.git"
    work.mkdir()
    _git(work, "init", "-b", "main", ".")
    _git(work, "commit", "--allow-empty", "-m", "one")
    _git(work, "tag", "-a", "v1.0.0rc1", "-m", "annotated")
    _git(work, "commit", "--allow-empty", "-m", "two")
    _git(work, "tag", "v1.0.1rc1")
    _git(work, "tag", "v1.0.1.post1")
    _git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    _git(work, "push", str(bare), "main", "--tags")
    return work, str(bare)


def test_each_matching_tag_maps_to_the_commit_it_points_at(remote):
    work, bare = remote

    found = facts.origin_tags(bare, "refs/tags/v*rc1")

    assert found == {
        "v1.0.0rc1": _commit_of(work, "v1.0.0rc1"),
        "v1.0.1rc1": _commit_of(work, "v1.0.1rc1"),
    }


def test_an_annotated_tag_gives_the_commit_not_the_tag_object(remote):
    work, bare = remote
    tag_object = _git(work, "rev-parse", "v1.0.0rc1")

    found = facts.origin_tags(bare, "refs/tags/v1.0.0rc1")

    assert found["v1.0.0rc1"] != tag_object
    assert found["v1.0.0rc1"] == _commit_of(work, "v1.0.0rc1")


def test_no_matching_tag_is_an_empty_map(remote):
    _, bare = remote

    assert facts.origin_tags(bare, "refs/tags/v9*rc1") == {}


def test_an_unreachable_remote_is_a_facts_error(tmp_path):
    with pytest.raises(facts.FactsError, match="ls-remote"):
        facts.origin_tags(str(tmp_path / "missing.git"), "refs/tags/v*")


def test_the_working_directory_names_the_remote_by_its_configured_name(remote):
    work, bare = remote
    _git(work, "remote", "add", "axiumine", bare)

    found = facts.origin_tags("axiumine", "refs/tags/v1.0.1rc1", cwd=str(work))

    assert list(found) == ["v1.0.1rc1"]


# --- poll -----------------------------------------------------------------------------------------


def test_a_probe_that_is_done_returns_its_value_without_sleeping(monkeypatch):
    monkeypatch.setattr(facts.time, "sleep", lambda s: pytest.fail("slept"))

    assert facts.poll(lambda: (True, "v"), deadline=facts.time.time() + 5, interval=1) == "v"


def test_poll_probes_again_after_each_interval_until_done(monkeypatch):
    slept = []
    monkeypatch.setattr(facts.time, "sleep", slept.append)
    answers = iter([(False, 1), (False, 2), (True, 3)])

    assert facts.poll(lambda: next(answers), deadline=facts.time.time() + 100, interval=7) == 3
    assert slept == [7, 7]


def test_the_sleep_never_passes_the_deadline(monkeypatch):
    slept = []
    monkeypatch.setattr(facts.time, "sleep", slept.append)
    answers = iter([(False, 1), (True, 2)])

    facts.poll(lambda: next(answers), deadline=facts.time.time() + 2, interval=1000)

    assert 0 < slept[0] <= 2


def test_a_deadline_that_passed_still_probes_once_and_then_raises():
    probes = []

    def probe():
        probes.append(1)
        return False, 0

    with pytest.raises(facts.DeadlineError):
        facts.poll(probe, deadline=facts.time.time() - 1, interval=1)
    assert probes == [1]


# --- parse_release_json ---------------------------------------------------------------------------


def test_wheels_and_sdists_of_the_package_come_with_their_digests_and_links():
    text = _doc(
        {
            "0.1.0": [_entry(SDIST), _entry(WHEEL, DIGEST.upper())],
            "0.2.0": [_entry("axmem-0.2.0.tar.gz", "cd" * 32)],
        },
    )

    files = facts.parse_release_json(text, PACKAGE)

    assert [(f.name, f.version, f.sha256, f.yanked) for f in files] == [
        (SDIST, "0.1.0", DIGEST, False),
        (WHEEL, "0.1.0", DIGEST, False),
        ("axmem-0.2.0.tar.gz", "0.2.0", "cd" * 32, False),
    ]
    assert files[0].url == f"https://files.pythonhosted.org/packages/ab/cd/{SDIST}"


@pytest.mark.parametrize("text", ["", " \n"])
def test_a_blank_body_is_a_project_with_no_versions(text):
    assert facts.parse_release_json(text, PACKAGE) == []


def test_the_name_of_the_project_matches_in_any_pep_503_spelling():
    assert facts.parse_release_json(_doc({}, name="Demo_Pkg.X"), "demo-pkg-x") == []


def test_a_version_with_an_empty_file_list_has_no_files():
    assert facts.parse_release_json(_doc({"0.0.9": [], "0.1.0": [_entry(SDIST)]}), PACKAGE)[0].name == SDIST


def test_a_yanked_file_is_kept_and_marked():
    (file,) = facts.parse_release_json(_one(yanked=True), PACKAGE)

    assert file.yanked is True


def test_a_file_without_a_yanked_flag_is_not_yanked():
    entry = _entry(SDIST)
    del entry["yanked"]

    (file,) = facts.parse_release_json(_doc({"0.1.0": [entry]}), PACKAGE)

    assert file.yanked is False


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("<html>Maintenance</html>", "not the JSON of a project"),
        ("[]", "not the JSON of a project"),
        ('"text"', "not the JSON of a project"),
        ('{"releases": {}}', "not the JSON of a project"),
        ('{"info": {"name": "axmem"}}', "not the JSON of a project"),
        ('{"info": {"name": "axmem"}, "releases": []}', "releases is not an object"),
        ('{"info": {"name": 7}, "releases": {}}', "not the JSON of axmem"),
        (_doc({}, name="other-pkg"), "not the JSON of axmem"),
        (_doc({"bad version": [_entry(SDIST)]}), "a version that is not readable"),
        (_doc({"0.1.0": {}}), "a version that is not readable"),
        (_doc({"0.1.0": [_entry(SDIST), _entry(SDIST)]}), f"{SDIST} is listed twice"),
    ],
)
def test_an_answer_that_is_not_the_json_of_the_project_is_refused_not_read_as_empty(text, why):
    with pytest.raises(facts.FactsError, match=why):
        facts.parse_release_json(text, PACKAGE)


@pytest.mark.parametrize("raw", ["axmem-0.1.0.tar.gz", None, {"digests": {"sha256": DIGEST}}, {"filename": SDIST}])
def test_a_file_entry_that_is_not_whole_is_refused(raw):
    with pytest.raises(facts.FactsError, match=r"a file of 0.1.0 in the PyPI JSON is not whole"):
        facts.parse_release_json(_doc({"0.1.0": [raw]}), PACKAGE)


@pytest.mark.parametrize("name", ["../axmem-0.1.0.tar.gz", ".hidden.tar.gz", "a b.tar.gz", "", 5])
def test_a_file_name_that_is_not_a_plain_name_is_refused(name):
    with pytest.raises(facts.FactsError, match="name that is not a plain file name"):
        facts.parse_release_json(_one(filename=name), PACKAGE)


@pytest.mark.parametrize(
    "name",
    ["axmem-0.1.0.zip", "axmem-0.1.0.tar.gz.sigstore.json", "axmem-0.1.1.tar.gz", "axmem-0.1.0-py3-none-any.whl.asc"],
)
def test_a_file_that_is_neither_the_wheel_nor_the_sdist_of_its_version_is_refused(name):
    with pytest.raises(facts.FactsError, match=r"is neither the wheel nor the sdist of 0.1.0"):
        facts.parse_release_json(_one(filename=name), PACKAGE)


@pytest.mark.parametrize("sha", ["abc", "zz" * 32, "ab" * 33, None, 7])
def test_a_digest_that_is_not_a_sha256_is_refused(sha):
    with pytest.raises(facts.FactsError, match=f"{SDIST} in the PyPI JSON has no sha256 digest"):
        facts.parse_release_json(_one(digests={"sha256": sha}), PACKAGE)


@pytest.mark.parametrize(
    "url",
    [
        f"http://files.pythonhosted.org/packages/{SDIST}",
        f"https://elsewhere.invalid/packages/{SDIST}",
        f"https://files.pythonhosted.org.elsewhere.invalid/packages/{SDIST}",
        f"https://user@files.pythonhosted.org/packages/{SDIST}",
        f"https://user:secret@files.pythonhosted.org/packages/{SDIST}",
        f"https://files.pythonhosted.org:8443/packages/{SDIST}",
        f"https://files.pythonhosted.org\\@elsewhere.invalid/packages/{SDIST}",
        f"https://files.pythonhosted.org/packages/{SDIST} x",
        f"https://files.pythonhosted.org/packages/[a-b]/{SDIST}",  # curl would expand a range or a set into several downloads
        f"https://files.pythonhosted.org/packages/{{a,b}}/{SDIST}",
        "https://files.pythonhosted.org/",
        f"file:///etc/passwd/{SDIST}",
        None,
    ],
)
def test_a_link_that_is_not_https_on_the_file_host_itself_is_refused(url):
    with pytest.raises(facts.FactsError, match=r"linked at a URL that is not https://files.pythonhosted.org/"):
        facts.parse_release_json(_one(url=url), PACKAGE)


@pytest.mark.parametrize("yanked", ["true", 1, None])
def test_a_yanked_flag_that_is_not_a_boolean_is_refused(yanked):
    with pytest.raises(facts.FactsError, match="yanked flag that is not true or false"):
        facts.parse_release_json(_one(yanked=yanked), PACKAGE)


# --- read_index -----------------------------------------------------------------------------------


def _saved(tmp_path, text):
    path = tmp_path / "pypi.json"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_the_files_of_the_package_are_read_from_a_saved_document(tmp_path):
    saved = _saved(tmp_path, _doc({"0.1.0": [_entry(SDIST)]}))

    assert [f.name for f in facts.read_index(saved, PACKAGE)] == [SDIST]


def test_an_empty_file_is_the_404_of_a_project_pypi_does_not_have(tmp_path):
    assert facts.read_index(_saved(tmp_path, ""), PACKAGE) == []


def test_a_saved_page_that_is_not_the_json_is_refused_not_read_as_empty(tmp_path):
    with pytest.raises(facts.FactsError, match="not the JSON of a project"):
        facts.read_index(_saved(tmp_path, "<html><h1>Maintenance</h1></html>"), PACKAGE)


def test_a_document_that_cannot_be_read_is_a_facts_error(tmp_path):
    with pytest.raises(facts.FactsError, match="cannot read the PyPI JSON"):
        facts.read_index(str(tmp_path / "missing.json"), PACKAGE)
    (tmp_path / "bytes.json").write_bytes(b"\xff\xfe")
    with pytest.raises(facts.FactsError, match="cannot read the PyPI JSON"):
        facts.read_index(str(tmp_path / "bytes.json"), PACKAGE)


# --- publish_runs ---------------------------------------------------------------------------------


def _publish_row(run_id, **changes):
    return {
        "databaseId": run_id,
        "event": "workflow_dispatch",
        "headBranch": "v1.0.0",
        "headSha": "abc",
        **changes,
    }


def test_only_the_dispatches_of_the_tag_at_the_commit_are_kept(monkeypatch):
    seen = []
    rows = [
        _publish_row(1),
        _publish_row(2, event="push"),
        _publish_row(3, headBranch="v1.0.1"),
        _publish_row(4, headSha="def"),
        _publish_row(5),
    ]
    monkeypatch.setattr(facts, "gh_json", lambda argv: seen.append(argv) or rows)

    kept = facts.publish_runs("o/r", "abc", "v1.0.0")

    assert [row["databaseId"] for row in kept] == [1, 5]
    (argv,) = seen
    assert argv[argv.index("--workflow") + 1] == "publish.yml"
    assert argv[argv.index("--event") + 1] == "workflow_dispatch"
    assert argv[argv.index("--branch") + 1] == "v1.0.0"
    assert argv[argv.index("--commit") + 1] == "abc"
    assert argv[argv.index("-R") + 1] == "o/r"
    assert {"databaseId", "updatedAt", "headSha"} <= set(argv[argv.index("--json") + 1].split(","))
