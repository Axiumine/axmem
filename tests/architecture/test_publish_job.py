"""The job of ``publish.yml`` that uploads: what it takes from the job that verified, what it leaves out, and the upload itself.

``test_publish_workflow.py`` holds the shape of the whole workflow and the job ``verify``. The job ``publish`` (environment
``pypi``, the only one with the identity token) checks out the tag commit for the small PyPI reader of the guard, takes the
files that ``verify`` kept, checks them against its sums, leaves out the files PyPI lists already (a name PyPI has used is
refused for good, and the upload action stops at the first refusal) and uploads what is left, last.
"""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "publish.yml"


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job(name):
    return _workflow()["jobs"][name]


def _names(job):
    return [step["name"] for step in _job(job)["steps"]]


def _step(job, name):
    (step,) = [step for step in _job(job)["steps"] if step["name"] == name]
    return step


def test_the_files_travel_between_the_jobs_as_one_short_lived_artifact_named_dist():
    keep = _step("verify", "Keep the verified files for the upload job")
    download = _step("publish", "Download the verified files")

    assert keep["with"] == {"name": "dist", "path": "dist/", "if-no-files-found": "error", "retention-days": 1}
    assert download["with"] == {"name": "dist", "path": "dist"}
    assert keep["uses"].startswith("actions/upload-artifact@")
    assert download["uses"].startswith("actions/download-artifact@")


def test_the_upload_job_checks_that_the_files_are_the_ones_that_verify_listed_and_that_there_are_no_others():
    run = _step("publish", "Check the files are the ones that were verified")["run"]

    assert '[[ -n "$SUMS" ]]' in run
    assert 'sha256sum --check --strict "${RUNNER_TEMP}/sums.txt"' in run
    assert "find dist -type f | wc -l" in run
    assert run.count("exit 1") == 2


def test_the_publish_job_checks_out_the_tag_commit_before_it_downloads_anything():
    checkout = _step("publish", "Checkout the tag commit")

    assert _names("publish")[0] == checkout["name"]  # the checkout cleans the workspace
    assert checkout["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
    assert checkout["uses"].startswith("actions/checkout@")


def test_a_file_that_pypi_holds_already_is_left_out_of_the_upload_and_one_it_holds_with_other_bytes_fails_the_run():
    step = _step("publish", "Leave out the files that PyPI already holds")
    flat = " ".join(step["run"].replace("\\", " ").split())
    names = _names("publish")

    assert step["id"] == "held"
    assert names.index("Check the files are the ones that were verified") < names.index(step["name"]) < names.index("Publish to PyPI")
    assert "curl -sSL --globoff --proto =https --proto-redir =https" in flat
    assert '"https://pypi.org/pypi/axmem/json"' in flat
    assert '404) : >"$json" ;;' in step["run"]  # a project PyPI does not know yet holds nothing
    assert '*) echo "::error::PyPI answered' in step["run"]
    assert 'python3 -m scripts.release.guard pypi-missing --json "$json" --package axmem --version "${GITHUB_REF_NAME#v}" --dist dist' in flat
    assert 'echo "remaining=$(find dist -type f | wc -l)" >>"$GITHUB_OUTPUT"' in step["run"]
    assert step["run"].count("exit 1") == 3


def test_the_upload_runs_only_when_a_file_is_left_to_send():
    assert _step("publish", "Publish to PyPI")["if"] == "steps.held.outputs.remaining != '0'"


def test_the_upload_is_the_last_step_of_the_workflow_and_the_only_pypi_action_with_nothing_but_the_directory_and_the_hash():
    steps = _job("publish")["steps"]
    pypi = [step for job in _workflow()["jobs"].values() for step in job["steps"] if "pypa/" in str(step.get("uses", ""))]

    assert pypi == [steps[-1]]
    assert steps[-1]["uses"].startswith("pypa/gh-action-pypi-publish@")
    assert steps[-1]["with"] == {"packages-dir": "dist", "print-hash": True}  # a name PyPI holds with other bytes must fail
    assert not {"skip-existing", "user", "password", "repository-url"} & set(steps[-1]["with"])
    assert "env" not in steps[-1]
