#!/usr/bin/env bash
#
# release-testpypi.sh — rehearse the upload of a release of the Axmem package on TestPyPI.
#
# Usage:
#   ./scripts/release-testpypi.sh <version>
#
# The rehearsal is advisory, not a gate, and PyPI is not touched: it reads test.pypi.org and test-files.pythonhosted.org,
# GitHub through gh, and git, and it dispatches publish-testpypi.yml, never publish.yml. The order of the two is by hand:
# nothing here is read by scripts/release-pypi.sh, and nothing there is read by this script. It proves that the signed
# bytes of the release upload to a package index, and that the trusted publisher, the environment testpypi and the
# upload step work (nothing installs the files from TestPyPI); it does not prove PyPI's own publisher or the environment pypi.
#
# It makes no release: no changelog stamp, no build, no commit, no tag, no push. It needs a release that scripts/release-pypi.sh
# has made up to the signing run (release commit pushed and green in CI, tag pushed, release.yml run green with a bundle per
# file), checked out as HEAD, and it uploads exactly the signed files of that release, the same bytes the upload to PyPI
# of that tag will carry. The only ref it writes is the remote-tracking ref refs/remotes/<remote>/main (the guard fetches
# it); HEAD, the branches and the tags are not changed.
#
# This script never uploads anything itself. The upload is made by .github/workflows/publish-testpypi.yml in CI, as trusted
# publishing: TestPyPI trusts that workflow's OIDC identity in the environment testpypi, whose reviewers approve every
# upload. No token, password or environment file exists in this flow, and none is read.
#
# Steps, in the order they run. Refused before anything is read: a version that is not canonical, a number of arguments
# other than one, waits that are not numbers above 0, a second rehearsal in the repository (its own lock, so that it runs
# beside a release that waits for the approval of the environment pypi), a remote that is not on github.com and a repository
# whose publication settings are not as the workflow relies on (scripts.release.guard testpypi-ready: every check of
# publish-ready, for the environment testpypi).
#   release commit of the version in HEAD's history (none: run release-pypi.sh first) -> HEAD is that tree -> wait for the
#   signing run -> download the signed files of the GitHub release, check them against the manifest -> THE GUARD
#   (scripts.release.guard check, five conditions) -> read TestPyPI -> dispatch of publish-testpypi.yml on the tag (or wait
#   for the run that is live; no second dispatch) -> wait for the run, which a reviewer of the environment testpypi must
#   approve -> read TestPyPI until it lists every file with the manifest's sha256 -> download TestPyPI's files, compare.
# Resume: the state is read from git, GitHub and TestPyPI, so re-running `release-testpypi.sh <version>` after any stop
# continues at the first unfinished step; a version TestPyPI already holds in full completes with no dispatch. A version
# TestPyPI holds with other bytes, or under a name it has used, is spent on TestPyPI only: the next version can be rehearsed,
# and PyPI is unaffected. Waits are bounded as in release-pypi.sh (RELEASE_POLL_INTERVAL_S, RELEASE_WAIT_TIMEOUT_S).
#
# The last line printed on success is "TestPyPI rehearsal complete: now approve environment pypi". See docs/release.md.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/lib/project.sh
source "$SCRIPT_DIR/lib/project.sh"
# shellcheck source=scripts/lib/release-flow.sh
source "$SCRIPT_DIR/lib/release-flow.sh"
# shellcheck source=scripts/lib/testpypi-flow.sh
source "$SCRIPT_DIR/lib/testpypi-flow.sh"

# Digest record of exactly what the index is given, committed in the release commit — see scripts/release/digests.py.
MANIFEST="${REPO_ROOT}/release-digests.json"

err() { echo "ERROR: $*" >&2; exit 1; }

# --- args ---------------------------------------------------------------
(( $# == 1 )) || err "exactly one argument, the version. Usage: $(basename "$0") <version>"
VERSION="$1"
rf_require_version "$VERSION"

echo "TestPyPI rehearsal of ${PKG} ${VERSION}: the signed files go to ${TESTPYPI_HOST} through ${TESTPYPI_WORKFLOW}. Advisory, not a gate. PyPI is not touched."

rf_require_tools git gh python3 curl flock timeout
rf_check_waits
tp_lock

TAG="v${VERSION}"
REMOTE_URL="$(git -C "$REPO_ROOT" remote get-url "$RELEASE_REMOTE")" \
  || err "the remote '${RELEASE_REMOTE}' is not configured in this clone"
[[ "$REMOTE_URL" =~ ^(git@github\.com:|https://github\.com/) ]] \
  || err "the remote '${RELEASE_REMOTE}' is not on github.com (got '${REMOTE_URL}')"
GH_REPO="$(printf '%s' "$REMOTE_URL" | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')"
[[ "$GH_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
  || err "the remote '${RELEASE_REMOTE}' is not a github.com repository (got '${GH_REPO}')"
# the publication settings the workflow relies on, read-only and checked on every start, before anything else is read
tp_require_ready
rf_init_tmp

# --- the release this rehearses: made by release-pypi.sh, read from git -------------------------------------------
RELEASE_SHA="$(rf_release_commit)"
[[ -n "$RELEASE_SHA" ]] \
  || err "no release commit of ${VERSION} in the history of HEAD — run: bash scripts/release-pypi.sh ${VERSION} (it stops at the approval of the environment pypi), then rehearse. The rehearsal makes no release of its own"
echo "Release commit ${RELEASE_SHA:0:8} of ${VERSION} found."
rf_require_release_tree
rf_wait_signing
rf_fetch_signed
tp_check

# --- what TestPyPI holds already --------------------------------------------------------------------------------
tp_load
state=0
tp_state >/dev/null || state=$?
case "$state" in
  0|4|5) ;;
  1) tp_state >&2 || true
     tp_spent ;;
  *) err "could not tell what TestPyPI holds for ${VERSION} (the JSON of ${TESTPYPI_JSON_URL} was unreadable) — nothing was dispatched" ;;
esac

if [[ "$state" != "0" ]]; then
  rf_require_no_branch_of_tag
  tp_run
  tp_wait_index
else
  echo "TestPyPI holds every file of ${VERSION} with the manifest's digests already — nothing to dispatch."
fi
tp_verify

echo "TestPyPI only; PyPI was not touched. ${PKG} ${VERSION} is on ${TESTPYPI_HOST} with the manifest's digests (release commit ${RELEASE_SHA:0:8}, tag ${TAG})."
tp_next
echo "TestPyPI rehearsal complete: now approve environment ${PUBLISH_ENVIRONMENT}"
