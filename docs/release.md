# Releasing Axmem

Axmem ships to one place, PyPI, by trusted publishing. A version is one tag, `v<version>`, and the route is:

1. `scripts/release-pypi.sh <version>` on the maintainer's machine builds the version, makes the release commit, pushes it
   and tags it;
2. the tag starts `.github/workflows/release.yml`, which rebuilds the package, signs the files (cosign, keyless) and attests
   them (SLSA-3) and uploads nothing;
3. once the signing succeeded and the guard is satisfied, the script dispatches `.github/workflows/publish.yml` on the tag;
4. its job `verify` proves the signed files again; the reviewers of the environment `pypi` approve; its job `publish`
   uploads them to PyPI with an OIDC identity, and the script reads PyPI until it serves the manifest's bytes.

No token, password or environment file exists anywhere in this flow, and `uv publish` is never run. A GitHub release made
by hand starts nothing: `publish.yml` runs only when it is dispatched on a tag.

## What the release guarantees

PyPI refuses a second upload of a file name for good, even after the file is deleted. So the dispatch is the last step, and
a version reaches PyPI only when its exact release commit is pushed, green in CI, tagged and signed, and the files being
uploaded are byte for byte the files that were signed. The proof is in code, in `scripts/release/guard.py`, and holds five
conditions before the workflow is dispatched (and again inside `publish.yml`):

1. the release commit is on the remote's `main`, and the manifest `release-digests.json` on disk is the one committed there;
2. every push-triggered CI run of `main` on that commit is completed, and green;
3. the tag is on the remote and points at that commit;
4. the `release.yml` run that the push of the tag started succeeded, and the GitHub release carries a whole `.sigstore.json`
   bundle for every file of the manifest, each signing that file's sha256;
5. the files that will be published, the signed ones downloaded from the GitHub release, are byte-identical to the
   manifest's digests and are the only files there.

`release.yml` rebuilds the package from the tag at the epoch and the version that the manifest records, refuses to sign
unless the rebuild is byte-identical to the manifest, checks that the package holds only Axmem's source (the `dist-check` of
the [package gate](quality-gates.md#package-contents)), signs each file with cosign (keyless) and attests them with the
SLSA-3 generator. The build backend is pinned to exact versions in `pyproject.toml` (`[build-system]`), so that the build on
the maintainer's machine and the rebuild in CI agree (a test requires every entry to be an exact `==` pin). The dependencies
of the two packages are not pinned: should one of them change the bytes, the rebuild no longer equals the manifest and the
signing run refuses, after the tag is pushed; PyPI holds nothing then, and the next version is released.

### What protects the upload

PyPI trusts a workflow file and an environment name. It does not check the ref the workflow ran on, and a check written
inside `publish.yml` cannot protect the file itself. The controls that keep any other ref from publishing are the settings
of the repository:

* the environment `pypi` accepts deployments from selected tags that match `v*.*.*` only (not from every branch, not from
  the protected ones), has required reviewers, and does not let administrators bypass them;
* a tag ruleset on `v*` forbids creating the tag to anyone but the maintainer, and moving or deleting it; its bypass list
  may name repository admins only, and `publish-ready` refuses a ruleset that lets the Write role or anybody else bypass it;
* the guard in CI (the job `verify` of `publish.yml`) is defence in depth: it checks the same five conditions on the files
  the reviewers are asked to approve.

`python3 -m scripts.release.guard publish-ready --repo Axiumine/axmem` checks all of these settings, and the release script
runs it every time before it does anything else.

## Once, before the first release

* Make `Axiumine/axmem` public first. `publish-ready` refuses a private repository, the guard in CI reads `main` without
  credentials, and the tag rule of the environment needs it. Check that `git ls-remote https://github.com/Axiumine/axmem`
  works without logging in.
* PyPI: an account with a hardware-key second factor, and no API token, ever. Check that
  <https://pypi.org/project/axmem/> answers 404 (the name is free). Right before the first release, add a pending publisher
  (Your projects, Publishing): project `axmem`, owner `Axiumine`, repository `axmem`, workflow `publish.yml`, environment
  `pypi`. The project is created by the first upload, which may fail; from the first dispatch on it is an ordinary publisher
  and a retry needs no setup.
* GitHub, Settings, Environments, `pypi`: Deployment branches and tags set to Selected, with one rule of the type tag,
  `v*.*.*`, and no branch rule; Required reviewers set; Allow administrators to bypass off; no secrets. Turn Prevent
  self-review on only when a second reviewer account exists: with one maintainer who approves their own deployment the gate
  is a pause, not a second pair of eyes. `publish-ready` enforces everything here except self-review.
* GitHub, Settings, Rules, Rulesets:
  * a tag ruleset on `refs/tags/v*`: creation restricted to the maintainer (the Admin role is the only bypass actor), update,
    deletion and non-fast-forward blocked (`publish-ready` enforces it, bypass list included);
  * recommended: a branch ruleset that blocks the creation of `refs/heads/v*`, so no branch can be named like a tag;
  * a ruleset on `main` without force push or deletion and with required status checks, with the maintainer as the only
    bypass actor, because the script pushes to `main` (`publish-ready` only warns when it is missing).
* GitHub, Settings, Actions, General: when Allow select actions is on, allow `actions/*`, `astral-sh/setup-uv`,
  `sigstore/cosign-installer`, `softprops/action-gh-release`, `pypa/gh-action-pypi-publish` and the workflow of the
  `slsa-framework` generator. The default workflow token may stay read-only.
* Install `uv`, `git`, `gh`, `curl`, `flock` and `timeout`, and Python 3.12 or newer as `python3`. The checks of
  `scripts/release/` use the standard library alone.
* Log in with `gh auth login` (github.com, HTTPS) with a fine-grained token limited to `Axiumine/axmem`: Contents, Actions
  and Workflows with write access, Administration with read access (`publish-ready` reads the environment and the
  rulesets, with their bypass lists). Run `publish-ready` once and adjust the token to what it reports.
* Enable the hooks with `uv run python -m scripts.quality install`. A release makes a commit and pushes it, so the
  pre-commit hook and the GPU gate of the pre-push hook run: a release needs the NVIDIA GPU and Docker like any other push.
  `SKIP` and `ONLY`, which leave gates out of a stage, make the script refuse to start.
* The remote is `axiumine` (`RELEASE_REMOTE` in `scripts/lib/project.sh`, or the environment variable of that name),
  `https://github.com/Axiumine/axmem`. Check out the local branch that tracks `axiumine/main`, with HEAD equal to the pushed
  tip: the script refuses a branch that is behind, diverged or ahead of it. The remote must already hold
  `release-superseded.json` (see below).
* Merge the release flow into `axiumine/main` before the first release: `workflow_dispatch` needs `publish.yml` on the
  default branch and inside the tagged commit, and the pending publisher names that file. Never push all tags to
  `axiumine` (`git push --tags`): the clone holds the tags of Forgetful (`v0.0.1` to `v0.5.5`, `models-v1.0.0`).
* After the first upload, check on PyPI (project, Publishing) that only `publish.yml` with the environment `pypi` is listed,
  and that no API token exists. Then change the install notes that say the package is not on PyPI yet (`README.md`,
  `docs/`, `skills/axmem-*-setup`) in the commit that follows.

### Rehearsal with a release candidate

The first version goes up as a release candidate, because it settles what cannot be tried offline: the trusted publisher
(project, workflow `publish.yml`, environment `pypi`) and the settings that `publish-ready` reads, in the real service.
PyPI refuses a file name it has used, even for the same bytes, so a half-finished upload is not resumed by sending a file
twice: the job `publish` reads the project JSON and sends only the files that PyPI does not list.

1. Pick a version above the tags that came with the clone, for example `0.6.0`, and release `0.6.0rc1`:
   `bash scripts/release-pypi.sh 0.6.0rc1`. Approve the deployment when the script says it waits.
2. When it is green, dispatch the publish workflow a second time on the same tag:
   `gh workflow run publish.yml -R Axiumine/axmem --ref v0.6.0rc1 -f tag=v0.6.0rc1`.
3. A green second run, whose step `Leave out the files that PyPI already holds` names both files and whose step
   `Publish to PyPI` is skipped, proves that a finished upload is recognised and that nothing is sent twice. A red second
   run needs a look before `0.6.0` is released: `publish.yml` is frozen per tag, so a fix costs one more release candidate.
4. A release candidate whose run failed before PyPI took a file leaves a tag with nothing on PyPI. That is not an error:
   the next version releases normally, and the tag stays, because the rulesets forbid deleting it.

## Cutting a release

```bash
bash scripts/release-pypi.sh                # the last version on PyPI that is not yanked, nothing else happens
bash scripts/release-pypi.sh 0.6.0          # release 0.6.0
```

The version is `X.Y.Z`, with an optional `aN`, `bN`, `rcN`, `.postN` and `.devN` (`0.6.0`, `0.6.0rc1`), and nothing else.
It is written nowhere: the version of the package comes from the tag (hatch-vcs), and every build of the release pins it
with `SETUPTOOLS_SCM_PRETEND_VERSION`. The wheel and the sdist are `axmem-0.6.0-py3-none-any.whl` and `axmem-0.6.0.tar.gz`;
the script stops if the build makes anything else.

The steps, in order:

1. no argument: print the last version of `axmem` on PyPI that is not yanked, in PEP 440 order, and stop; a project that PyPI
   does not know yet has none. Otherwise the argument must be a version as above;
2. check the tools, the numeric waits, that `SKIP` and `ONLY` are unset, that no other release holds the lock
   (`release-pypi.lock` in the git directory), and that the remote is on github.com; then run `publish-ready`, which reads
   the settings of the repository and refuses a release that the settings would not protect. It changes nothing and runs
   every time;
3. fetch the remote, read the JSON of the project from `https://pypi.org/pypi/axmem/json`, and look in the history of HEAD for
   the release commit of this version (the manifest names the version and the tag `v<version>`). Found: a re-run (step 4).
   None: a new release (step 5). The state is read, never stored;
4. a re-run: the release commit must be on the remote's `main`, or be the only commit ahead of it, and the manifest on disk
   the committed one. If PyPI holds every file of the manifest with its sha256, the script verifies the integrity, checks
   the downloaded copies, prints `Release complete` and stops. If PyPI holds none or some of the files, it continues at the
   push (step 7). If PyPI holds other bytes, the version is spent and it stops;
5. a new release: the branch tracks `axiumine/main` and is in sync, the tracked tree is clean, the version is absent from PyPI
   (otherwise it is spent), the tag `v<version>` is free locally and on the remote and no remote branch has that name, the
   **release integrity** of PyPI holds (every version on it has a pushed tag, a manifest that lists its files and a signed
   release, or is named in `release-superseded.json`; the version in hand may hold a subset of its manifest), and CI is green
   on HEAD. A refusal changes nothing;
6. the preflight gate, `python -m scripts.quality stage ci`; stamp the changelog (`## [Unreleased]` becomes
   `## [v0.6.0] - <date>`, with a new empty Unreleased above it); pin the build clock (`SOURCE_DATE_EPOCH`, the commit time of
   HEAD) and the version; build once with `uv build`; check that the sdist holds only tracked files; run
   `scripts/check-package.sh` (the package gate, then `python -m scripts.quality wheel-check dist`: `twine check`,
   `check-wheel-contents`, and an install of the wheel on top of the locked dependencies into a clean environment, where
   `axmem --help` runs); write `release-digests.json`: the version,
   tag, epoch, tool versions and the sha256 of each file. Nothing is committed yet, so a re-run does it again: a failure
   anywhere between the stamp and the commit (a gate, the build, Ctrl-C) puts `CHANGELOG.md` and the manifest back to HEAD;
7. the release commit (changelog and manifest; the hooks run), then the push to the remote's `main` (the pre-push gate runs).
   A refused commit restores both paths to HEAD and stops; a re-run is a new release. A refused push keeps the local commit,
   and a re-run pushes it;
8. wait for CI on the release commit, tag it `v0.6.0` (annotated), push the tag. A tag at the commit is done, a tag
   elsewhere is an error; a pushed tag is never moved or deleted;
9. wait for the `release.yml` run that the push of the tag started (rebuild equals the manifest, cosign, SLSA), require a
   bundle for every file, download the wheel and the sdist from the GitHub release into a temporary directory and verify them
   against the manifest;
10. read PyPI again; if it already holds the manifest's files, verify and finish without a dispatch; if it holds other bytes,
    stop, the version is spent. Then run the guard (the five conditions, with the downloaded signed files as `dist`). A
    refusal stops before any dispatch;
11. look at the runs of `publish.yml` for this tag and commit: a live run is waited for; a successful run younger than 30
    minutes means no dispatch (PyPI's CDN may lag); otherwise
    `gh workflow run publish.yml --ref v0.6.0 -f tag=v0.6.0`, find the new run and wait for it (a dispatch is refused while a
    branch of the remote is named like the tag). The wait for the approval of the environment counts against the deadline
    below;
12. the run, in CI: the job `verify` (no id-token, no environment) checks that the run is on the tag and that the tag is the
    commit of the run, downloads the release, verifies the digests and `cosign verify-blob` with the identity of
    `release.yml` at the tag, runs the guard and `dist-check` (the standard library of the tag, no `uv`), checks the digests
    once more and writes the sha256 of every file into its summary. The job `publish` (environment `pypi`, `id-token: write`)
    starts when a reviewer approves, checks the sums again, reads the project JSON of PyPI, takes out of `dist` every file
    that PyPI lists with the same sha256 (a different sha256 fails the run) and uploads the rest with
    `pypa/gh-action-pypi-publish`; with nothing left, the upload is skipped;
13. poll the JSON of PyPI every 30 seconds until it lists every file of the manifest with the same sha256, for `RF_PYPI_LAG_S`
    (30 minutes) counted from the end of the green run, apart from the deadline below. A read that fails (a 5xx, a timeout)
    counts as a read that listed nothing, and is tried again;
14. download each file from `files.pythonhosted.org` (https only), verify it against the manifest and print
    `Release complete`. A re-run after this ends at step 4 with nothing to do.

Every wait is bounded: `RELEASE_POLL_INTERVAL_S` seconds apart (30), and one deadline of `RELEASE_WAIT_TIMEOUT_S` seconds
(28800, eight hours) from the first wait. At the deadline the script stops with nothing more done and prints the command
that resumes.

## When it stops

Every stop says what failed and prints the command that resumes. The state is read from git, GitHub and PyPI, never from a
file of the script's own, so running `scripts/release-pypi.sh <version>` again continues at the first step that is not done.
A pushed tag is never moved or deleted, and nothing is force-pushed.

| Stop | What it means |
|---|---|
| the release commit was refused | a hook refused it; the changelog and the manifest are restored to HEAD, nothing was pushed, tagged or dispatched; a re-run is a new release, and the version is not spent |
| the push was refused | the local release commit survives; a re-run finds it, accepts it as the only commit ahead and pushes it again; nothing is tagged before it is on `main` |
| CI is red on the release commit | the commit stays on `main`, no tag, PyPI untouched. If a job was flaky, `gh run rerun <id>` and re-run the script; otherwise fix it on `main` and release the next version |
| the signing run of the tag failed | the tag stays and PyPI is untouched. For flaky infrastructure, `gh run rerun <id>` of the run that the push of the tag started (`release.yml` has no manual trigger). A defect in `release.yml` or `publish.yml` at the tag cannot be fixed in place: release the next version |
| the dispatch failed (a `gh` error, 403, 404, or the run not seen within 120 seconds) | nothing was uploaded by the script. A re-run waits for a live run, or dispatches again; a double dispatch is harmless (the runs queue, and an identical upload is the same bytes) |
| the run waits for approval | the status `waiting` and the URL of the run are printed; a reviewer of the environment approves it, and the wait counts against the deadline. A re-run waits on the live run and never dispatches a second. A rejection is a failed run |
| the run was cancelled by a newer dispatch | the script switches to the newer run of the same tag and commit; with no newer run it is handled as a red run |
| the job `verify` refused (guard, cosign, digests, `dist-check`) | red before any approval or upload, PyPI holds nothing. A cause outside the tag (CI status, a GitHub outage, publisher or environment settings) is fixed and dispatched again; a defect in the files at the tag means the next version |
| the run is red after a partial upload (PyPI holds one file of two) | `integrity` accepts a subset of the manifest with equal sha256. A re-run passes the guard, finds no live run and dispatches again; the job `publish` leaves out the file PyPI lists and sends the missing one with the same signed bytes. If PyPI's listing lags and hides the file that was just uploaded, the upload fails on the name: run the script again once PyPI lists it |
| PyPI says a file name was used before | a deleted file keeps its name: the version is spent. Release the next version and list this one in `release-superseded.json`; a blind re-run would only dispatch another red run |
| the run is green and PyPI does not list the files | PyPI's CDN can lag. The script polls for 30 minutes from the green run, then stops with `run succeeded, PyPI not listing yet`. A re-run does not dispatch while the green run is younger than 30 minutes and polls again; later, if PyPI still lists nothing, it dispatches again, which is harmless |
| PyPI holds the version with other digests | the version is spent, nothing is dispatched. Release the next version and list this one in `release-superseded.json` with the sha256 that PyPI serves |
| PyPI holds the version and the remote has no tag | a re-run is refused by the integrity check, a new release by the version being on PyPI. Push the lost tag at the release commit if its digests equal PyPI's, otherwise list the version in `release-superseded.json` |
| PyPI holds every file of the manifest (a re-run after a success) | the integrity, the check of the downloaded copies, `Release complete`, exit 0; nothing is dispatched |
| the script died after the dispatch (Ctrl-C, the network) | a re-run reads the runs of `publish.yml`: a live or recent one is waited for and no second one is dispatched; then PyPI decides |

### A version that sits on PyPI without a tag

A failure around the upload can leave a version on PyPI that no tag and no signature match, and nothing can repair it: PyPI
never accepts a file name twice. The integrity check refuses to build another release on top of it, until the version is
named in `release-superseded.json` at the root of the repository, a JSON list that is empty (`[]`) now:

```json
[
  {
    "version": "0.6.0",
    "files": [
      {"name": "axmem-0.6.0-py3-none-any.whl", "sha256": "<64 lowercase hex characters>"},
      {"name": "axmem-0.6.0.tar.gz", "sha256": "<64 lowercase hex characters>"}
    ],
    "published": "2026-10-23",
    "reason": "PyPI refused a file name the failed run had used",
    "superseded_by": "0.6.1"
  }
]
```

The sha256 are the ones PyPI serves for the files. If PyPI serves other bytes for a listed version, the check refuses it.
The list that counts is the one on the remote's `main`, so a local edit does not excuse a version. Check a file with
`python3 -m scripts.release.superseded check release-superseded.json`.

## Checking a release

Two signatures cover a release, made by two workflows, and each is checked where it lives.

GitHub carries the files `release.yml` signed: the wheel, the sdist, one `.sigstore.json` bundle for each, and the
provenance file `axmem-artefacts.intoto.jsonl`. To verify them:

```bash
gh release download v0.6.0 --repo Axiumine/axmem --pattern 'axmem-0.6.0*'
cosign verify-blob axmem-0.6.0-py3-none-any.whl \
  --bundle axmem-0.6.0-py3-none-any.whl.sigstore.json \
  --certificate-identity "https://github.com/Axiumine/axmem/.github/workflows/release.yml@refs/tags/v0.6.0" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

PyPI carries the files that `publish.yml` uploaded, with their PEP 740 attestation, whose identity is `publish.yml` at the
tag. The files are the same bytes as the signed ones: compare the sha256 that PyPI serves (the `digests` of
`https://pypi.org/pypi/axmem/json`, or the page of the file) with the `release-digests.json` committed in the release commit,
or run the script again, which does it and prints `Release complete`:

```bash
bash scripts/release-pypi.sh 0.6.0
```

The keyless signature is recorded in the public Rekor transparency log, with the name of the repository.

## Known gap

The one read of PyPI in the shell (`rf_pypi_get`, a `curl` of a constant URL with `--proto =https`) is checked only against
a fake `curl`, because the URL is constant, so no test can point it at a local server. The argv of the call is pinned in the
fake, and the parts that judge the answer (`scripts/release/facts.py`, and `guard pypi-missing` for `publish.yml`) are tested
on real documents. The step of `publish.yml` that reads the same JSON is held by structure tests only. The first release is
the first real read.

## Files

| File | Role |
|---|---|
| `scripts/release-pypi.sh` | the release, step by step; it uploads nothing and dispatches the publish workflow |
| `scripts/lib/release-flow.sh` | the steps that read or change the state of a release |
| `scripts/lib/project.sh` | package name, remote, PyPI hosts, publish workflow and environment |
| `scripts/check-package.sh` | what a package must pass before a release |
| `scripts/release/guard.py` | the five conditions, and the reads of the flow |
| `scripts/release/publish.py` | the settings check (`publish-ready`) and the runs of `publish.yml` (dispatch, state, wait) |
| `scripts/release/facts.py` | what git, GitHub and the JSON of PyPI say about a release |
| `scripts/release/integrity.py` | the integrity check of every version on PyPI |
| `scripts/release/superseded.py` | the reader of `release-superseded.json` |
| `scripts/release/digests.py` | writes and verifies `release-digests.json` |
| `scripts/release/ci_green.py` | the CI check of a commit |
| `scripts/release/sdist.py` | refuses an sdist with files that git does not track |
| `.github/workflows/release.yml` | the rebuild, the digest check, the signing and the provenance; uploads nothing |
| `.github/workflows/publish.yml` | the verification of the signed files and the upload to PyPI; dispatched by the script on the tag |
