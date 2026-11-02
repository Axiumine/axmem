# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""``release.yml`` delivers an SBOM with every release, under the same signature as the wheel and the sdist.

The job that signs makes the CycloneDX file of the locked runtime dependencies with ``scripts/release/sbom.py``, checks it
before anything is signed, signs it keyless like the other files, attaches it and its bundle to the release, and adds it to
the subjects of the SLSA provenance. The SBOM is in no manifest and not in ``dist/``: it is not a file of PyPI, and the
manifest, the guard and ``publish.yml`` know the wheel and the sdist only. ``test_release_workflow.py`` holds the rest of the
workflow; the guard side (a bundle for an SBOM that the release carries) is in ``test_release_facts_sbom.py``.
"""

import re
from pathlib import Path

import yaml

from scripts.release import facts, sbom

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
MAKE = "Generate and check the SBOM"
SIGN = "Sign the SBOM (keyless)"
VERIFY = "Verify the SBOM signature (round-trip)"
ATTACH = "Attach the SBOM and its signature to the release"
SUBJECTS = "Compute SLSA provenance subjects (sha256 + name, base64)"


def _steps():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["sign-artefacts"]["steps"]


def _step(name):
    (step,) = [step for step in _steps() if step.get("name") == name]
    return step


def _names():
    return [step.get("name") or step.get("uses") for step in _steps()]


def test_the_sbom_is_made_after_the_package_is_checked_and_signed_and_attached_before_the_provenance_is_computed():
    names = _names()
    order = [
        "Check what the package holds",
        MAKE,
        "Install cosign",
        "Sign artefacts (keyless, Fulcio OIDC, Rekor log)",
        SIGN,
        VERIFY,
        "Attach artefacts + signatures to release",
        ATTACH,
        SUBJECTS,
    ]

    assert [names.index(name) for name in order] == sorted(names.index(name) for name in order)


def test_the_sbom_is_made_at_the_epoch_and_the_version_of_the_manifest_and_checked_before_it_is_signed():
    step = _step(MAKE)
    run = step["run"]

    assert step["id"] == "sbom"
    assert step["env"]["VERSION"] == "${{ steps.manifest.outputs.version }}"
    assert step["env"]["EPOCH"] == "${{ steps.manifest.outputs.epoch }}"
    assert "${{" not in run  # the values reach the shell as variables, never as text of the script
    assert run.index("scripts.release.sbom generate") < run.index("scripts.release.sbom verify")
    assert '--version "$VERSION" --epoch "$EPOCH" --output "$file"' in run
    assert '--version "$VERSION"' in run.split("scripts.release.sbom verify", 1)[1]
    assert run.index("scripts.release.sbom verify") < run.index("GITHUB_OUTPUT")
    assert "continue-on-error" not in step  # a document that is not valid stops the run before anything is signed


def test_the_file_name_is_the_one_the_module_and_the_release_guard_know_and_the_file_is_outside_dist():
    step = _step(MAKE)

    assert facts.SBOM_SUFFIX == ".cdx.json"
    assert f"axmem-${{VERSION}}{facts.SBOM_SUFFIX}" == "axmem-${VERSION}.cdx.json"
    assert 'file="${SBOM_DIR}/axmem-${VERSION}.cdx.json"' in step["run"]
    assert step["env"]["SBOM_DIR"] == "${{ runner.temp }}/sbom"  # not the workspace and not dist/, where only PyPI's files are


def test_the_sbom_is_signed_keyless_as_one_sigstore_bundle_and_verified_against_this_workflow_at_the_tag():
    sign, verify = _step(SIGN), _step(VERIFY)

    assert sign["env"]["COSIGN_YES"] == "true"
    assert sign["env"]["SBOM"] == "${{ steps.sbom.outputs.path }}"
    assert 'cosign sign-blob --yes --bundle "${SBOM}.sigstore.json" "${SBOM}"' in sign["run"]
    assert verify["env"]["SBOM"] == "${{ steps.sbom.outputs.path }}"
    assert verify["env"]["TAG_NAME"] == "${{ github.ref_name }}"
    assert "cosign verify-blob" in verify["run"]
    assert '--bundle "${SBOM}.sigstore.json"' in verify["run"]
    assert "workflows/release.yml@refs/tags/${TAG_NAME}" in verify["run"]
    assert re.search(r'--certificate-oidc-issuer "https://token\.actions\.githubusercontent\.com"', verify["run"])


def test_the_sbom_and_its_bundle_are_uploaded_to_the_release_after_the_step_that_creates_it():
    step = _step(ATTACH)
    names = _names()

    assert step["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "TAG_NAME": "${{ github.ref_name }}",
        "SBOM": "${{ steps.sbom.outputs.path }}",
    }
    assert "${{" not in step["run"]
    assert 'gh release upload "$TAG_NAME" "$SBOM" "${SBOM}.sigstore.json" --repo "$GITHUB_REPOSITORY" --clobber' in step["run"]
    assert names.index("Attach artefacts + signatures to release") < names.index(step["name"])


def test_the_provenance_attests_the_sbom_with_the_files_it_describes():
    step = _step(SUBJECTS)
    run = step["run"]

    assert step["env"] == {"SBOM": "${{ steps.sbom.outputs.path }}"}
    assert "artefacts=(dist/*.whl dist/*.tar.gz)" in run  # the wheel and the sdist are still required, apart from the SBOM
    assert run.index("no artefacts to attest") < run.index('artefacts+=("${SBOM}")') < run.index("sha256sum")


def test_uv_is_pinned_to_one_version_and_its_archive_to_a_sha256_because_the_sbom_comes_from_a_preview_feature_of_it():
    (install,) = [step for step in _steps() if step.get("uses", "").startswith("astral-sh/setup-uv")]

    assert re.fullmatch(r"\d+\.\d+\.\d+", install["with"]["version"])
    assert re.fullmatch(r"[0-9a-f]{64}", install["with"]["checksum"])
    assert "--preview-features" in sbom.UV_EXPORT
    assert "sbom-export" in sbom.UV_EXPORT
