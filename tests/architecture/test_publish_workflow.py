# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The workflow that uploads a release to PyPI proves the signed files, and only then lets PyPI have them.

``publish.yml`` is the only workflow that uploads to PyPI and the one PyPI knows as the trusted publisher of axmem. It
runs when ``scripts/release-pypi.sh`` dispatches it on a release tag. It builds nothing: job ``verify`` (no identity
token) checks the files that ``release.yml`` signed against the committed manifest, the cosign identity and the release
guard; job ``publish`` (environment ``pypi``, the only job with the identity token) checks the same sums again and
uploads. These tests hold that shape, the pins of every action and the places where an input could reach a shell.
``test_release_workflow.py`` holds the signing side and ``test_workflows.py`` the hosted-runner rules.
"""

import re
from pathlib import Path

import pytest
import yaml

from scripts.release import facts

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "publish.yml"
RELEASE = WORKFLOWS / "release.yml"
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<what>\S+)(?P<rest>.*)$")
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
VERSION_COMMENT = re.compile(r"\s+# v\d+\.\d+\.\d+")


def _load(path):
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["triggers"] = document.get("on", document.get(True))  # YAML reads a bare `on` as the boolean True
    return document


def _workflow():
    return _load(WORKFLOW)


def _job(name):
    return _workflow()["jobs"][name]


def _names(job):
    return [step["name"] for step in _job(job)["steps"]]


def _step(job, name):
    (step,) = [step for step in _job(job)["steps"] if step["name"] == name]
    return step


def _runs():
    return [(job, step["name"], step["run"]) for job, body in _workflow()["jobs"].items() for step in body["steps"] if "run" in step]


def _text():
    return WORKFLOW.read_text(encoding="utf-8")


def _code():
    """The workflow without its comment lines: what the words below are looked for in."""
    return "\n".join(line for line in _text().splitlines() if not line.lstrip().startswith("#"))


def test_the_workflow_is_the_file_that_pypi_and_the_release_script_name():
    assert WORKFLOW.name == facts.PUBLISH_WORKFLOW
    assert _workflow()["name"] == "Publish to PyPI"


def test_it_runs_only_when_somebody_dispatches_it_with_the_tag():
    triggers = _workflow()["triggers"]

    assert set(triggers) == {"workflow_dispatch"}  # no release, no push, no schedule: a release made by hand starts nothing
    (tag,) = triggers["workflow_dispatch"]["inputs"].values()  # the tag is the only input
    assert set(triggers["workflow_dispatch"]["inputs"]) == {"tag"}
    assert (tag["required"], tag["type"]) == (True, "string")


def test_the_token_is_read_only_and_the_jobs_are_the_two_that_the_release_script_follows():
    document = _workflow()

    assert document["permissions"] == {"contents": "read"}
    assert set(document["jobs"]) == {"verify", "publish"}


def test_a_ref_is_published_one_at_a_time_and_a_run_is_never_cancelled_half_way():
    concurrency = _workflow()["concurrency"]

    assert concurrency["group"] == "publish-${{ github.ref }}"  # keyed on the ref, never on the input
    assert concurrency["cancel-in-progress"] is False


def test_the_verify_job_has_neither_an_identity_token_nor_an_environment():
    job = _job("verify")

    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert "environment" not in job
    assert "needs" not in job
    assert job["outputs"] == {"sums": "${{ steps.summary.outputs.sums }}"}


def test_the_publish_job_waits_for_verify_and_for_the_reviewers_and_holds_the_identity_token_alone():
    job = _job("publish")

    assert job["needs"] == "verify"
    assert job["environment"] == "pypi"
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert job["env"] == {"SUMS": "${{ needs.verify.outputs.sums }}"}
    assert _names("publish") == [
        "Checkout the tag commit",
        "Download the verified files",
        "Check the files are the ones that were verified",
        "Leave out the files that PyPI already holds",
        "Publish to PyPI",
    ]


def test_the_steps_of_the_verify_job_are_the_contract_that_the_other_tests_and_the_reviewers_rely_on():
    assert _names("verify") == [
        "Check the run is on a release tag",
        "Checkout the tag commit",
        "Check the checkout is the commit of the tag",
        "Set up Python",
        "List the files of the manifest",
        "Download the signed release files",
        "Verify the digests against the manifest",
        "Install cosign",
        "Verify the signatures against release.yml at the tag",
        "Assemble dist with the signed files and nothing else",
        "Run the release guard on the tag commit",
        "Check what the package holds",
        "Summarise what will be published",
        "Keep the verified files for the upload job",
    ]


def test_the_run_must_be_on_a_release_tag_and_the_tag_of_the_input_must_be_that_tag():
    run = _step("verify", "Check the run is on a release tag")["run"]

    assert _names("verify")[0] == "Check the run is on a release tag"
    assert r"^v${part}\.${part}\.${part}((a|b|rc)${part})?(\.post${part})?(\.dev${part})?$" in run
    assert '"$GITHUB_REF_TYPE" == "tag"' in run
    assert '"$GITHUB_REF" == "refs/tags/${TAG}"' in run
    assert run.count("exit 1") == 2


def test_the_checkout_is_the_commit_that_the_run_started_on_and_never_a_branch_of_the_tags_name():
    checkout = _step("verify", "Checkout the tag commit")
    check = _step("verify", "Check the checkout is the commit of the tag")["run"]

    assert checkout["with"] == {"ref": "${{ github.sha }}", "fetch-depth": 0, "persist-credentials": False}
    assert "git rev-parse HEAD" in check
    assert '== "$GITHUB_SHA"' in check
    assert 'git rev-parse "refs/tags/${TAG}^{commit}"' in check
    assert _job("verify")["env"] == {"TAG": "${{ inputs.tag }}"}


def test_the_manifest_of_the_tag_decides_which_files_are_published():
    run = _step("verify", "List the files of the manifest")["run"]

    assert 'manifest.get("package"), manifest.get("tag"), manifest.get("version")' in run
    assert "python3 -m scripts.release.guard artefacts --root ." in run
    assert "axmem-${version}-py3-none-any.whl" in run
    assert "axmem-${version}.tar.gz" in run
    assert '"${RUNNER_TEMP}/artefacts.txt"' in run


def test_the_files_come_from_the_release_that_release_yml_signed_and_are_proven_before_they_are_trusted():
    download = _step("verify", "Download the signed release files")
    digests = _step("verify", "Verify the digests against the manifest")
    names = _names("verify")

    assert download["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert 'gh release download "$TAG" --repo "$GITHUB_REPOSITORY" --dir release' in " ".join(download["run"].replace("\\", " ").split())
    assert digests["run"] == "python3 -m scripts.release.digests verify --manifest release-digests.json --dist release"
    assert names.index(download["name"]) < names.index(digests["name"]) < names.index("Install cosign")
    assert names.index("Verify the signatures against release.yml at the tag") < names.index(
        "Assemble dist with the signed files and nothing else",
    )


def test_cosign_is_the_version_of_release_yml_and_the_installer_reads_it_from_the_environment():
    installer = _step("verify", "Install cosign")

    assert _workflow()["env"]["COSIGN_VERSION"] == _load(RELEASE)["env"]["COSIGN_VERSION"]
    assert re.fullmatch(r"v3\.\d+\.\d+", _workflow()["env"]["COSIGN_VERSION"])
    assert installer["with"] == {"cosign-release": "${{ env.COSIGN_VERSION }}"}
    assert installer["uses"].startswith("sigstore/cosign-installer@")


def test_a_signature_counts_only_when_it_is_the_one_that_the_push_run_of_release_yml_made_on_this_commit():
    run = _step("verify", "Verify the signatures against release.yml at the tag")["run"]
    flat = " ".join(run.replace("\\", " ").split())

    assert "cosign verify-blob" in run
    assert 'workflows/release.yml@refs/tags/${TAG}"' in run
    assert '--certificate-oidc-issuer "https://token.actions.githubusercontent.com"' in run
    assert '--certificate-github-workflow-repository "${GITHUB_REPOSITORY}"' in flat
    assert '--certificate-github-workflow-ref "refs/tags/${TAG}"' in flat
    assert '--certificate-github-workflow-sha "${GITHUB_SHA}"' in flat
    assert "--certificate-github-workflow-trigger push" in flat
    assert '--bundle "release/${name}.sigstore.json"' in flat
    assert facts.BUNDLE_SUFFIX == ".sigstore.json"


def test_dist_holds_the_signed_files_of_the_manifest_and_nothing_else():
    run = _step("verify", "Assemble dist with the signed files and nothing else")["run"]

    assert "mkdir dist" in run
    assert 'cp -- "release/${name}" "dist/${name}"' in run
    assert "*" not in run.replace("${names[@]}", "")  # no glob: only the names of the manifest are copied
    assert '"${RUNNER_TEMP}/artefacts.txt"' in run


def test_the_release_guard_runs_again_on_the_tag_commit_with_the_files_that_will_be_uploaded():
    step = _step("verify", "Run the release guard on the tag commit")
    flat = " ".join(step["run"].replace("\\", " ").split())

    assert step["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert "python3 -m scripts.release.guard check" in flat
    assert '--sha "$GITHUB_SHA"' in flat
    assert '--tag "$TAG"' in flat
    assert '--repo "$GITHUB_REPOSITORY"' in flat
    assert "--dist dist" in flat


def test_the_package_gate_is_the_same_command_as_in_the_other_workflows():
    assert _step("verify", "Check what the package holds")["run"] == (
        "python3 -m scripts.quality dist-check dist"
    )
    assert _step("verify", "Check what the package holds")["run"] == next(
        step["run"] for step in _load(RELEASE)["jobs"]["sign-artefacts"]["steps"] if step.get("name") == "Check what the package holds"
    )


def test_no_step_of_the_workflow_runs_uv_or_installs_it():
    assert "setup-uv" not in _code()
    assert not re.search(r"\buv\b", " ".join(run for _, _, run in _runs()))  # the checker is the standard library of the tag


def test_the_sums_are_taken_only_after_the_manifest_has_vouched_for_dist_once_more():
    step = _step("verify", "Summarise what will be published")
    run = step["run"]

    assert run.index("python3 -m scripts.release.digests verify --manifest release-digests.json --dist dist") < run.index("sha256sum")
    assert _names("verify")[-2:] == ["Summarise what will be published", "Keep the verified files for the upload job"]


def test_the_summary_and_the_sums_for_the_next_job_are_written_in_the_same_step():
    step = _step("verify", "Summarise what will be published")

    assert step["id"] == "summary"
    assert "sha256sum" in step["run"]
    assert "GITHUB_STEP_SUMMARY" in step["run"]
    assert "sums<<SUMS_END" in step["run"]
    assert "GITHUB_OUTPUT" in step["run"]


def test_every_action_is_a_commit_with_its_version_in_a_trailing_comment():
    lines = [USES.match(line) for line in _text().splitlines() if USES.match(line)]

    assert len(lines) == 7
    for found in lines:
        assert PINNED.fullmatch(found["what"]), found["what"]
        assert VERSION_COMMENT.fullmatch(found["rest"].rstrip()), found["what"]
    assert not any("PIN[" in line for line in _text().splitlines())


def test_the_same_actions_are_the_same_commits_in_release_yml():
    def pins(path):
        found = [USES.match(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return {match["what"].split("@")[0]: (match["what"], match["rest"].strip()) for match in found if match}

    ours, theirs = pins(WORKFLOW), pins(RELEASE)
    shared = set(ours) & set(theirs)

    assert shared == {"actions/checkout", "actions/setup-python", "sigstore/cosign-installer"}
    assert all(ours[name] == theirs[name] for name in shared)


def test_the_token_is_given_to_the_steps_that_call_gh_and_to_nothing_else():
    document = _workflow()
    steps_with = {
        step["name"] for job in document["jobs"].values() for step in job["steps"] if "GH_TOKEN" in step.get("env", {})
    }

    assert "GH_TOKEN" not in document.get("env", {})
    assert all("GH_TOKEN" not in job.get("env", {}) for job in document["jobs"].values())
    assert steps_with == {"Download the signed release files", "Run the release guard on the tag commit"}
    assert _code().count("GH_TOKEN") == 2  # the two env keys; no comment and no script names it
    assert "GH_TOKEN" not in "\n".join(run for _, _, run in _runs())


@pytest.mark.parametrize("word", ["secrets.", "uv build", "uv sync", "uv publish", "twine"])
def test_the_workflow_builds_nothing_installs_nothing_and_holds_no_credential(word):
    assert word not in _code()


@pytest.mark.parametrize(("job", "name", "run"), _runs(), ids=[f"{job}:{number}" for number, (job, _, _) in enumerate(_runs())])
def test_no_input_or_context_is_written_into_a_script(job, name, run):
    assert "${{" not in run, f"{job}: {name} writes an expression into a shell script; pass it through env"


def test_the_inputs_reach_the_scripts_only_through_the_environment():
    document = _workflow()
    expressions = re.findall(r"\$\{\{\s*([^}]+?)\s*\}\}", _code())

    assert "inputs.tag" in expressions
    assert document["jobs"]["verify"]["env"]["TAG"] == "${{ inputs.tag }}"
    assert document["jobs"]["publish"]["env"]["SUMS"] == "${{ needs.verify.outputs.sums }}"
    assert expressions.count("inputs.tag") == 1


def test_the_workflow_is_a_hosted_one_without_a_container_a_gpu_or_the_tests_of_the_gpu_suites():
    text = _code().lower()

    assert not re.search(r"docker|nvidia-smi|tests/integration|mutmut", text)
    assert all(job["runs-on"] == "ubuntu-latest" for job in _workflow()["jobs"].values())
    assert not {"container", "services"} & {key for job in _workflow()["jobs"].values() for key in job}


def test_no_other_workflow_than_the_signing_the_two_publishing_and_the_scorecard_ones_asks_for_an_identity_token():
    asking = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        document = _load(path)
        grants = [document.get("permissions") or {}, *((job.get("permissions") or {}) for job in document["jobs"].values())]
        if any(isinstance(grant, dict) and grant.get("id-token") == "write" for grant in grants):
            asking.add(path.name)

    assert asking == {"release.yml", "publish.yml", "publish-testpypi.yml", "scorecard.yml"}
    assert "id-token" not in (_load(WORKFLOWS / "scorecard.yml").get("permissions") or {})  # its one job holds it, not the top
