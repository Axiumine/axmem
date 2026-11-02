# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The signing facts, SBOM: a release that carries an SBOM carries a whole bundle that signed those very bytes.

``release.yml`` signs the SBOM and attaches it with its bundle. No manifest lists it (it is not a file of PyPI), so the
release guard and the integrity check hold it to the one thing they can know: the bundle beside it signed the sha256 that
GitHub computed for the attached file. A release without an SBOM (the first one, cut before the workflow made one) is
unchanged. ``test_release_facts_signing_b.py`` covers the same proof for the wheel and the sdist; ``test_release_sbom.py``
covers the file itself.
"""

import base64
import json
import subprocess
from pathlib import Path

import pytest

from scripts.release import facts

TAG = "v1.0.0"
DIGEST = "ab" * 32
OTHER = "cd" * 32
SBOM = "p-1.cdx.json"
DIGESTS = {"p-1.whl": DIGEST, "p-1.tar.gz": DIGEST}


def _asset(size=9, state="uploaded", digest=None):
    return facts.Asset(size, state, digest)


def _bundle_for(digest):
    signed = {"algorithm": "SHA2_256", "digest": base64.b64encode(bytes.fromhex(digest)).decode()}
    return json.dumps({"mediaType": "bundle", "messageSignature": {"messageDigest": signed}})


def _signed(sbom=True):
    """A whole release: each artefact with its digest and bundle, the provenance file, and the SBOM with its bundle."""
    found = {}
    for name, digest in DIGESTS.items():
        found[name] = _asset(digest=digest)
        found[f"{name}.sigstore.json"] = _asset()
    found["x.intoto.jsonl"] = _asset()
    if sbom:
        found[SBOM] = _asset(digest=OTHER)
        found[f"{SBOM}.sigstore.json"] = _asset()
    return found


@pytest.fixture(name="target")
def _target():
    return facts.Target("o/r", "abc", TAG, DIGESTS)


def _scripted(monkeypatch, assets, draft=False):
    """A green run and the release ``assets``; returns the digests the bundles were asked to have signed."""
    asked = []

    def download(_repo, _tag, digests):
        asked.append(digests)
        return []

    row = {"status": "completed", "conclusion": "success", "url": "u", "event": "push", "headBranch": TAG, "headSha": "abc"}
    monkeypatch.setattr(facts, "release_runs", lambda *_a: [row])
    monkeypatch.setattr(facts, "release_view", lambda *_a: facts.Release(draft, assets))
    monkeypatch.setattr(facts, "bundle_digest_problems", download)
    return asked


def test_the_sboms_of_a_release_are_the_assets_with_the_suffix_and_the_digest_github_computed():
    found = facts.Release(False, {**_signed(), "p-2.cdx.json": _asset(digest=None), "notes.json": _asset()})

    assert facts.sbom_digests(found) == {SBOM: OTHER, "p-2.cdx.json": None}
    assert facts.SBOM_SUFFIX == ".cdx.json"
    assert facts.sbom_digests(facts.Release(False, _signed(sbom=False))) == {}


def test_a_release_without_an_sbom_is_proven_as_before_and_no_digest_of_an_sbom_is_asked_for(monkeypatch, target):
    asked = _scripted(monkeypatch, _signed(sbom=False))

    assert facts.signing_problems(target, need_run=True) == []
    assert asked == [DIGESTS]


def test_a_release_with_a_signed_sbom_is_proven_and_its_bundle_must_have_signed_the_digest_of_the_attached_file(monkeypatch, target):
    asked = _scripted(monkeypatch, _signed())

    assert facts.signing_problems(target, need_run=True) == []
    assert asked == [{**DIGESTS, SBOM: OTHER}]


def test_an_sbom_without_a_bundle_is_named(monkeypatch, target):
    found = _signed()
    del found[f"{SBOM}.sigstore.json"]
    _scripted(monkeypatch, found)

    assert facts.signing_problems(target, need_run=True) == [
        f"the release has no {SBOM}.sigstore.json — the artefact is not signed",
    ]


def test_an_sbom_with_an_empty_bundle_is_named(monkeypatch, target):
    _scripted(monkeypatch, {**_signed(), f"{SBOM}.sigstore.json": _asset(size=0)})

    assert facts.signing_problems(target, need_run=True) == [
        f"{SBOM}.sigstore.json on the release is unfinished (state uploaded, 0 bytes)",
    ]


def test_an_sbom_whose_upload_never_finished_is_not_attached(monkeypatch, target):
    _scripted(monkeypatch, {**_signed(), SBOM: _asset(state="starter", digest=OTHER)})

    assert facts.signing_problems(target, need_run=True) == [
        f"the release has no {SBOM} — the signed bytes are not attached",
    ]


def test_an_sbom_in_a_draft_release_is_no_better_than_the_wheel(monkeypatch, target):
    _scripted(monkeypatch, _signed(), draft=True)

    assert facts.signing_problems(target, need_run=True) == [f"the GitHub release of {TAG} is still a draft"]


def _download_gives(monkeypatch, bundles):
    def fake(argv, **_k):
        target = Path(argv[argv.index("-D") + 1])
        for name, text in bundles.items():
            (target / name).write_text(text, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(facts, "run_text", fake)


def _bundles(sbom_signed):
    return {
        **{f"{name}.sigstore.json": _bundle_for(digest) for name, digest in DIGESTS.items()},
        f"{SBOM}.sigstore.json": _bundle_for(sbom_signed),
    }


def test_a_bundle_that_signed_other_bytes_than_the_attached_sbom_is_named(monkeypatch, target):
    monkeypatch.setattr(facts, "release_runs", lambda *_a: [])
    monkeypatch.setattr(facts, "release_view", lambda *_a: facts.Release(False, _signed()))
    _download_gives(monkeypatch, _bundles(DIGEST))

    assert facts.signing_problems(target, need_run=False) == [
        f"{SBOM}.sigstore.json signed sha256 {DIGEST}, not the {SBOM} digest {OTHER}",
    ]


def test_a_bundle_that_signed_the_attached_sbom_is_proven_against_the_real_download_check(monkeypatch, target):
    monkeypatch.setattr(facts, "release_runs", lambda *_a: [])
    monkeypatch.setattr(facts, "release_view", lambda *_a: facts.Release(False, _signed()))
    _download_gives(monkeypatch, _bundles(OTHER))

    assert facts.signing_problems(target, need_run=False) == []


def test_an_sbom_that_github_gave_no_digest_is_only_checked_to_have_a_bundle_that_names_one(monkeypatch, target):
    found = {**_signed(), SBOM: _asset(digest=None)}
    monkeypatch.setattr(facts, "release_runs", lambda *_a: [])
    monkeypatch.setattr(facts, "release_view", lambda *_a: facts.Release(False, found))
    _download_gives(monkeypatch, {**_bundles(OTHER), f"{SBOM}.sigstore.json": "not a bundle"})

    assert facts.signing_problems(target, need_run=False) == [
        f"{SBOM}.sigstore.json is not a Sigstore bundle that names the sha256 it signed",
    ]
