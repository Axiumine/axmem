#!/usr/bin/env bash
# testpypi-flow.sh — the steps of scripts/release-testpypi.sh that are not those of the PyPI release.
# SOURCED ONLY (after scripts/lib/project.sh and scripts/lib/release-flow.sh): defines functions and constants, runs nothing.
#
# The rehearsal uploads the signed files of a release that scripts/release-pypi.sh has already made (release commit, tag,
# signing run) to TestPyPI, so that the trusted publisher, the GitHub environment testpypi and the upload are tried on the
# scratch index before the PyPI upload that cannot be taken back. It is advisory, not a gate, and it never touches PyPI: it
# reads test.pypi.org and test-files.pythonhosted.org only, it dispatches publish-testpypi.yml only, and the steps it
# shares with the PyPI release (release-flow.sh: the release commit, the signing run, the signed files) read git and GitHub.
# Like the upload of PyPI, the upload of TestPyPI is made by a workflow in CI (trusted publishing, no token), never from
# this machine. No credential of any kind exists here and none is read.
#
# The caller sets what release-flow.sh asks for (SCRIPT_DIR REPO_ROOT VERSION TAG PKG MANIFEST GH_REPO RELEASE_REMOTE
# RELEASE_BRANCH RELEASE_SHA) and defines err(). The constants below are the shell's copy of the row `testpypi` of the table
# in scripts/release/facts.py and of .github/workflows/publish-testpypi.yml; tests/integration/test_release_testpypi_script.py
# holds the three together.

# Where the rehearsal goes: the scratch index, the workflow that uploads to it and the environment that workflow runs in.
TESTPYPI_HOST="test.pypi.org"                       # the JSON API of the project: https://<host>/pypi/<PKG>/json
TESTPYPI_FILES_HOST="test-files.pythonhosted.org"   # the only host the files are downloaded from
TESTPYPI_WORKFLOW="publish-testpypi.yml"            # the workflow that uploads; dispatched on the release tag
TESTPYPI_ENVIRONMENT="testpypi"                     # the GitHub environment it runs in (required reviewers, tag rule v*.*.*)
TESTPYPI_JSON_URL="https://${TESTPYPI_HOST}/pypi/${PKG}/json"

# Stop the rehearsal: nothing of the release was changed, PyPI was not touched, and TestPyPI may hold none, some or all of
# the files if the workflow was dispatched. A re-run reads GitHub and TestPyPI first and continues from what they hold.
#   $* why
tp_stop() {
  echo "STOPPED: $*" >&2
  echo "TestPyPI only: PyPI was not touched. The rehearsal is advisory. A re-run reads GitHub and TestPyPI first, then continues from what they hold:" >&2
  echo "    bash scripts/$(basename "$0") ${VERSION}" >&2
  exit 1
}

# One rehearsal at a time per repository, under its own lock: the PyPI release holds release-pypi.lock for as long as it
# waits for the approval of the environment pypi, and the rehearsal runs beside it, in a second terminal.
tp_lock() {
  local lock
  lock="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir)/release-testpypi.lock"
  exec 9>"$lock"
  flock -n 9 || err "another rehearsal is running in this repository (lock ${lock})"
}

# The settings the rehearsal relies on, read-only (scripts.release.guard testpypi-ready, every check of publish-ready for
# the environment testpypi): the workflow is active, the repository is public and the maintainer may push, the environment
# gates the upload and only tags v*.*.* may use it, and a ruleset protects the release tags. Runs on every start, before
# anything is read or dispatched.
tp_require_ready() {
  rf_py guard testpypi-ready --repo "$GH_REPO" \
    || err "the repository is not set up for the TestPyPI rehearsal (the reasons are listed above) — nothing was changed"
}

# Read the JSON of the project on TestPyPI into $RF_JSON (a project that is not on TestPyPI yet is an empty file: no versions).
# Returns 1 when TestPyPI or the network does not answer.
tp_try_load() {
  local rc=0
  rf_pypi_get "$TESTPYPI_JSON_URL" "$RF_JSON" || rc=$?
  [[ "$rc" == "0" || "$rc" == "44" ]]
}

tp_load() {
  tp_try_load || err "failed to read ${TESTPYPI_JSON_URL} (TestPyPI or the network is not answering)"
}

# What TestPyPI holds for this version, against the manifest. Prints one line per file. Returns 0 = exactly the manifest's
# files with its digests, 5 = some of them, with its digests, 4 = none, 1 = anything else (a digest differs, or a file the
# manifest lacks), 2 = the JSON could not be read. The same answers as rf_pypi_state, from the same code.
tp_state() {
  rf_py guard testpypi-state --json "$RF_JSON" --package "$PKG" --version "$VERSION" --manifest "$MANIFEST"
}

# A version TestPyPI holds with other bytes than the manifest's, or under a name it has used already, cannot be rehearsed
# again. PyPI is unaffected: the real release of this version is not spent by it.
tp_spent() {
  err "${PKG} ${VERSION} is on TestPyPI with other bytes than the manifest's (see above) — that version is spent on TestPyPI and cannot be rehearsed again; PyPI is unaffected. The next version can be rehearsed, or the release can go on without the rehearsal"
}

# The guard proves the five conditions of the invariant on the signed files (scripts.release.guard check) before the
# rehearsal is dispatched, as it does before the publication: the rehearsal never uploads what the release would not.
tp_check() {
  rf_py guard check --repo "$GH_REPO" --sha "$RELEASE_SHA" --tag "$TAG" \
    --root "$REPO_ROOT" --remote "$RELEASE_REMOTE" --dist "${RF_TMP}/signed" \
    || tp_stop "the release guard refused the rehearsal (the unmet conditions are listed above)"
}

# THE ONLY DISPATCH of this script: publish-testpypi.yml on the tag (or the run that is live, never a second). It
# uploads nothing itself: the job in the environment testpypi does, after its reviewers approve, with an OIDC identity and
# no stored credential. Success starts the clock of the TestPyPI lag.
tp_run() {
  local rc=0
  rf_set_deadline
  rf_py guard testpypi-run --repo "$GH_REPO" --sha "$RELEASE_SHA" --tag "$TAG" \
    --deadline "$RF_DEADLINE" --interval "$RELEASE_POLL_INTERVAL_S" || rc=$?
  case "$rc" in
    0) RF_GREEN_AT="$(date +%s)" ;;
    3) tp_stop "the ${TESTPYPI_WORKFLOW} run did not finish before the deadline (RELEASE_WAIT_TIMEOUT_S=${RELEASE_WAIT_TIMEOUT_S}) — approve the deployment of environment ${TESTPYPI_ENVIRONMENT} if it waits" ;;
    2) tp_stop "GitHub could not be asked for the ${TESTPYPI_WORKFLOW} run of ${TAG}" ;;
    *) tp_stop "the ${TESTPYPI_WORKFLOW} run of ${TAG} did not succeed or was not seen in time (see above)" ;;
  esac
}

# Read TestPyPI until it lists every file of the manifest with its digest. A read can lag behind an upload, so absence is
# not final before RF_PYPI_LAG_S seconds have passed since the green run; that limit is its own clock, not the deadline
# of the waits. A read that fails is a read that listed nothing: it is tried again, and at the limit it ends like absence,
# with the command that resumes. A file with other bytes (state 1) ends it at once.
tp_wait_index() {
  local state out limit
  limit=$(( ${RF_GREEN_AT:-$(date +%s)} + RF_PYPI_LAG_S ))
  while :; do
    state=4
    if tp_try_load; then
      state=0
      out="$(tp_state)" || state=$?
    else
      echo "WARNING: could not read ${TESTPYPI_JSON_URL} (TestPyPI or the network is not answering)." >&2
    fi
    case "$state" in
      0) echo "TestPyPI lists every file of ${VERSION} with the manifest's digests."; return 0 ;;
      1) printf '%s\n' "$out" >&2
         tp_spent ;;
      4|5) ;;
      *) echo "WARNING: the answer of TestPyPI could not be read as the project's JSON." >&2 ;;
    esac
    if (( $(date +%s) >= limit )); then
      tp_stop "the ${TESTPYPI_WORKFLOW} run succeeded but TestPyPI does not list the files yet (${RF_PYPI_LAG_S} s since the green run)"
    fi
    echo "TestPyPI does not list all files of ${VERSION} yet — reading again in ${RELEASE_POLL_INTERVAL_S} s."
    sleep "$RELEASE_POLL_INTERVAL_S"
  done
}

# After the upload: download the version's files from TestPyPI's file host (https only, the URLs the guard takes from the
# JSON, and no URL of another host) and compare their sha256 with the manifest. Needs the JSON to be current and in state 0.
tp_verify() {
  local dir="${RF_TMP}/testpypi-files" listed name url
  local safe='^[A-Za-z0-9][A-Za-z0-9._+-]*$'
  rm -rf "$dir"
  mkdir -m 700 "$dir" || err "could not create ${dir}"
  listed="$(rf_py guard testpypi-urls --json "$RF_JSON" --package "$PKG" --version "$VERSION")" \
    || err "could not list the files of ${VERSION} on TestPyPI"
  [[ -n "$listed" ]] || err "TestPyPI lists no file of ${VERSION}"
  while IFS=$'\t' read -r name url; do
    [[ "$name" =~ $safe ]] || err "TestPyPI lists a file name that is not a plain file name: '${name}'"
    [[ "$url" == "https://${TESTPYPI_FILES_HOST}/"* ]] \
      || err "TestPyPI lists ${name} at a host that is not ${TESTPYPI_FILES_HOST}: '${url}'"
    curl -sSLf --globoff --proto =https --proto-redir =https --connect-timeout 20 --max-time 600 -o "${dir}/${name}" "$url" \
      || tp_stop "could not download ${name} from ${TESTPYPI_FILES_HOST} (the network, or the file host, is not answering)"
  done <<<"$listed"
  rf_py digests verify --manifest "$MANIFEST" --dist "$dir" \
    || err "TestPyPI serves bytes that are not the manifest's (see above) — ${VERSION} is spent on TestPyPI; PyPI is unaffected"
}

# Tell the operator what is left: the PyPI upload is a separate dispatch, made by release-pypi.sh, and approved by the
# reviewers of the environment pypi. Read-only (guard publish-state lists the runs of publish.yml on the tag); a failure
# to read is a warning, never an error: the rehearsal has succeeded either way.
tp_next() {
  local out kind url
  if out="$(rf_py guard publish-state --repo "$GH_REPO" --sha "$RELEASE_SHA" --tag "$TAG" --lag "$RF_PYPI_LAG_S")"; then
    IFS=$'\t' read -r kind _ url _ <<<"$out"
    case "$kind" in
      live) echo "The ${PUBLISH_WORKFLOW} run of ${TAG} to approve in the environment ${PUBLISH_ENVIRONMENT}: ${url}" ;;
      recent) echo "The ${PUBLISH_WORKFLOW} run of ${TAG} succeeded a moment ago: ${url}" ;;
      *) echo "The ${PUBLISH_WORKFLOW} of ${TAG} has not been dispatched yet: continue the release with bash scripts/release-pypi.sh ${VERSION}" ;;
    esac
  else
    echo "WARNING: could not read the ${PUBLISH_WORKFLOW} runs of ${TAG}; the rehearsal does not depend on them." >&2
  fi
}
