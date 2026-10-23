#!/usr/bin/env bash
#
# release-pypi.sh — release the Axmem package to PyPI.
#
# Usage:
#   ./scripts/release-pypi.sh [version]
#
#   - no argument -> print the last version of the package on PyPI that is not yanked, then exit (read-only: needs only
#                    curl and python3, and touches nothing)
#   - <version>   -> changelog stamp, build, commit, push, tag, sign, dispatch of the publish workflow, check of PyPI
#
# The version is X.Y.Z with an optional aN, bN, rcN, .postN or .devN (PyPI's own spelling, so the tag v<version> and the
# file names agree). It is not written anywhere: it comes from the tag (hatch-vcs). Every build of a release pins it
# with SETUPTOOLS_SCM_PRETEND_VERSION, and the release is the tag v<version>.
#
# This script never uploads anything itself. The upload is made by .github/workflows/publish.yml in
# CI, as trusted publishing: PyPI trusts that workflow's OIDC identity in the environment pypi, whose reviewers approve
# every upload. No token, password or environment file exists in this flow, and none is read: the script reads PyPI's
# public JSON and files, GitHub through gh, and git.
#
# Publish path, in the order it runs. PyPI refuses a second upload of a file name for good (even after a delete), so the
# dispatch is the LAST step: every step that can still fail runs before it, and a version reaches PyPI only when its exact
# release commit is pushed, CI-green, tagged and signed.
# Refused before anything runs: a version that is not canonical, SKIP or ONLY set (gates left out of the hooks), a second
# release running in the repository, wait variables that are not numbers above 0, a remote that is not on github.com and
# a repository whose publication settings are not as publish.yml relies on (scripts.release.guard publish-ready: the
# repository public, the environment pypi with required reviewers, no admin bypass and the one rule v*.*.* for tags, a
# ruleset that protects the v* tags). A new release is also refused for a tracked change in the tree, a tag name that is
# taken (locally, on the remote, or as a remote branch) and a version that is already on PyPI.
#   fetch + sync checks -> clean tracked tree, tag free, version absent from PyPI -> release integrity of PyPI
#   (read-only) -> CI-green check of HEAD -> preflight gate -> changelog stamp -> build -> sdist and package checks ->
#   digest manifest -> RELEASE COMMIT (the repository's hooks run) -> push of main (the pre-push gate) -> wait for CI on
#   the release commit -> annotated tag on the release commit, push of the tag -> wait for the tag's release.yml run
#   (rebuild, digest check, cosign, SLSA) and require a bundle per artefact on the GitHub release -> download the signed
#   files from that release and check them against the manifest -> read PyPI again -> THE GUARD (scripts.release.guard
#   check, five conditions, on the downloaded files) -> dispatch of publish.yml on the tag (or wait for the run that is
#   live; no second dispatch) -> wait for the run, which a reviewer of the environment pypi must approve -> read PyPI
#   until it lists every file with the manifest's sha256 -> download PyPI's files, compare.
# The invariant is in code: publish_release runs the guard first, and it is the only place that dispatches the publish
# workflow; no upload command and no token exists anywhere in scripts/ (a structure test pins that).
# A commit CI has not verified is exactly what the guard refuses.
# A refused commit, and any failure between the changelog stamp and the commit, restores the release paths and exits;
# nothing before the dispatch is irreversible.
# Resume: the state is read from git, GitHub and PyPI, never from a file of the script's own, so re-running
# `release-pypi.sh <version>` after any stop continues at the first unfinished step (the release commit, its push, CI,
# the tag, the signing run, the dispatch, the wait for PyPI). A pushed tag is never moved or deleted and nothing is
# force-pushed. Waits are bounded: RELEASE_POLL_INTERVAL_S (30) between polls, one deadline of RELEASE_WAIT_TIMEOUT_S
# (28800 = 8 h) from the first wait, in which the approval of the environment counts too; at the deadline the script stops
# with the command that resumes. The wait for PyPI to list the files has its own limit (30 minutes from the green run).
# A version PyPI holds with other bytes, or with a name PyPI already used, is spent: release the next version and list
# the spent one in release-superseded.json.
#
# Where it runs: on the local branch that tracks the remote's main (RELEASE_REMOTE, default axiumine, see
# scripts/lib/project.sh), with HEAD equal to the pushed tip. See docs/release.md.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/lib/project.sh
source "$SCRIPT_DIR/lib/project.sh"
# shellcheck source=scripts/lib/release-flow.sh
source "$SCRIPT_DIR/lib/release-flow.sh"

# Digest record of exactly what PyPI is given, committed in the release commit
# and therefore reachable from the tag — see scripts/release/digests.py.
MANIFEST="${REPO_ROOT}/release-digests.json"

err() { echo "ERROR: $*" >&2; exit 1; }

# Rename the '## [Unreleased]' heading to a dated version section and open a
# fresh empty '## [Unreleased]' above it (keep-a-changelog release flow).
# Idempotent: re-running for the same tag is a no-op.
#   $1 changelog path   $2 version section heading text (e.g. 'v0.6.0')
stamp_changelog() {
  local file="$1" tag="$2" date
  date="$(date +%F)"
  [[ -f "$file" ]] || err "CHANGELOG.md not found at $file"
  if grep -qF "## [${tag}]" "$file"; then
    echo "CHANGELOG already has section [${tag}] — skipping stamp."
    return 0
  fi
  grep -qF '## [Unreleased]' "$file" \
    || err "CHANGELOG.md missing '## [Unreleased]' section to stamp"
  local tmp
  tmp="$(mktemp)"
  # not 'awk ... && mv': a failing awk in the middle of an && list does not trip 'set -e'
  awk -v hd="## [${tag}] - ${date}" '
    !stamped && $0 == "## [Unreleased]" {
      print; print ""; print hd
      stamped = 1
      next
    }
    { print }
  ' "$file" >"$tmp" || { rm -f "$tmp"; err "the CHANGELOG stamp failed (awk)"; }
  mv "$tmp" "$file"
  grep -qF "## [${tag}]" "$file" || err "the CHANGELOG has no section [${tag}] after the stamp"
  echo "Stamped CHANGELOG section [${tag}] - ${date}."
}

# Put the release paths back to HEAD, in the index and in the tree; a path that HEAD does not hold is removed. One at a
# time, and a path that is neither in HEAD nor on disk (the manifest of a release that stopped before it was written) is
# passed over: git refuses a pathspec that matches nothing, and would restore none of the others.
#   $@ the release paths (absolute)
restore_release_paths() {
  local path
  for path in "$@"; do
    [[ -e "$path" ]] || git -C "$REPO_ROOT" cat-file -e "HEAD:${path#"${REPO_ROOT}"/}" 2>/dev/null || continue
    git -C "$REPO_ROOT" restore --source=HEAD --staged --worktree -- "$path" \
      || echo "WARNING: could not restore ${path} — run: git -C ${REPO_ROOT} restore --source=HEAD --staged --worktree -- ${path}" >&2
  done
}

# The release paths hold a change that no commit has, from the changelog stamp to the release commit. Any exit in
# between (a failed gate or build, a refused commit, Ctrl-C) puts them back, so the tree is clean again and a re-run
# is a new release. Also removes the scratch directory.
RELEASE_UNCOMMITTED=""
release_exit() {
  if [[ -n "$RELEASE_UNCOMMITTED" ]]; then
    RELEASE_UNCOMMITTED=""
    restore_release_paths "$CHANGELOG" "$MANIFEST" || true
  fi
  [[ -z "$RF_TMP" ]] || rm -rf "$RF_TMP"
}

# Commit the release paths BEFORE anything is pushed or uploaded. The repository's hooks run on this
# commit (the pre-commit suite, on the stamped tree) and can refuse it. A refusal restores exactly these
# paths to HEAD and exits, so nothing is published and no change to a tracked path is left behind.
#   $@ the release paths (absolute)
commit_release() {
  if git -C "$REPO_ROOT" add -- "$@" \
    && git -C "$REPO_ROOT" commit -m "🤖 chore(release): release ${PKG} ${VERSION} to PyPI" -- "$@"; then
    RELEASE_UNCOMMITTED=""
    echo "Committed the release ${VERSION}."
    return 0
  fi
  RELEASE_UNCOMMITTED=""
  restore_release_paths "$@"
  err "the release commit was refused — nothing was published, and the release paths were restored to HEAD. Fix what the hook reported above, then re-run."
}

# --- args ---------------------------------------------------------------
(( $# <= 1 )) || err "too many arguments. Usage: $(basename "$0") [version]"
VERSION="${1:-}"
[[ -z "$VERSION" ]] || rf_require_version "$VERSION"

# --- no version -> report last remote version, then exit (read-only) ----
if [[ -z "$VERSION" ]]; then
  rf_require_tools curl python3
  rf_init_tmp
  echo "No version provided — querying the last version of '${PKG}' on PyPI..."
  rf_load_pypi
  rc=0
  last="$(rf_py guard pypi-last --json "$RF_JSON" --package "$PKG")" || rc=$?
  case "$rc" in
    0) echo "Last published version: ${last}" ;;
    4) echo "No published versions of ${PKG} on PyPI yet." ;;
    *) err "could not read the versions of ${PKG} from PyPI" ;;
  esac
  echo "Re-run to publish: $(basename "$0") <new-version>"
  exit 0
fi

# --- publish path -------------------------------------------------------
rf_require_tools uv git gh python3 curl flock timeout
rf_check_waits
rf_require_hooks_armed
rf_lock

# release tag + changelog target
TAG="v${VERSION}"
CHANGELOG="${REPO_ROOT}/CHANGELOG.md"
REMOTE_URL="$(git -C "$REPO_ROOT" remote get-url "$RELEASE_REMOTE")" \
  || err "the remote '${RELEASE_REMOTE}' is not configured in this clone"
[[ "$REMOTE_URL" =~ ^(git@github\.com:|https://github\.com/) ]] \
  || err "the remote '${RELEASE_REMOTE}' is not on github.com (got '${REMOTE_URL}')"
GH_REPO="$(printf '%s' "$REMOTE_URL" | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')"
[[ "$GH_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
  || err "the remote '${RELEASE_REMOTE}' is not a github.com repository (got '${GH_REPO}')"
# the publication settings the workflow relies on, read-only and checked on every start (see rf_require_dispatchable)
rf_require_dispatchable
rf_init_tmp
trap release_exit EXIT

# What is already true of this release, read from git and PyPI: fetch first, then look for a release
# commit of this version in HEAD's history (a re-run) before deciding what is left to do.
rf_net git -C "$REPO_ROOT" fetch --quiet "$RELEASE_REMOTE" "+refs/heads/${RELEASE_BRANCH}:${RF_MAIN}" \
  || err "git fetch ${RELEASE_REMOTE} ${RELEASE_BRANCH} failed — cannot verify the branch is up to date"
rf_load_pypi
RELEASE_SHA="$(rf_release_commit)"

if [[ -n "$RELEASE_SHA" ]]; then
  # --- a re-run: the release commit exists, continue from it ------------------
  echo "Release commit ${RELEASE_SHA:0:8} of ${VERSION} found — continuing the release from it."
  rf_require_resumable
  state=0
  rf_pypi_state "$MANIFEST" || state=$?
  case "$state" in
    0|4|5) ;;
    1) err "${PKG} ${VERSION} is on PyPI with other bytes than the manifest's (see above) — that version is spent; release the next one and list this one in release-superseded.json" ;;
    *) err "could not tell what PyPI holds for ${VERSION} (the JSON of ${PYPI_JSON_URL} was unreadable)" ;;
  esac
  rf_integrity
  if [[ "$state" == "0" ]]; then
    rf_verify_pypi
    echo "Release complete: ${PKG} ${VERSION} is on PyPI with the manifest's digests; nothing to dispatch."
    exit 0
  fi
else
  # --- a new release ------------------------------------------------------
  # a release commits and tags the integration branch: the local branch that tracks the remote's main
  rf_require_release_branch
  rf_require_synced_main
  rf_require_clean_tree
  rf_require_tag_free
  state=0
  rf_pypi_state "" >/dev/null || state=$?
  case "$state" in
    4) ;;
    1) err "${PKG} ${VERSION} is already on PyPI and no release commit of it exists in this history — PyPI never accepts a file name twice; that version is spent, release the next one" ;;
    *) err "could not tell what PyPI holds for ${VERSION} (the JSON of ${PYPI_JSON_URL} was unreadable)" ;;
  esac
  rf_integrity

  # The exact commit must be pushed and green in CI: the local gate below cannot see what only GitHub Actions
  # runs. The release commit is awaited the same way after the push, and the guard asks again before the dispatch.
  LOCAL_TIP="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  rf_py ci_green --repo "$GH_REPO" --sha "$LOCAL_TIP" \
    || err "CI is not green for ${LOCAL_TIP:0:8} — fix it or wait for it, then release."

  # --- preflight gate (the static gates of the hosted CI) ------------------
  # The release commit below runs the pre-commit hook and its push the pre-push gate (tests, end-to-end,
  # coverage on the GPU); this fails early and cheaply on what any machine can see. Nothing is pushed yet.
  ( cd "$REPO_ROOT" && uv run --frozen python -m scripts.quality stage ci ) \
    || err "Preflight gate failed — fix above before releasing."

  echo "Releasing ${PKG} ${VERSION} -> PyPI (${PYPI_JSON_URL})"

  # stamp the changelog: '## [Unreleased]' -> '## [<tag>] - <date>'. From here until the commit, an exit restores it.
  RELEASE_UNCOMMITTED=1
  stamp_changelog "$CHANGELOG" "$TAG"

  # pin the version and the build clock. This build and the one release.yml
  # signs on the tag must be the SAME BYTES: the signature and the SLSA provenance are worth nothing if they
  # attest an artifact that is not the one PyPI serves to `pip install`. hatchling is reproducible given
  # SOURCE_DATE_EPOCH (and the version, see rf_pin_version), so every build pins both and the manifest below
  # carries them to CI. The epoch is the CURRENT HEAD's commit time: deterministic (no wall clock), and fixed
  # before the release commit exists, which is why CI reads it from the manifest instead of deriving it from the
  # tagged commit.
  SOURCE_DATE_EPOCH="$(git -C "$REPO_ROOT" log -1 --pretty=%ct)"
  [[ -n "$SOURCE_DATE_EPOCH" ]] || err "could not resolve SOURCE_DATE_EPOCH from git"
  export SOURCE_DATE_EPOCH
  rf_pin_version

  # clean + build
  rm -rf "${REPO_ROOT}/dist"
  ( cd "$REPO_ROOT" && uv build ) || err "uv build failed"
  rf_require_built_version

  # hatchling's sdist honours only the root .gitignore (not nested ones, not .git/info/exclude), so
  # untracked files can land in the tarball: per machine. CI rebuilds from a clean checkout of the tag,
  # gets different bytes, and refuses to sign. Catch it here, before anything is pushed.
  rf_py sdist --dist "${REPO_ROOT}/dist" --repo "$REPO_ROOT" \
    || err "sdist contains untracked files — it cannot be reproduced or signed."

  # package validation gate — what the package holds, twine check, wheel contents, clean-environment smoke test
  # (BUILD=0: validate the dist/ we just built, no rebuild)
  BUILD=0 bash "$SCRIPT_DIR/check-package.sh" \
    || err "Package check failed — fix above before releasing."

  # record the digests, and commit them below with the changelog stamp, so the tag states what PyPI will be
  # given. release.yml rebuilds at this epoch and refuses to sign anything that is not byte-identical to it.
  rf_py digests write \
    --manifest "$MANIFEST" \
    --dist "${REPO_ROOT}/dist" \
    --package "$PKG" \
    --version "$VERSION" \
    --tag "$TAG" \
    --epoch "$SOURCE_DATE_EPOCH" \
    || err "failed to write the release digest manifest"

  # the release commit (see commit_release): the changelog stamp and the digest manifest, so the commit the
  # tag points at is the tree that was built and will be published. $MANIFEST joins it: a tag without it
  # cannot prove the signed artifact is the published one, and release.yml fails the release when it is
  # absent rather than signing an unprovable build.
  commit_release "$CHANGELOG" "$MANIFEST"
  RELEASE_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
fi

# --- from here the release commit exists: every stop prints the command that resumes ---
rf_require_release_tree
rf_push_release
rf_wait_ci
rf_tag
rf_wait_signing
rf_fetch_signed
rf_recheck_pypi
publish_release
rf_wait_pypi
rf_verify_pypi

echo "Release complete: ${PKG} ${VERSION} on PyPI — commit ${RELEASE_SHA:0:8} on ${RELEASE_REMOTE}/${RELEASE_BRANCH}, signed tag ${TAG}, and PyPI serves the manifest's bytes."
