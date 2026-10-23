"""The release workflow signs wheels and sdists as Sigstore bundles, with a cosign that is not vulnerable.

Format: ``cosign sign-blob`` emits one ``.sigstore.json`` (signature, Fulcio chain and Rekor proof) instead of the old
``.sig`` + ``.crt`` + ``.cosign.bundle`` trio, and the release guard expects exactly that file per artefact.

Version: the legacy JSON bundle carries GHSA-fx35-mq7g-6g98 (CVSS 7.4): a bare public key in the ``cert`` field bypasses
``--certificate-identity`` and ``--certificate-oidc-issuer``, and ``verify-blob`` still prints ``Verified OK``. It is
patched in cosign v2.6.5 and v3.1.3; the protobuf bundle is not affected. A pin below the floor brings the identity
bypass back, so the floor is asserted as a number, not as a string. The consumer-facing description of the release
(``docs/release.md``) has to describe the bundle that is really published.
"""

import re
from pathlib import Path

import pytest
import yaml

from scripts.release import facts

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
RELEASE_DOC = REPO_ROOT / "docs" / "release.md"

BUNDLE_SUFFIX = ".sigstore.json"
LEGACY_SUFFIX = ".cosign.bundle"
# v2.6.5 / v3.1.3 patch the advisory; the workflow is on the v3 line, so the v3 floor binds.
COSIGN_FLOOR = (3, 1, 3)
# cosign-installer v3.x cannot install cosign v3 at all.
INSTALLER_FLOOR = (4, 0, 0)


def _version(raw):
    """``v3.1.3`` -> ``(3, 1, 3)`` for an ordered comparison."""
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", raw)
    assert match is not None, f"expected a vX.Y.Z pin, got {raw!r}"
    return tuple(int(part) for part in match.groups())


def _step(job, fragment):
    return next(s for s in job["steps"] if fragment in s.get("name", ""))


def _commands(text):
    """The text without its comment-only lines: a comment that names a flag is not a promise to use it."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _installer(job):
    return next(
        s for s in job["steps"] if s.get("uses", "").startswith("sigstore/cosign-installer")
    )


@pytest.fixture(name="release", scope="module")
def _release():
    return yaml.safe_load(RELEASE.read_text(encoding="utf-8"))


@pytest.fixture(name="sign_job", scope="module")
def _sign_job(release):
    return release["jobs"]["sign-artefacts"]


# --- one bundle per artefact, no legacy sidecar ---------------------------------------------------


def test_the_sign_step_writes_a_sigstore_bundle(sign_job):
    run = _step(sign_job, "Sign artefacts")["run"]

    assert "cosign sign-blob" in run
    assert f'--bundle "${{f}}{BUNDLE_SUFFIX}"' in run


def test_the_sign_step_drops_the_removed_output_flags(sign_job):
    # cosign v3 ignores these and v3.1.3 errors and asks for --bundle instead
    run = _commands(_step(sign_job, "Sign artefacts")["run"])

    assert "--output-signature" not in run
    assert "--output-certificate" not in run


def test_the_self_verification_reads_the_bundle_it_just_wrote(sign_job):
    run = _step(sign_job, "Self-verify")["run"]

    assert f'--bundle "${{f}}{BUNDLE_SUFFIX}"' in run
    # the identity policy is the whole point of the round trip
    assert "--certificate-identity" in run
    assert "--certificate-oidc-issuer" in run
    assert "workflows/release.yml@refs/tags/${TAG_NAME}" in run


def test_the_release_uploads_the_bundle_and_nothing_legacy(sign_job):
    files = _step(sign_job, "Attach artefacts")["with"]["files"]
    # line by line: `dist/*.sig` is a substring of `dist/*.sigstore.json`
    patterns = {line.strip() for line in files.splitlines() if line.strip()}

    assert f"dist/*{BUNDLE_SUFFIX}" in patterns
    for stale in ("dist/*.sig", "dist/*.crt", f"dist/*{LEGACY_SUFFIX}"):
        assert stale not in patterns, f"{stale} is no longer produced: the upload would fail"


def test_no_workflow_still_promises_a_legacy_bundle():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        assert LEGACY_SUFFIX not in _commands(path.read_text(encoding="utf-8")), path.name


# --- the guard asks for what the workflow produces ------------------------------------------------


def test_the_guard_looks_for_the_bundle_suffix_the_workflow_writes():
    assert facts.BUNDLE_SUFFIX == BUNDLE_SUFFIX


def test_the_guard_looks_for_the_workflow_file_that_signs():
    assert (WORKFLOWS / facts.RELEASE_WORKFLOW).is_file()
    assert facts.RELEASE_WORKFLOW == RELEASE.name


def test_the_provenance_file_has_the_suffix_the_guard_accepts_for_history(release):
    provenance = release["jobs"]["provenance-artefacts"]["with"]["provenance-name"]

    assert provenance.endswith(facts.PROVENANCE_SUFFIX)


# --- the cosign pin is above the advisory floor ---------------------------------------------------


def test_the_release_pins_a_patched_cosign(release):
    assert _version(release["env"]["COSIGN_VERSION"]) >= COSIGN_FLOOR


def test_the_installer_can_install_cosign_v3(sign_job):
    installer = _installer(sign_job)

    # the action is pinned to a commit; the release it stands for is the trailing comment of its line
    line = next(
        text for text in RELEASE.read_text(encoding="utf-8").splitlines() if installer["uses"] in text
    )
    assert _version(line.rsplit("# ", 1)[1].strip()) >= INSTALLER_FLOOR
    assert installer["with"]["cosign-release"] == "${{ env.COSIGN_VERSION }}"


def test_the_signer_and_the_verifier_are_the_one_install(sign_job):
    # a verifier older than the signer cannot read the bundle at all: one install serves both steps
    names = [step.get("name", "") for step in sign_job["steps"]]
    installed = names.index(_installer(sign_job).get("name", ""))

    assert installed < names.index(_step(sign_job, "Sign artefacts")["name"])
    assert installed < names.index(_step(sign_job, "Self-verify")["name"])
    assert sum(1 for step in sign_job["steps"] if "cosign-installer" in step.get("uses", "")) == 1


# --- the description of the release names what is published ---------------------------------------


def test_the_release_doc_names_the_bundle_and_verifies_against_it():
    text = RELEASE_DOC.read_text(encoding="utf-8")

    assert BUNDLE_SUFFIX in text
    assert "cosign verify-blob" in text
    assert re.search(rf"--bundle \S+{re.escape(BUNDLE_SUFFIX)}", text)
    assert "--certificate-identity" in text
    assert "--certificate-oidc-issuer" in text
    assert "workflows/release.yml@refs/tags/" in text


def test_the_release_doc_no_longer_promises_a_legacy_bundle():
    assert LEGACY_SUFFIX not in RELEASE_DOC.read_text(encoding="utf-8")
