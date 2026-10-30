# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The workflow that signs a release is the one that scripts/release-pypi.sh and scripts/release/ wait for.

``release.yml`` signs and attests and uploads nothing to an index (``publish.yml`` does, see
test_publish_workflow.py). It rebuilds the files of a release tag at the epoch and the version of the committed
manifest, refuses to
sign anything that is not byte-identical to the manifest, signs each file with cosign (keyless) and attests them with the
SLSA-3 generator. The release script and the release guard find that run, and the bundles it attaches, by name: these
tests keep the two sides together. ``test_workflows.py`` keeps the hosted-runner and permission rules of every workflow.
"""

import fnmatch
import re
from pathlib import Path

import pytest
import yaml

from scripts.release import facts, testpypi

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
SCRIPT = ROOT / "scripts" / "release-pypi.sh"
FLOW = ROOT / "scripts" / "lib" / "release-flow.sh"
PIN = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+")


def _workflow():
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    document["triggers"] = document.get("on", document.get(True))  # YAML reads a bare `on` as the boolean True
    return document


def _job(name):
    return _workflow()["jobs"][name]


def _step(prefix):
    (step,) = [
        step for step in _job("sign-artefacts")["steps"]
        if step.get("name", "").startswith(prefix) or str(step.get("uses", "")).startswith(prefix)
    ]
    return step


def _names():
    return [step.get("name") or step.get("uses") for step in _job("sign-artefacts")["steps"]]


def test_the_workflow_is_the_file_that_the_release_guard_looks_for():
    assert WORKFLOW.name == facts.RELEASE_WORKFLOW
    assert _workflow()["name"] == "Release"


def test_it_runs_on_the_push_of_a_plain_release_tag_and_on_nothing_else():
    triggers = _workflow()["triggers"]

    assert triggers["push"] == {"tags": ["v*.*.*"]}
    # no dispatch: a run by hand would sign again and replace the bundles of the push run with ones that publish.yml
    # refuses (trigger workflow_dispatch); a published release starts nothing either
    assert set(triggers) == {"push"}


@pytest.mark.parametrize("tag", ["v0.1.0", "v1.2.3", "v0.1.0rc1", "v0.3.0.dev1", "v0.3.0.post2"])
def test_every_tag_that_the_script_pushes_starts_it(tag):
    patterns = _workflow()["triggers"]["push"]["tags"]

    assert any(fnmatch.fnmatchcase(tag, pattern) for pattern in patterns)


@pytest.mark.parametrize("tag", ["0.1.0", "release-1", "v1.2", "models-v1.0.0"])
def test_a_tag_that_is_not_a_release_tag_does_not_start_it(tag):
    patterns = _workflow()["triggers"]["push"]["tags"]

    assert not any(fnmatch.fnmatchcase(tag, pattern) for pattern in patterns)


def test_a_release_run_is_never_cancelled_and_the_token_is_read_only_until_a_job_asks_for_more():
    document = _workflow()

    assert document["permissions"] == {"contents": "read"}
    assert document["concurrency"]["cancel-in-progress"] is False
    assert "github.ref" in document["concurrency"]["group"]


def test_the_signing_job_may_write_the_release_and_ask_for_an_identity_token_and_nothing_else():
    assert _job("sign-artefacts")["permissions"] == {"contents": "write", "id-token": "write"}


def test_the_code_is_checked_out_at_the_fully_qualified_tag_without_keeping_the_token():
    (checkout,) = [step for step in _job("sign-artefacts")["steps"] if step.get("uses", "").startswith("actions/checkout")]

    assert checkout["with"]["ref"] == "refs/tags/${{ github.ref_name }}"  # a bare name is resolved branch first
    assert checkout["with"]["persist-credentials"] is False


def test_the_checkout_is_proven_to_be_the_commit_of_the_tag_and_of_the_push_before_anything_is_built():
    step = _step("Check the checkout is the commit of the tag")
    names = _names()

    assert step["env"] == {"TAG_NAME": "${{ github.ref_name }}"}
    assert "git rev-parse HEAD" in step["run"]
    assert 'git rev-parse "refs/tags/${TAG_NAME}^{commit}"' in step["run"]
    assert '"$GITHUB_EVENT_NAME" == "push"' in step["run"]
    assert '== "$GITHUB_SHA"' in step["run"]
    assert step["run"].count("exit 1") == 2
    assert names.index("Checkout") < names.index(step["name"]) < names.index("Build wheel + sdist")


def test_every_action_of_the_workflow_is_a_commit_with_its_version_in_a_comment_and_the_generator_stays_on_its_tag():
    text = WORKFLOW.read_text(encoding="utf-8")
    uses = [line.split("uses:", 1)[1].strip() for line in text.splitlines() if line.strip().startswith(("uses:", "- uses:"))]
    generator = [name for name in uses if name.startswith("slsa-framework/")]

    assert len(uses) == 6
    assert all(PIN.fullmatch(name) for name in uses if name not in generator)
    assert len(generator) == 1
    assert re.fullmatch(r"[\w./-]+@v\d+\.\d+\.\d+", generator[0])  # the SLSA verifier needs a tag, not a commit


def test_the_environment_is_not_synced_because_the_build_needs_none_of_the_tensorrt_libraries():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "uv sync" not in text


def test_the_epoch_and_the_version_come_from_the_manifest_not_from_the_tagged_commit():
    step = _step("Resolve SOURCE_DATE_EPOCH")
    build = _step("Build wheel")

    assert step["id"] == "manifest"
    assert step["env"] == {"TAG_NAME": "${{ github.ref_name }}"}
    for needle in (
        "release-digests.json",
        '["source_date_epoch"]',
        '["version"]',
        '["tag"]',
        "exit 1",  # a tag without the manifest is refused, not signed
        "scripts/release-pypi.sh",
        "the bytes PyPI will serve",
    ):
        assert needle in step["run"]
    assert build["env"] == {
        "SOURCE_DATE_EPOCH": "${{ steps.manifest.outputs.epoch }}",
        "SETUPTOOLS_SCM_PRETEND_VERSION": "${{ steps.manifest.outputs.version }}",
    }
    assert build["run"] == "uv build"  # the command of the release script: the same bytes


def test_nothing_reaches_the_step_outputs_before_the_epoch_the_version_and_the_tag_of_the_manifest_are_checked():
    run = _step("Resolve SOURCE_DATE_EPOCH")["run"]
    epoch_check = run.index('"$epoch" =~ ^[0-9]+$')
    grammar_check = run.index('"$version" =~ ^${part}')
    name_check = run.index('"$manifest_tag" == "$TAG_NAME" && "v${version}" == "$TAG_NAME"')

    assert max(epoch_check, grammar_check, name_check) < run.index("GITHUB_OUTPUT")
    assert run.count("GITHUB_OUTPUT") == 2  # the epoch and the version, nothing else
    assert ".dev0" not in run


def test_nothing_is_signed_before_the_rebuild_is_proven_to_be_the_manifest_and_to_hold_only_source():
    names = _names()
    verify = _step("Verify the rebuild matches the release manifest")
    holds = _step("Check what the package holds")

    assert names.index("Build wheel + sdist") < names.index(verify["name"]) < names.index(holds["name"])
    assert names.index(holds["name"]) < names.index("Sign artefacts (keyless, Fulcio OIDC, Rekor log)")
    assert " ".join(verify["run"].replace("\\", " ").split()).endswith(
        "python3 -m scripts.release.digests verify --manifest release-digests.json --dist dist",
    )
    assert holds["run"] == "python3 -m scripts.quality dist-check dist"


def test_cosign_is_the_pinned_v3_release_and_signs_each_file_as_one_sigstore_bundle():
    installer = _step("sigstore/cosign-installer")
    sign = _step("Sign artefacts")

    assert installer["with"]["cosign-release"] == "${{ env.COSIGN_VERSION }}"
    assert re.fullmatch(r"v3\.\d+\.\d+", _workflow()["env"]["COSIGN_VERSION"])
    assert "cosign sign-blob" in sign["run"]
    assert '--bundle "${f}.sigstore.json"' in sign["run"]
    assert sign["env"]["COSIGN_YES"] == "true"
    assert facts.BUNDLE_SUFFIX == ".sigstore.json"


def test_the_signatures_are_verified_against_the_identity_of_this_workflow_at_the_tag_before_they_are_attached():
    verify = _step("Self-verify")
    names = _names()

    assert "cosign verify-blob" in verify["run"]
    assert "workflows/release.yml@refs/tags/${TAG_NAME}" in verify["run"]
    assert "https://token.actions.githubusercontent.com" in verify["run"]
    assert names.index(verify["name"]) < names.index("Attach artefacts + signatures to release")


def test_the_release_gets_the_wheel_the_sdist_and_a_bundle_for_each():
    attach = _step("softprops/action-gh-release")

    assert attach["with"]["tag_name"] == "${{ github.ref_name }}"
    assert attach["with"]["files"].split() == ["dist/*.whl", "dist/*.tar.gz", "dist/*.sigstore.json"]
    assert attach["with"]["fail_on_unmatched_files"] is True


def test_the_signing_job_hands_the_digests_of_what_it_signed_to_the_provenance_job():
    job = _job("sign-artefacts")
    subjects = _step("Compute SLSA provenance subjects")

    assert job["outputs"]["subjects-b64"] == "${{ steps.subjects.outputs.value }}"
    assert subjects["id"] == "subjects"
    assert "sha256sum" in subjects["run"]
    assert "base64 -w0" in subjects["run"]


def test_the_provenance_is_made_by_the_pinned_slsa_generator_and_lands_on_the_same_release():
    job = _job("provenance-artefacts")
    pin = _workflow()["env"]["SLSA_GENERATOR_VERSION"]

    assert re.fullmatch(
        r"slsa-framework/slsa-github-generator/\.github/workflows/generator_generic_slsa3\.yml@v\d+\.\d+\.\d+",
        job["uses"],
    )
    assert job["uses"].endswith(f"@{pin}")  # a `uses:` cannot read `env`, so the test holds the two together
    # the commit the tag pointed to when it was chosen: `publish-ready` stops a release whose tag was moved
    assert re.fullmatch(r"[0-9a-f]{40}", _workflow()["env"]["SLSA_GENERATOR_COMMIT"])
    assert job["needs"] == "sign-artefacts"
    assert job["permissions"] == {"id-token": "write", "contents": "write", "actions": "read"}
    assert job["with"]["base64-subjects"] == "${{ needs.sign-artefacts.outputs.subjects-b64 }}"
    assert job["with"]["upload-assets"] is True
    assert job["with"]["upload-tag-name"] == "${{ github.ref_name }}"
    assert job["with"]["private-repository"] is True  # harmless on a public repository, needed while one is private


def test_the_provenance_file_has_the_suffix_that_the_release_guard_accepts_in_place_of_a_missing_run():
    name = _job("provenance-artefacts")["with"]["provenance-name"]

    assert name.endswith(facts.PROVENANCE_SUFFIX)


def test_the_workflow_signs_nothing_but_the_files_of_the_package():
    assert set(_workflow()["jobs"]) == {"sign-artefacts", "provenance-artefacts"}
    assert "REGISTRY" not in _workflow()["env"]
    assert "docker" not in WORKFLOW.read_text(encoding="utf-8").lower()


def _texts(*folders):
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for folder in folders
        for path in (ROOT / folder).rglob("*")
        if path.is_file() and path.suffix in {".sh", ".yml", ".py"} and "__pycache__" not in path.parts
    }


def test_no_script_and_no_workflow_runs_uv_publish():
    texts = _texts("scripts", ".github/workflows")

    assert texts
    assert not [name for name, text in texts.items() if re.search(r"^\s*uv publish\b", text, re.MULTILINE)]


def test_the_only_uploads_to_an_index_are_the_pypi_action_of_the_two_publish_workflows():
    texts = _texts("scripts", ".github/workflows")

    assert {name for name, text in texts.items() if "pypa/gh-action-pypi-publish" in text} == {
        ".github/workflows/publish.yml",
        ".github/workflows/publish-testpypi.yml",
    }


def test_the_release_script_dispatches_the_publish_workflow_only_after_the_guard_has_passed():
    flow = FLOW.read_text(encoding="utf-8")
    body = flow[flow.index("\npublish_release() {"):]
    body = body[: body.index("\n}\n")]

    assert body.index("guard check") < body.index("rf_dispatch_publish")
    assert "gh workflow run" not in flow  # the dispatch is the one of scripts/release/, behind the same guard


def test_the_dispatch_of_the_publish_workflow_is_one_command_in_the_release_package_and_the_script_calls_it_nowhere_else():
    package = _texts("scripts/release")
    holders = {name for name, text in package.items() if re.search(r'"gh",\s*"workflow",\s*"run",\s*PUBLISH_WORKFLOW', text)}

    assert holders <= {"scripts/release/guard.py", "scripts/release/publish.py"}
    assert holders
    assert facts.PUBLISH_WORKFLOW == "publish.yml"
    assert "gh workflow run" not in SCRIPT.read_text(encoding="utf-8")


def test_the_dispatch_of_the_rehearsal_workflow_is_one_command_of_its_own_module_and_nothing_else_dispatches_it():
    package = _texts("scripts/release")
    dispatch = r'"gh",\s*"workflow",\s*"run",\s*TESTPYPI_WORKFLOW'
    holders = {name for name, text in package.items() if re.search(dispatch, text)}

    assert holders == {"scripts/release/testpypi.py"}
    assert len(re.findall(dispatch, package["scripts/release/testpypi.py"])) == 1
    assert testpypi.TESTPYPI_WORKFLOW == "publish-testpypi.yml" == facts.INDEXES["testpypi"].workflow
    assert not [name for name, text in package.items() if re.search(r'"gh",\s*"workflow",\s*"run",\s*"publish', text)]
    for path in (SCRIPT, FLOW):
        assert "gh workflow run" not in path.read_text(encoding="utf-8")


def test_uv_does_not_restore_a_cache_in_the_job_that_signs():
    """A tag run would restore what a run of main wrote, and this job holds the identity token and write access."""
    assert _step("astral-sh/setup-uv")["with"] == {"enable-cache": False}
