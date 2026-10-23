"""The signing facts, part two: a whole bundle per artefact, what each bundle signed, and the whole proof.

Condition 4 of the release guard and the integrity check both ask ``facts.signing_problems``. These cases cover a bundle
that exists by name only (empty, unfinished, in a draft release), a bundle that signs something other than the artefact,
a release that attaches other bytes than were signed, and the proof that puts them together with the run of the tag.
``test_release_facts_signing_a.py`` covers the runs and the release view. The command runner is replaced where the case
is a shape of answer.
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
DIGESTS = {"p-1.whl": DIGEST, "p-1.tar.gz": DIGEST}


def _completed(code, out="", err=""):
    return subprocess.CompletedProcess(["x"], code, out, err)


def _run(status="completed", conclusion="success", created="2026-01-01", **fields):
    """A ``release.yml`` run row of the tag's own push; ``fields`` replace what that push has."""
    row = {
        "event": "push",
        "headBranch": fields.pop("branch", TAG),
        "headSha": fields.pop("sha", "abc"),
    }
    return {
        "status": status,
        "conclusion": conclusion,
        "createdAt": created,
        "url": f"u-{created}-{conclusion or status}",
        **row,
        **fields,
    }


def _asset(size=9, state="uploaded", digest=None):
    return facts.Asset(size, state, digest)


def _release(assets, draft=False):
    return facts.Release(draft, assets)


def _bundle_for(digest):
    """A Sigstore bundle (protobuf form) that signed the sha256 ``digest``."""
    signed = {"algorithm": "SHA2_256", "digest": base64.b64encode(bytes.fromhex(digest)).decode()}
    return json.dumps({"mediaType": "bundle", "messageSignature": {"messageDigest": signed}})


# --- bundle_problems ------------------------------------------------------------------------------


def test_a_whole_bundle_per_artefact_is_no_problem():
    found = _release({"a.sigstore.json": _asset(), "b.sigstore.json": _asset()})

    assert facts.bundle_problems(found, ["a", "b"]) == []


def test_a_missing_bundle_is_named():
    found = _release({"a": _asset(), "a.sigstore.json": _asset()})

    assert facts.bundle_problems(found, ["a", "b"]) == [
        "the release has no b.sigstore.json — the artefact is not signed",
    ]


def test_a_bundle_of_no_bytes_is_not_a_signature():
    found = _release({"a.sigstore.json": _asset(size=0)})

    assert facts.bundle_problems(found, ["a"]) == [
        "a.sigstore.json on the release is unfinished (state uploaded, 0 bytes)",
    ]


def test_an_upload_that_never_finished_is_not_a_signature():
    found = _release({"a.sigstore.json": _asset(state="starter")})

    assert facts.bundle_problems(found, ["a"]) == [
        "a.sigstore.json on the release is unfinished (state starter, 9 bytes)",
    ]


# --- digest_problems ------------------------------------------------------------------------------


def test_artefacts_with_the_expected_digests_are_no_problem():
    found = _release({"a": _asset(digest=DIGEST), "b": _asset(digest=None)})

    assert facts.digest_problems(found, {"a": DIGEST.upper(), "b": DIGEST}) == []


def test_an_artefact_that_is_not_attached_is_named():
    assert facts.digest_problems(_release({}), {"a": DIGEST}) == [
        "the release has no a — the signed bytes are not attached",
    ]


def test_an_artefact_whose_upload_never_finished_is_not_attached():
    found = _release({"a": _asset(state="starter", digest=DIGEST)})

    assert facts.digest_problems(found, {"a": DIGEST}) == [
        "the release has no a — the signed bytes are not attached",
    ]


def test_other_bytes_on_the_release_are_named():
    found = _release({"a": _asset(digest=OTHER)})

    assert facts.digest_problems(found, {"a": DIGEST}) == [
        f"a on the release has sha256 {OTHER}, not the expected {DIGEST}",
    ]


def test_an_expected_digest_that_is_not_known_is_not_compared():
    found = _release({"a": _asset(digest=OTHER)})

    assert facts.digest_problems(found, {"a": None}) == []


# --- signed_digest --------------------------------------------------------------------------------


def test_the_digest_a_protobuf_bundle_signed_is_read_as_hex():
    assert facts.signed_digest(_bundle_for(DIGEST)) == DIGEST


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        "{}",
        '{"messageSignature": {}}',
        '{"messageSignature": {"messageDigest": {"algorithm": "SHA2_512", "digest": "AAAA"}}}',
        '{"messageSignature": {"messageDigest": {"algorithm": "SHA2_256", "digest": "***"}}}',
        '{"base64Signature": "c2ln", "cert": "x"}',
    ],
)
def test_a_file_that_is_not_such_a_bundle_names_no_digest(text):
    assert facts.signed_digest(text) is None


# --- bundle_digest_problems -----------------------------------------------------------------------


def _download_gives(monkeypatch, bundles, seen=None):
    """``gh release download`` is replaced by one that writes ``bundles`` (name -> text) into its ``-D`` directory."""

    def fake(argv, **_k):
        if seen is not None:
            seen.append(argv)
        target = Path(argv[argv.index("-D") + 1])
        for name, text in bundles.items():
            (target / name).write_text(text, encoding="utf-8")
        return _completed(0)

    monkeypatch.setattr(facts, "run_text", fake)


def test_a_bundle_that_signed_the_artefact_is_no_problem(monkeypatch):
    seen = []
    _download_gives(monkeypatch, {"a.sigstore.json": _bundle_for(DIGEST)}, seen)

    assert facts.bundle_digest_problems("o/r", "v1", {"a": DIGEST}) == []
    argv = seen[0]
    assert argv[:4] == ["gh", "release", "download", "v1"]
    assert argv[argv.index("-p") + 1] == "*.sigstore.json"


def test_a_bundle_that_signed_other_bytes_is_named(monkeypatch):
    _download_gives(monkeypatch, {"a.sigstore.json": _bundle_for(OTHER)})

    assert facts.bundle_digest_problems("o/r", "v1", {"a": DIGEST}) == [
        f"a.sigstore.json signed sha256 {OTHER}, not the a digest {DIGEST}",
    ]


def test_a_digest_in_capitals_is_the_same_digest(monkeypatch):
    _download_gives(monkeypatch, {"a.sigstore.json": _bundle_for(DIGEST)})

    assert facts.bundle_digest_problems("o/r", "v1", {"a": DIGEST.upper()}) == []


@pytest.mark.parametrize("text", ["", "garbage", '{"base64Signature": "c2ln"}'])
def test_a_downloaded_file_that_is_not_a_bundle_is_named(monkeypatch, text):
    _download_gives(monkeypatch, {"a.sigstore.json": text})

    assert facts.bundle_digest_problems("o/r", "v1", {"a": DIGEST}) == [
        "a.sigstore.json is not a Sigstore bundle that names the sha256 it signed",
    ]


def test_an_artefact_with_no_known_digest_is_only_checked_to_have_a_bundle(monkeypatch):
    _download_gives(monkeypatch, {"a.sigstore.json": _bundle_for(OTHER)})

    assert facts.bundle_digest_problems("o/r", "v1", {"a": None}) == []


def test_a_bundle_that_was_not_downloaded_is_named(monkeypatch):
    _download_gives(monkeypatch, {"b.sigstore.json": _bundle_for(DIGEST)})

    assert facts.bundle_digest_problems("o/r", "v1", {"a": DIGEST}) == [
        "a.sigstore.json is not a Sigstore bundle that names the sha256 it signed",
    ]


# --- signing_problems -----------------------------------------------------------------------------


@pytest.fixture(name="target")
def _target():
    return facts.Target("o/r", "abc", TAG, DIGESTS)


def _signed():
    """A whole release: each artefact with its digest, its bundle, and the provenance file."""
    found = {}
    for name, digest in DIGESTS.items():
        found[name] = _asset(digest=digest)
        found[f"{name}.sigstore.json"] = _asset()
    found["x.intoto.jsonl"] = _asset()
    return found


def _scripted(monkeypatch, runs, assets, draft=False, bundles=None):
    """Script the runs and the release (``assets`` None: no release); ``bundles`` is what the download check answers.

    With no ``bundles`` the download check fails the test if it is asked. Returns the calls made, in order.
    """
    asked = []

    def release_runs(repo, sha, tag):
        asked.append((repo, sha, tag))
        return runs

    def download(_repo, tag, _digests):
        if bundles is None:
            pytest.fail("the bundles were downloaded")
        asked.append(("download", tag))
        return bundles

    monkeypatch.setattr(facts, "release_runs", release_runs)
    monkeypatch.setattr(
        facts,
        "release_view",
        lambda repo, tag: None if assets is None else _release(assets, draft),
    )
    monkeypatch.setattr(facts, "bundle_digest_problems", download)
    return asked


def test_the_run_is_asked_for_by_the_tag(monkeypatch, target):
    asked = _scripted(monkeypatch, [_run()], _signed(), bundles=[])

    facts.signing_problems(target, need_run=True)

    assert asked == [("o/r", "abc", TAG), ("download", TAG)]


def test_a_green_run_and_a_whole_signed_release_is_proven(monkeypatch, target):
    _scripted(monkeypatch, [_run()], _signed(), bundles=[])

    assert facts.signing_problems(target, need_run=True) == []


def test_a_missing_bundle_is_named_and_nothing_is_downloaded(monkeypatch, target):
    found = _signed()
    del found["p-1.whl.sigstore.json"]
    _scripted(monkeypatch, [_run()], found)

    problems = facts.signing_problems(target, need_run=True)

    assert problems == ["the release has no p-1.whl.sigstore.json — the artefact is not signed"]


def test_an_empty_bundle_is_a_problem_and_nothing_is_downloaded(monkeypatch, target):
    found = {**_signed(), "p-1.whl.sigstore.json": _asset(size=0, state="starter")}
    _scripted(monkeypatch, [_run()], found)

    problems = facts.signing_problems(target, need_run=True)

    assert problems == [
        "p-1.whl.sigstore.json on the release is unfinished (state starter, 0 bytes)",
    ]


def test_a_draft_release_is_a_problem_and_nothing_is_downloaded(monkeypatch, target):
    _scripted(monkeypatch, [_run()], _signed(), draft=True)

    assert facts.signing_problems(target, need_run=True) == [
        f"the GitHub release of {TAG} is still a draft",
    ]


def test_other_bytes_attached_to_the_release_are_a_problem(monkeypatch, target):
    found = {**_signed(), "p-1.whl": _asset(digest=OTHER)}
    _scripted(monkeypatch, [_run()], found, bundles=[])

    problems = facts.signing_problems(target, need_run=True)

    assert problems == [f"p-1.whl on the release has sha256 {OTHER}, not the expected {DIGEST}"]


def test_what_the_bundles_signed_is_a_problem_too(monkeypatch, target):
    _scripted(monkeypatch, [_run()], _signed(), bundles=["a bundle signed other bytes"])

    assert facts.signing_problems(target, need_run=True) == ["a bundle signed other bytes"]


def test_a_tag_without_a_release_is_a_problem(monkeypatch, target):
    _scripted(monkeypatch, [_run()], None)

    assert facts.signing_problems(target, need_run=True) == ["tag v1.0.0 has no GitHub release"]


@pytest.mark.parametrize(
    ("run", "word"),
    [(_run("queued", ""), "queued"), (_run(conclusion="failure"), "failure")],
)
def test_a_run_that_is_not_green_is_a_problem_with_or_without_the_need(
    monkeypatch, target, run, word,
):
    _scripted(monkeypatch, [run], _signed(), bundles=[])

    for need in (True, False):
        problems = facts.signing_problems(target, need_run=need)

        assert len(problems) == 1
        assert word in problems[0]


def test_a_missing_run_is_a_problem_when_a_run_is_needed(monkeypatch, target):
    _scripted(monkeypatch, [], _signed(), bundles=[])

    problems = facts.signing_problems(target, need_run=True)

    assert problems == ["no release.yml run of the tag push is recorded for this commit"]


def test_a_missing_run_is_accepted_for_history_when_the_provenance_file_is_there(
    monkeypatch, target,
):
    _scripted(monkeypatch, [], _signed(), bundles=[])

    assert facts.signing_problems(target, need_run=False) == []


def test_a_missing_run_and_no_provenance_is_a_problem_for_history_too(monkeypatch, target):
    found = _signed()
    del found["x.intoto.jsonl"]
    _scripted(monkeypatch, [], found, bundles=[])

    problems = facts.signing_problems(target, need_run=False)

    assert problems == [
        (
            "no release.yml run of the tag push is recorded for this commit, "
            "and the release carries no .intoto.jsonl provenance file"
        ),
    ]
