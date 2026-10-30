# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The rehearsal workflow on TestPyPI is publish.yml with the listed differences and no others.

``publish-testpypi.yml`` uploads the same signed bytes of a release as ``publish.yml`` to TestPyPI, by trusted publishing in
the environment ``testpypi``, so that the publisher, the environment and the upload are tried before the PyPI upload that
cannot be taken back. It is advisory, not a gate, and neither workflow names the other. Its two jobs are those of
``publish.yml`` step for step, so the test below builds what the file must be from ``publish.yml`` and one closed table of
differences, and compares the two whole: a change to one file that does not reach the other fails here, and a difference
that the table does not list fails here. The direct pins that follow do not read ``publish.yml``, so that the two files
cannot drift together past the facts that matter: the cosign identity of ``release.yml`` and every flag that ties a
signature to the push run on this commit, the host, the environment, the repository URL and the absence of any credential.
``test_publish_workflow.py`` holds the shape of ``publish.yml`` and ``test_workflows.py`` the hosted-runner rules.
"""

import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

from scripts.release import facts, testpypi

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PYPI = WORKFLOWS / "publish.yml"
REHEARSAL = WORKFLOWS / "publish-testpypi.yml"
RELEASE = WORKFLOWS / "release.yml"
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<what>\S+)(?P<rest>.*)$")
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
VERSION_COMMENT = re.compile(r"\s+# v\d+\.\d+\.\d+")
UPLOAD_ACTION = "pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086"

# Every difference between the two files, as the text of publish.yml that changes, what it becomes, and how many times it
# occurs (one string per place). The environment and the one added key of the upload step are the two structural ones, below.
DIFFERENCES = [
    (
        "Release tag to publish (v<version>); dispatch the workflow on this same tag",
        "Release tag to rehearse on TestPyPI (v<version>); dispatch the workflow on this same tag",
        1,
    ),
    ("publish-${{ github.ref }}", "publish-testpypi-${{ github.ref }}", 1),
    ("gh workflow run publish.yml --ref", "gh workflow run publish-testpypi.yml --ref", 1),
    ("### axmem ${TAG} to PyPI", "### axmem ${TAG} to TestPyPI (rehearsal, PyPI is not touched)", 1),
    ("Leave out the files that PyPI already holds", "Leave out the files that TestPyPI already holds", 1),
    ("${RUNNER_TEMP}/pypi.json", "${RUNNER_TEMP}/testpypi.json", 1),
    ('"https://pypi.org/pypi/axmem/json"', '"https://test.pypi.org/pypi/axmem/json"', 1),
    ("PyPI could not be asked what it holds", "TestPyPI could not be asked what it holds", 1),
    ("PyPI answered ${code} to the read", "TestPyPI answered ${code} to the read", 1),
    ("guard pypi-missing", "guard testpypi-missing", 1),
    ("Publish to PyPI", "Publish to TestPyPI", 2),  # the name of the workflow and the name of the upload step
]
REPOSITORY_URL = "https://test.pypi.org/legacy/"


def _load(path):
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["triggers"] = document.get("on", document.get(True))  # YAML reads a bare `on` as the boolean True
    return document


def _text(path=REHEARSAL):
    return path.read_text(encoding="utf-8")


def _code(path=REHEARSAL):
    """The workflow without its comment lines: what the words below are looked for in."""
    return "\n".join(line for line in _text(path).splitlines() if not line.lstrip().startswith("#"))


def _job(name):
    return _load(REHEARSAL)["jobs"][name]


def _step(job, name):
    (step,) = [step for step in _job(job)["steps"] if step["name"] == name]
    return step


def _strings(value, change):
    """``value`` with ``change`` applied to every string in it, the keys of mappings too."""
    if isinstance(value, str):
        return change(value)
    if isinstance(value, list):
        return [_strings(item, change) for item in value]
    if isinstance(value, dict):
        return {_strings(key, change): _strings(item, change) for key, item in value.items()}
    return value


def _expected():
    """What ``publish-testpypi.yml`` must be, made from ``publish.yml`` and the table of differences alone."""
    found = Counter()
    pattern = re.compile("|".join(re.escape(old) for old, _, _ in sorted(DIFFERENCES, key=lambda row: -len(row[0]))))
    news = {old: new for old, new, _ in DIFFERENCES}

    def change(text):
        def one(match):
            found[match.group(0)] += 1
            return news[match.group(0)]

        return pattern.sub(one, text)

    document = _strings(yaml.safe_load(_text(PYPI)), change)
    document["triggers"] = document.get("on", document.get(True))
    assert found == {old: count for old, _, count in DIFFERENCES}, (
        "a difference of the table is missing from publish.yml, or occurs elsewhere"
    )
    publish = document["jobs"]["publish"]
    assert publish["environment"] == "pypi"
    publish["environment"] = "testpypi"
    (upload,) = [step for step in publish["steps"] if step["name"] == "Publish to TestPyPI"]
    assert "repository-url" not in upload["with"]
    upload["with"] = {"repository-url": REPOSITORY_URL, **upload["with"]}
    return document


# --- the same workflow, with the listed differences ---------------------------------------------------------------


def test_the_two_workflows_hold_the_same_keys():
    assert set(_load(REHEARSAL)) == set(_expected())


@pytest.mark.parametrize("key", ["name", "triggers", "permissions", "concurrency", "env"])
def test_the_top_of_the_two_workflows_is_the_same_but_for_the_listed_differences(key):
    assert _load(REHEARSAL)[key] == _expected()[key]


@pytest.mark.parametrize("job", ["verify", "publish"])
def test_the_steps_of_a_job_are_the_same_in_the_same_order_but_for_the_listed_differences(job):
    actual, expected = _load(REHEARSAL)["jobs"][job]["steps"], _expected()["jobs"][job]["steps"]

    assert [step["name"] for step in actual] == [step["name"] for step in expected]
    for have, want in zip(actual, expected, strict=True):
        assert have == want, have["name"]


@pytest.mark.parametrize("job", ["verify", "publish"])
def test_a_job_has_the_same_settings_but_for_the_listed_differences(job):
    actual, expected = _load(REHEARSAL)["jobs"][job], _expected()["jobs"][job]

    assert {key for key in actual if key != "steps"} == {key for key in expected if key != "steps"}
    assert actual == expected


def test_the_whole_document_is_what_publish_yml_and_the_table_make():
    assert _load(REHEARSAL) == _expected()


def test_every_difference_of_the_table_is_in_the_file_and_the_old_text_is_in_no_place_of_it():
    code = _code()

    for old, new, count in DIFFERENCES:
        assert code.count(new) >= count, new
        assert old not in code.replace(new, ""), old


def test_the_table_is_closed_so_that_a_new_difference_is_a_change_of_this_file():
    olds = [row[0] for row in DIFFERENCES]

    assert len(DIFFERENCES) == 11
    assert len(set(olds)) == len(olds)
    assert sum(count for _, _, count in DIFFERENCES) == 12


# --- what it is ----------------------------------------------------------------------------------------------------


def test_the_workflow_is_the_file_and_the_environment_that_the_rehearsal_names():
    row = facts.INDEXES["testpypi"]

    assert REHEARSAL.name == testpypi.TESTPYPI_WORKFLOW == row.workflow == "publish-testpypi.yml"
    assert _load(REHEARSAL)["name"] == "Publish to TestPyPI"
    assert _job("publish")["environment"] == testpypi.TESTPYPI_ENVIRONMENT == row.environment == "testpypi"
    assert re.findall(r"^\s*environment:\s*(\S+)\s*$", _code(), re.MULTILINE) == ["testpypi"]


def test_it_runs_only_when_somebody_dispatches_it_with_the_tag():
    triggers = _load(REHEARSAL)["triggers"]

    assert set(triggers) == {"workflow_dispatch"}  # no release, no push, no schedule
    assert set(triggers["workflow_dispatch"]["inputs"]) == {"tag"}  # the tag is the only input
    tag = triggers["workflow_dispatch"]["inputs"]["tag"]
    assert (tag["required"], tag["type"]) == (True, "string")


def test_a_rehearsal_of_a_ref_runs_one_at_a_time_and_is_never_cancelled_half_way():
    group = _load(REHEARSAL)["concurrency"]

    assert group == {"group": "publish-testpypi-${{ github.ref }}", "cancel-in-progress": False}
    assert group["group"] != _load(PYPI)["concurrency"]["group"]  # a rehearsal does not queue behind the upload to PyPI, nor the reverse


def test_the_verify_job_has_neither_an_identity_token_nor_an_environment():
    job = _job("verify")

    assert "environment" not in job
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert "id-token" not in str(_load(REHEARSAL)["permissions"])


def test_the_identity_token_is_the_publish_job_s_alone_and_the_token_of_the_workflow_is_read_only():
    document = _load(REHEARSAL)

    assert document["permissions"] == {"contents": "read"}
    assert set(document["jobs"]) == {"verify", "publish"}
    assert _job("publish")["permissions"] == {"contents": "read", "id-token": "write"}
    assert _job("publish")["needs"] == "verify"
    assert _code().count("id-token") == 1


# --- the signatures are release.yml's, whoever uploads -------------------------------------------------------------


SIGNATURE_FLAGS = [
    '--certificate-identity "https://github.com/${GITHUB_REPOSITORY}/.github/workflows/release.yml@refs/tags/${TAG}"',
    '--certificate-oidc-issuer "https://token.actions.githubusercontent.com"',
    '--certificate-github-workflow-repository "${GITHUB_REPOSITORY}"',
    '--certificate-github-workflow-ref "refs/tags/${TAG}"',
    '--certificate-github-workflow-sha "${GITHUB_SHA}"',
    "--certificate-github-workflow-trigger push",
]


@pytest.mark.parametrize("flag", SIGNATURE_FLAGS)
def test_a_signature_counts_only_when_it_is_the_one_that_the_push_run_of_release_yml_made_on_this_commit(flag):
    assert flag in _step("verify", "Verify the signatures against release.yml at the tag")["run"]


def test_no_flag_of_the_signature_check_is_left_out_or_relaxed_and_the_signer_is_release_yml():
    run = _step("verify", "Verify the signatures against release.yml at the tag")["run"]

    assert run.count("--certificate-") == len(SIGNATURE_FLAGS)  # the identity, the issuer and four more: nothing is added or left out
    assert "publish" not in run  # the signer is release.yml; the rehearsal is not a signer
    assert "cosign verify-blob" in run
    assert "--insecure" not in run


def test_cosign_is_the_version_of_release_yml_and_publish_yml():
    versions = {path.name: _load(path)["env"]["COSIGN_VERSION"] for path in (REHEARSAL, PYPI, RELEASE)}

    assert len(set(versions.values())) == 1, versions
    assert _step("verify", "Install cosign")["with"]["cosign-release"] == "${{ env.COSIGN_VERSION }}"


def test_the_release_guard_runs_again_on_the_tag_commit_with_the_files_that_will_be_uploaded():
    run = _step("verify", "Run the release guard on the tag commit")["run"]

    assert "python3 -m scripts.release.guard check" in run
    for flag in ('--repo "$GITHUB_REPOSITORY"', '--sha "$GITHUB_SHA"', '--tag "$TAG"', "--root .", "--remote origin", "--dist dist"):
        assert flag in run, flag
    assert _step("verify", "Run the release guard on the tag commit")["env"] == {"GH_TOKEN": "${{ github.token }}"}


def test_the_run_must_be_on_a_release_tag_and_the_dispatch_hint_names_this_workflow():
    run = _step("verify", "Check the run is on a release tag")["run"]

    assert '[[ "$GITHUB_REF_TYPE" == "tag" && "$GITHUB_REF" == "refs/tags/${TAG}" ]]' in run
    assert "gh workflow run publish-testpypi.yml --ref ${TAG} -f tag=${TAG}" in run
    assert "publish.yml" not in run.replace("publish-testpypi.yml", "")


# --- what is uploaded, and where -----------------------------------------------------------------------------------


def test_the_upload_step_is_the_pinned_action_with_the_testpypi_url_and_without_a_credential_or_a_skip():
    step = _step("publish", "Publish to TestPyPI")

    assert step["uses"] == UPLOAD_ACTION
    assert f"{UPLOAD_ACTION} # v1.14.3" in _text()
    assert step["with"] == {"repository-url": "https://test.pypi.org/legacy/", "packages-dir": "dist", "print-hash": True}
    assert step["if"] == "steps.held.outputs.remaining != '0'"
    assert set(step) == {"name", "if", "uses", "with"}  # no env, no secret, no continue-on-error
    assert step["with"]["repository-url"] == REPOSITORY_URL
    assert re.findall(r"repository-url:\s*(\S+)", _code()) == [REPOSITORY_URL]


def test_the_upload_action_is_the_one_publish_yml_pins():
    ours = [match["what"] for match in map(USES.match, _text().splitlines()) if match]
    theirs = [match["what"] for match in map(USES.match, _text(PYPI).splitlines()) if match]

    assert ours == theirs
    assert UPLOAD_ACTION in ours
    assert all(PINNED.fullmatch(what) for what in ours)


def test_every_action_is_a_commit_with_its_version_in_a_trailing_comment():
    lines = [USES.match(line) for line in _text().splitlines() if USES.match(line)]

    assert len(lines) == 7
    for found in lines:
        assert PINNED.fullmatch(found["what"]), found["what"]
        assert VERSION_COMMENT.fullmatch(found["rest"].rstrip()), found["what"]


def test_the_files_already_on_testpypi_are_read_from_testpypi_and_the_guard_leaves_them_out():
    step = _step("publish", "Leave out the files that TestPyPI already holds")
    run = step["run"]

    assert step["id"] == "held"
    assert re.findall(r"https?://[^\s\"')]+", run) == ["https://test.pypi.org/pypi/axmem/json"]
    assert 'python3 -m scripts.release.guard testpypi-missing --json "$json" --package axmem' in run
    assert "pypi-missing" not in run.replace("testpypi-missing", "")
    assert "--proto =https --proto-redir =https" in run
    assert "skip-existing" not in run


def test_the_files_that_are_uploaded_are_the_ones_the_verify_job_checked():
    run = _step("publish", "Check the files are the ones that were verified")["run"]

    assert 'sha256sum --check --strict "${RUNNER_TEMP}/sums.txt"' in run
    assert _job("publish")["env"] == {"SUMS": "${{ needs.verify.outputs.sums }}"}
    assert _step("publish", "Download the verified files")["with"]["name"] == "dist"


def test_the_summary_says_testpypi_so_that_a_reviewer_cannot_take_it_for_the_upload_to_pypi():
    run = _step("verify", "Summarise what will be published")["run"]

    assert 'echo "### axmem ${TAG} to TestPyPI (rehearsal, PyPI is not touched)"' in run
    assert "to PyPI" not in run.replace("to TestPyPI", "")


# --- it and the upload to PyPI do not meet -------------------------------------------------------------------------


def test_the_rehearsal_names_no_pypi_environment_host_workflow_or_publisher():
    code = _code()

    assert not re.search(r"environment:\s*pypi\b", code)
    assert not re.search(r"(?<![\w.-])pypi\.org", code)
    assert not re.search(r"(?<![\w-])files\.pythonhosted\.org", code)
    assert not re.search(r"(?<![\w-])publish\.yml", code)
    assert "https://upload.pypi.org" not in code


def test_publish_yml_names_nothing_of_the_rehearsal():
    text = _text(PYPI).lower()

    assert "testpypi" not in text
    assert "test.pypi" not in text
    assert "test-files" not in text
    assert "rehearsal" not in text


def test_no_other_workflow_names_the_rehearsal_either():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path != REHEARSAL:
            assert "testpypi" not in path.read_text(encoding="utf-8").lower(), path.name


@pytest.mark.parametrize(
    "word",
    ["secrets.", "uv build", "uv sync", "uv publish", "twine", "password", "skip-existing", "skip_existing", "api-token", "user:"],
)
def test_the_workflow_builds_nothing_installs_nothing_and_holds_no_credential(word):
    assert word not in _code()


def test_the_token_is_given_to_the_steps_that_call_gh_and_to_nothing_else():
    document = _load(REHEARSAL)
    steps_with = {step["name"] for job in document["jobs"].values() for step in job["steps"] if "GH_TOKEN" in step.get("env", {})}

    assert "GH_TOKEN" not in document.get("env", {})
    assert all("GH_TOKEN" not in job.get("env", {}) for job in document["jobs"].values())
    assert steps_with == {"Download the signed release files", "Run the release guard on the tag commit"}
    assert _code().count("GH_TOKEN") == 2


def test_the_inputs_reach_the_scripts_only_through_the_environment():
    document = _load(REHEARSAL)
    expressions = re.findall(r"\$\{\{\s*([^}]+?)\s*\}\}", _code())
    runs = [step["run"] for job in document["jobs"].values() for step in job["steps"] if "run" in step]

    assert expressions.count("inputs.tag") == 1
    assert document["jobs"]["verify"]["env"]["TAG"] == "${{ inputs.tag }}"
    assert not [run for run in runs if "${{" in run]


def test_the_workflow_is_a_hosted_one_without_a_container_a_gpu_or_the_tests_of_the_gpu_suites():
    assert not re.search(r"docker|nvidia-smi|tests/integration|mutmut", _code().lower())
    assert all(job["runs-on"] == "ubuntu-latest" for job in _load(REHEARSAL)["jobs"].values())
    assert not {"container", "services"} & {key for job in _load(REHEARSAL)["jobs"].values() for key in job}
