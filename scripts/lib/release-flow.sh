#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

# release-flow.sh — the steps of scripts/release-pypi.sh that read or change the state of a release.
# SOURCED ONLY: defines functions, runs nothing.
#
# The release script never uploads anything itself. The upload is made by the publish workflow (publish.yml, trusted
# publishing: PyPI trusts the workflow's OIDC identity, so no token exists anywhere), and the script dispatches it last,
# and only after `scripts.release.guard` has proved the release (see its docstring): the release commit is on the
# remote's main, CI is green on it, the tag is on the remote, the signing run succeeded, the bundles are on the GitHub
# release and the files to publish are the manifest's bytes, downloaded from that release. These functions are those
# steps. Every one reads the state from git, GitHub and PyPI, never from a file of its own, so a re-run of the script
# continues at the first unfinished step.
#
# The caller sets, before it calls any of them:
#   SCRIPT_DIR REPO_ROOT VERSION TAG PKG MANIFEST GH_REPO RELEASE_REMOTE RELEASE_BRANCH
#   RELEASE_SHA (the release commit, once it exists)
# (scripts/lib/project.sh, sourced first, supplies PKG, RELEASE_REMOTE, RELEASE_BRANCH and the PyPI settings) and defines
# err() (print "ERROR: ..." and exit 1). Nothing here reads an environment file or holds a credential: PyPI is read
# through its public JSON API.
#
# What the flow refuses before it does anything: a version that is not canonical (rf_require_version),
# waits that are not numbers above 0 (rf_check_waits), gates left out of the hooks (rf_require_hooks_armed),
# a repository whose publication settings are not as the workflow relies on (rf_require_dispatchable), a second
# release in the same repository (rf_lock), a tracked change in the tree (rf_require_clean_tree) and a tag name that
# is already taken (rf_require_tag_free).

RELEASE_POLL_INTERVAL_S="${RELEASE_POLL_INTERVAL_S:-30}"
RELEASE_WAIT_TIMEOUT_S="${RELEASE_WAIT_TIMEOUT_S:-28800}"
# A fetch or ls-remote that has not answered in this long is an error (the pre-push gate of a push is not limited).
RF_NET_TIMEOUT_S=180
# PyPI purges its cache on an upload, but a read can still lag behind it: a version is not called absent from the JSON
# before this many seconds have passed since the publish run went green. Independent of the deadline of the waits.
RF_PYPI_LAG_S=1800
# The JSON API of the project (public, no credential, no cache header: the CDN ignores one).
PYPI_JSON_URL="https://${PYPI_HOST}/pypi/${PKG}/json"
# The remote's main as this clone tracks it, spelled in full: a bare 'axiumine/main' is resolved by git against
# tags and local branches first, so a local tag of that name would stand in for the remote.
RF_MAIN="refs/remotes/${RELEASE_REMOTE}/${RELEASE_BRANCH}"
RF_TMP=""
RF_JSON=""
RF_DEADLINE=""
RF_GREEN_AT=""

# Run one of the checks of scripts/release (python -m scripts.release.<module>) from the repository root, where the
# package is importable. They use the standard library alone, so no environment is needed.
#   $1 module   rest: its arguments
rf_py() {
  local module="$1"
  shift
  ( cd "$REPO_ROOT" && python3 -m "scripts.release.${module}" "$@" )
}

# Run a command that talks to the remote with a time limit.
rf_net() {
  timeout "$RF_NET_TIMEOUT_S" "$@"
}

# The tools the script needs are on the PATH, and python3 is new enough for scripts/release (generics syntax, PEP 695;
# nothing outside the standard library).
#   $@ tool names
rf_require_tools() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || err "$tool not found in PATH"
  done
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' \
    || err "python3 is older than 3.12, which scripts/release needs"
}

# The version is canonical PEP 440 with three numeric parts, so the package index, the tag filter of
# release.yml (v*.*.*) and every grep and sed below see the same string. 0.3.0-rc1 would reach PyPI as
# 0.3.0rc1, a name no tag matches, and 1.0 would never trigger the signing run.
#   $1 version
rf_require_version() {
  local part='(0|[1-9][0-9]*)'
  [[ "$1" =~ ^${part}\.${part}\.${part}((a|b|rc)${part})?(\.post${part})?(\.dev${part})?$ ]] \
    || err "invalid version '$1' — use X.Y.Z with optional aN, bN, rcN, .postN, .devN (for example 0.3.0 or 0.3.0rc1)"
}

# The two knobs of the waits are numbers above 0: the deadline is computed with shell arithmetic, which
# would run anything else as an expression, and an interval of 0 would poll GitHub in a tight loop.
rf_check_waits() {
  [[ "$RELEASE_WAIT_TIMEOUT_S" =~ ^[1-9][0-9]*$ ]] \
    || err "RELEASE_WAIT_TIMEOUT_S must be a whole number of seconds above 0 (got '${RELEASE_WAIT_TIMEOUT_S}')"
  [[ "$RELEASE_POLL_INTERVAL_S" =~ ^[0-9]+(\.[0-9]+)?$ && "$RELEASE_POLL_INTERVAL_S" =~ [1-9] ]] \
    || err "RELEASE_POLL_INTERVAL_S must be a number of seconds above 0 (got '${RELEASE_POLL_INTERVAL_S}')"
}

# The hooks are the controls that must not refuse a release late: a release does not run with them skipped. SKIP and
# ONLY leave gates out of, or run only some gates of, the stages of the commit and of the push (docs/quality-gates.md).
rf_require_hooks_armed() {
  local name
  for name in SKIP ONLY; do
    [[ -z "${!name:-}" ]] || err "${name} is set — a release runs every gate of the commit and push hooks, unset it"
  done
}

# One release at a time per repository: two runs would race on the commit, the tag and the upload.
rf_lock() {
  local lock
  lock="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir)/release-pypi.lock"
  exec 9>"$lock"
  flock -n 9 || err "another release is running in this repository (lock ${lock})"
}

# Releases bump nothing: the version comes from the tag (hatch-vcs), so a release is cut from the local branch that
# tracks the remote's main, whatever it is called, and is pushed to that remote branch from there.
rf_on_release_branch() {
  [[ "$(git -C "$REPO_ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)" \
    == "${RELEASE_REMOTE}/${RELEASE_BRANCH}" ]]
}

rf_require_release_branch() {
  rf_on_release_branch \
    || err "releases run on the local branch that tracks ${RELEASE_REMOTE}/${RELEASE_BRANCH} (current branch: $(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)) — check that branch out"
}

# The version of every build of a release is set from the release itself, never from git: before the tag exists the
# commit would build as a development version of the one before it. The pin is kept for byte reproducibility: the
# build here and the one release.yml signs must name the same version to be the same bytes.
# Exported for every build of the script, and by release.yml for the one it signs, from the manifest.
rf_pin_version() {
  SETUPTOOLS_SCM_PRETEND_VERSION="$VERSION"
  export SETUPTOOLS_SCM_PRETEND_VERSION
}

# The build produced the files of exactly this version, under the names the manifest, the integrity check and the
# signing run use. A version the build normalised to another spelling would be signed and published under it.
rf_require_built_version() {
  local name
  for name in "${PKG//-/_}-${VERSION}-py3-none-any.whl" "${PKG}-${VERSION}.tar.gz"; do
    [[ -f "${REPO_ROOT}/dist/${name}" ]] \
      || err "the build did not produce dist/${name} (dist/ holds: $(find "${REPO_ROOT}/dist" -maxdepth 1 -type f -printf '%f\n' 2>/dev/null | sort | tr '\n' ' '))"
  done
}

# A fresh release builds and commits exactly the tree that is checked out: a tracked file with uncommitted
# changes would be built into the wheel (and the sdist) yet absent from the tag, so CI would rebuild other
# bytes, and a file marked skip-worktree or assume-unchanged hides such a change from git altogether.
rf_require_clean_tree() {
  local hidden
  [[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)" ]] \
    || err "tracked files have uncommitted changes — commit or restore them first (git status); a release builds and commits exactly what is in the tree"
  hidden="$(git -C "$REPO_ROOT" ls-files -v | grep '^[Ssh]' || true)"
  [[ -z "$hidden" ]] \
    || err "a tracked file is marked skip-worktree or assume-unchanged, so git cannot see its changes — clear the mark first (git update-index --no-skip-worktree / --no-assume-unchanged): ${hidden%%$'\n'*}"
}

# The release commit's tree is what is checked out, with no tracked change: the pre-push gate tests the working tree
# (docs/quality-gates.md) and the guard reads the manifest on disk, so the push and the checks run on what is published.
rf_require_release_tree() {
  rf_require_clean_tree
  [[ "$(git -C "$REPO_ROOT" rev-parse "HEAD^{tree}")" == "$(git -C "$REPO_ROOT" rev-parse "${RELEASE_SHA}^{tree}")" ]] \
    || err "HEAD is not the tree of the release commit ${RELEASE_SHA:0:8} — check that commit out and re-run: the pre-push gate tests the working tree and the guard reads the manifest on disk"
}

# A scratch directory for the PyPI JSON and the files downloaded to compare, removed on exit.
rf_init_tmp() {
  RF_TMP="$(mktemp -d "${TMPDIR:-/tmp}/release-flow.XXXXXX")"
  RF_JSON="${RF_TMP}/pypi.json"
  trap 'rm -rf "$RF_TMP"' EXIT
}

# Stop after the release commit exists and before the publish workflow was dispatched: nothing was uploaded, and the
# release continues from here.
#   $* why
rf_stop() {
  echo "STOPPED: $*" >&2
  echo "Nothing was uploaded to PyPI. Fix the cause, then continue where it stopped with:" >&2
  echo "    bash scripts/$(basename "$0") ${VERSION}" >&2
  exit 1
}

# Stop at or after the dispatch of the publish workflow: its run may be live or done, and PyPI may hold none, some
# or all of the files. Nothing is dropped or reset; the next run reads PyPI and GitHub first and continues from what
# they hold (a live run is waited for, never a second dispatched).
#   $* why
rf_stop_dispatched() {
  echo "STOPPED: $*" >&2
  echo "The ${PUBLISH_WORKFLOW} run of ${TAG} may be live or done, and PyPI may hold none, some or all of its files. A re-run reads PyPI and GitHub first, then continues from what they hold:" >&2
  echo "    bash scripts/$(basename "$0") ${VERSION}" >&2
  exit 1
}

# GET a PyPI URL (public: no credential, no header). Returns 0 on a 2xx answer, 44 on a 404 (the project is not on
# PyPI yet; the file is left empty), 1 otherwise. Only https, also across redirects; no globbing, so a brace or a
# bracket in a URL is one URL.
#   $1 url   $2 output file
rf_pypi_get() {
  local code
  code="$(curl -sSL --globoff --proto =https --proto-redir =https --connect-timeout 20 --max-time 600 -o "$2" -w '%{http_code}' "$1")" || return 1
  case "$code" in
    2??) return 0 ;;
    404) : >"$2"; return 44 ;;
    *) return 1 ;;
  esac
}

# Read the JSON of the project into $RF_JSON (a project that is not on PyPI yet is an empty file: no versions).
# Returns 1 when PyPI or the network does not answer; the caller decides whether that ends the script.
rf_try_load_pypi() {
  local rc=0
  rf_pypi_get "$PYPI_JSON_URL" "$RF_JSON" || rc=$?
  [[ "$rc" == "0" || "$rc" == "44" ]]
}

rf_load_pypi() {
  rf_try_load_pypi || err "failed to read $PYPI_JSON_URL (PyPI or the network is not answering)"
}

# The settings the publication relies on, read-only (scripts.release.guard publish-ready): the workflow is active, the
# repository is public and the maintainer may push, the environment gates the upload and only tags v*.*.* may use it,
# and a ruleset protects the release tags. Runs on every start, before anything changes.
rf_require_dispatchable() {
  rf_py guard publish-ready --repo "$GH_REPO" \
    || err "the repository is not set up for the publication (the reasons are listed above) — nothing was changed"
}

# The tag name is free: not locally, not on the remote, and no branch of that name on the remote (a branch named like
# the tag would be what the workflow checks out when it is given a short name). A pushed tag is never moved, so a
# taken name ends a new release. This clone holds the inherited Forgetful tags v0.0.1..v0.5.5: a version at or below
# 0.5.5 collides with one of them.
rf_require_tag_free() {
  local found
  git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/${TAG}" >/dev/null \
    && err "the tag ${TAG} exists locally — a release needs a version whose tag is free (this clone holds the inherited Forgetful tags v0.0.1..v0.5.5)"
  found="$(rf_net git -C "$REPO_ROOT" ls-remote --tags --heads "$RELEASE_REMOTE" "refs/tags/${TAG}" "refs/heads/${TAG}")" \
    || err "git ls-remote ${RELEASE_REMOTE} failed — cannot tell whether ${TAG} is free"
  [[ -z "$found" ]] \
    || err "the name ${TAG} is already taken on ${RELEASE_REMOTE} (${found##*[[:space:]]}) — release another version (the inherited Forgetful tags v0.0.1..v0.5.5 of this clone are taken too)"
}

# No branch of the remote is named like the tag: the dispatch names its ref as a short name, which GitHub resolves branch
# first. The environment's tag-only rule would still refuse such a run, but it is stopped here, with the cause.
# (rf_require_tag_free asks the same for a new release.)
rf_require_no_branch_of_tag() {
  local found
  found="$(rf_net git -C "$REPO_ROOT" ls-remote --heads "$RELEASE_REMOTE" "refs/heads/${TAG}")" \
    || rf_stop "git ls-remote ${RELEASE_REMOTE} failed — cannot tell whether a branch is named ${TAG}"
  [[ -z "$found" ]] \
    || rf_stop "a branch named ${TAG} exists on ${RELEASE_REMOTE}, which would make the ref of the dispatch ambiguous — delete that branch, then re-run"
}

# Release integrity (scripts.release.integrity): every version already on PyPI has a pushed tag
# at a commit whose manifest lists PyPI's files, and a signed release, or is named in the pushed
# release-superseded.json. This version may hold a subset of its manifest's files (a partly finished upload).
# Read-only; needs $RF_JSON and the fetched remote main.
rf_integrity() {
  local listed="${RF_TMP}/release-superseded.json"
  # the list that counts is the pushed one: a local edit (or a skip-worktree mark) must not excuse a version
  git -C "$REPO_ROOT" show "${RF_MAIN}:release-superseded.json" >"$listed" \
    || err "release-superseded.json is not on ${RELEASE_REMOTE}/${RELEASE_BRANCH} — the integrity check reads the pushed list"
  rf_py integrity --json "$RF_JSON" --package "$PKG" \
    --repo "$GH_REPO" --superseded "$listed" --version "$VERSION" --root "$REPO_ROOT" --remote "$RELEASE_REMOTE" \
    || err "the release integrity check failed — an earlier release left PyPI in a state this one must not build on (see above)"
}

# Print the sha of the release commit of this version, if HEAD's history holds one: the last commit that
# touched the digest manifest, when the manifest there names this version and tag. Prints nothing
# when there is none (a release not yet committed).
rf_release_commit() {
  local out rc=0
  out="$(rf_py guard find-release --version "$VERSION" --root "$REPO_ROOT")" || rc=$?
  case "$rc" in
    0) printf '%s' "$out" ;;
    4) ;;
    *) err "could not look for the release commit of ${VERSION}" ;;
  esac
}

# What PyPI holds for this version, against a manifest ("" = any file is a conflict). Prints one line per file.
# Returns 0 = exactly the manifest's files with its digests, 5 = some of them, with its digests, 4 = none,
# 1 = anything else (a digest differs, a file the manifest lacks, or the version is there and no manifest was given),
# 2 = the JSON could not be read.
#   $1 manifest path or ""
rf_pypi_state() {
  local args=(--json "$RF_JSON" --package "$PKG" --version "$VERSION")
  [[ -z "${1:-}" ]] || args+=(--manifest "$1")
  rf_py guard pypi-state "${args[@]}"
}

# A fresh release starts from the pushed tip of main: a stale local main is the failure this script
# cannot undo after an upload. behind = the remote has commits this release would omit; diverged = the
# release commit could not fast-forward; ahead = commits CI never saw.
rf_require_synced_main() {
  local local_tip remote_tip base
  local_tip="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  remote_tip="$(git -C "$REPO_ROOT" rev-parse "$RF_MAIN")"
  base="$(git -C "$REPO_ROOT" merge-base HEAD "$RF_MAIN")"
  [[ "$local_tip" != "$remote_tip" ]] || return 0
  if [[ "$local_tip" == "$base" ]]; then
    err "the local branch is BEHIND ${RELEASE_REMOTE}/${RELEASE_BRANCH} ($(git -C "$REPO_ROOT" rev-list --count "HEAD..${RF_MAIN}") commit(s)) — run 'git pull --ff-only' and re-release"
  elif [[ "$remote_tip" != "$base" ]]; then
    err "the local branch has DIVERGED from ${RELEASE_REMOTE}/${RELEASE_BRANCH} — reconcile the two before releasing"
  fi
  err "the local branch has $(git -C "$REPO_ROOT" rev-list --count "${RF_MAIN}..HEAD") commit(s) CI has not seen — push, wait for CI to go green, then release"
}

# A release whose commit exists continues from it: pushed to the remote's main already, or the one commit
# the local branch is ahead by. The manifest on disk must be the one committed there.
rf_require_resumable() {
  if ! git -C "$REPO_ROOT" merge-base --is-ancestor "$RELEASE_SHA" "$RF_MAIN"; then
    [[ "$(git -C "$REPO_ROOT" rev-parse HEAD)" == "$RELEASE_SHA" ]] && rf_on_release_branch \
      && [[ "$(git -C "$REPO_ROOT" rev-list --count "${RF_MAIN}..HEAD")" == "1" ]] \
      || err "the release commit ${RELEASE_SHA:0:8} of ${VERSION} is not on ${RELEASE_REMOTE}/${RELEASE_BRANCH}, and it is not the only commit the branch tracking it is ahead by"
    git -C "$REPO_ROOT" merge-base --is-ancestor "$RF_MAIN" HEAD \
      || err "the local branch has DIVERGED from ${RELEASE_REMOTE}/${RELEASE_BRANCH} — reconcile the two before continuing the release"
  fi
  git -C "$REPO_ROOT" diff --quiet "$RELEASE_SHA" -- "$(basename "$MANIFEST")" \
    || err "$(basename "$MANIFEST") differs from the one committed at ${RELEASE_SHA:0:8}"
}

# Push the release commit to the remote's main (the pre-push gate runs). Nothing to do when the remote has it.
rf_push_release() {
  if git -C "$REPO_ROOT" merge-base --is-ancestor "$RELEASE_SHA" "$RF_MAIN"; then
    echo "The release commit ${RELEASE_SHA:0:8} is already on ${RELEASE_REMOTE}/${RELEASE_BRANCH}."
    return 0
  fi
  echo "Pushing the release commit ${RELEASE_SHA:0:8} to ${RELEASE_REMOTE}/${RELEASE_BRANCH} (the pre-push gate runs)..."
  git -C "$REPO_ROOT" push "$RELEASE_REMOTE" "${RELEASE_SHA}:refs/heads/${RELEASE_BRANCH}" \
    || rf_stop "the push of the release commit was refused — fix what the hook reported above"
}

# One deadline for every wait of this run, set when the first wait starts.
rf_set_deadline() {
  [[ -n "$RF_DEADLINE" ]] || RF_DEADLINE=$(( $(date +%s) + 10#${RELEASE_WAIT_TIMEOUT_S} ))
}

# Wait until every push CI run of main on the release commit is done; red, no run and the deadline stop.
rf_wait_ci() {
  local rc=0
  rf_set_deadline
  rf_py ci_green --repo "$GH_REPO" --sha "$RELEASE_SHA" --branch "$RELEASE_BRANCH" \
    --wait --deadline "$RF_DEADLINE" --interval "$RELEASE_POLL_INTERVAL_S" || rc=$?
  case "$rc" in
    0) ;;
    3) rf_stop "CI on the release commit did not finish before the deadline (RELEASE_WAIT_TIMEOUT_S=${RELEASE_WAIT_TIMEOUT_S})" ;;
    2) rf_stop "GitHub could not be asked for the CI runs of the release commit" ;;
    *) rf_stop "CI is not green on the release commit ${RELEASE_SHA:0:8}" ;;
  esac
}

# Tag the release commit and push the tag. A pushed tag is a fact: it is never moved or deleted, so a
# tag on the remote that is not at the release commit is an error, not something to repair here.
rf_tag() {
  local remote_sha local_sha listed
  listed="$(rf_net git -C "$REPO_ROOT" ls-remote --tags "$RELEASE_REMOTE" "refs/tags/${TAG}" "refs/tags/${TAG}^{}")" \
    || rf_stop "could not ask ${RELEASE_REMOTE} whether it has the tag ${TAG}"
  remote_sha="$(awk 'END { print $1 }' <<<"$listed")"
  if [[ -n "$remote_sha" ]]; then
    [[ "$remote_sha" == "$RELEASE_SHA" ]] \
      || err "tag ${TAG} is on ${RELEASE_REMOTE} at ${remote_sha:0:8}, not at the release commit ${RELEASE_SHA:0:8} — a pushed tag is never moved"
    echo "Tag ${TAG} is already on ${RELEASE_REMOTE} at the release commit."
    return 0
  fi
  if local_sha="$(git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/${TAG}^{commit}")"; then
    [[ "$local_sha" == "$RELEASE_SHA" ]] \
      || err "a local tag ${TAG} points at ${local_sha:0:8}, not at the release commit ${RELEASE_SHA:0:8} — delete that local tag yourself, this script never moves a tag"
  else
    git -C "$REPO_ROOT" tag -a "$TAG" "$RELEASE_SHA" -m "🤖 chore(release): release ${PKG} ${VERSION} to PyPI" \
      || err "git tag ${TAG} failed"
  fi
  git -C "$REPO_ROOT" push "$RELEASE_REMOTE" "refs/tags/${TAG}" || rf_stop "the push of tag ${TAG} was refused"
  echo "Pushed tag ${TAG}."
}

# Wait for the release.yml run of the release commit (rebuild at the manifest's epoch, digest check,
# cosign signature, SLSA provenance), then require a bundle on the GitHub release for every artefact.
rf_wait_signing() {
  local rc=0
  rf_set_deadline
  rf_py guard wait-release --repo "$GH_REPO" --sha "$RELEASE_SHA" --tag "$TAG" \
    --root "$REPO_ROOT" --deadline "$RF_DEADLINE" --interval "$RELEASE_POLL_INTERVAL_S" || rc=$?
  case "$rc" in
    0) ;;
    3) rf_stop "the signing run did not finish before the deadline (RELEASE_WAIT_TIMEOUT_S=${RELEASE_WAIT_TIMEOUT_S})" ;;
    2) rf_stop "GitHub could not be asked for the signing run of ${TAG}" ;;
    *) rf_stop "the release of ${TAG} is not signed (see above)" ;;
  esac
}

# The files to publish: the manifest's artefacts, downloaded from the GitHub release of the tag (the very files
# release.yml signed) into $RF_TMP/signed, and hashed against the manifest. The guard checks this directory, and the
# workflow downloads the same files; nothing built on this machine after the signing run is published. A name outside
# [A-Za-z0-9._+-] (first character a letter or digit) is refused: it could be a glob or an option.
rf_fetch_signed() {
  local dir="${RF_TMP}/signed" names name args=()
  local safe='^[A-Za-z0-9][A-Za-z0-9._+-]*$'
  names="$(rf_py guard artefacts --root "$REPO_ROOT")" \
    || err "could not list the artefacts of $(basename "$MANIFEST")"
  rm -rf "$dir"
  mkdir -m 700 "$dir" || err "could not create ${dir}"
  while IFS= read -r name; do
    [[ "$name" =~ $safe ]] || err "the manifest names a file that gh would read as a glob or an option: '${name}'"
    args+=(--pattern "$name")
  done <<<"$names"
  timeout 600 gh release download "$TAG" --repo "$GH_REPO" --dir "$dir" "${args[@]}" \
    || err "could not download the files of the release ${TAG} from ${GH_REPO}"
  rf_py digests verify --manifest "$MANIFEST" --dist "$dir" \
    || err "the files of the GitHub release ${TAG} are not the manifest's bytes (see above)"
}

# Immediately before the dispatch: read PyPI again. The first read is hours old when CI and the signing run were
# slow, and a publication since then (a run of this script that died after its dispatch) must be seen, not raced.
# 4 and 5 continue (nothing, or some of the manifest's files); 0 is a finished release (nothing to dispatch);
# 1 is a version PyPI holds with other bytes, which no release can use again.
rf_recheck_pypi() {
  local state=0
  rf_load_pypi
  rf_integrity
  rf_pypi_state "$MANIFEST" || state=$?
  case "$state" in
    4|5) ;;
    0)
      rf_verify_pypi
      echo "Release complete: ${PKG} ${VERSION} is on PyPI with the manifest's digests; nothing to dispatch."
      exit 0
      ;;
    1) err "${PKG} ${VERSION} is on PyPI with other bytes than the manifest's (see above) — that version is spent; release the next one and list this one in release-superseded.json" ;;
    *) err "could not tell what PyPI holds for ${VERSION} (the JSON of ${PYPI_JSON_URL} was unreadable) — nothing was dispatched" ;;
  esac
}

# THE ONLY PUBLICATION STEP, and it uploads nothing itself: it asks CI to. The guard proves the five conditions of the
# invariant (scripts.release.guard) on the signed files first, and a guard that refuses, or cannot run, ends the script
# before the workflow is dispatched. The workflow checks the same conditions again, then PyPI is given the files by the
# job in the environment pypi, after its reviewers approve, with an OIDC identity and no stored credential.
publish_release() {
  rf_py guard check --repo "$GH_REPO" --sha "$RELEASE_SHA" --tag "$TAG" \
    --root "$REPO_ROOT" --remote "$RELEASE_REMOTE" --dist "${RF_TMP}/signed" \
    || rf_stop "the release guard refused the publication (the unmet conditions are listed above)"
  rf_dispatch_publish
}

# Start the publish workflow on the tag, unless it is already running or finished a moment ago.
# live: a run of this tag is not completed (a re-run after a Ctrl-C) — wait for it, never dispatch a second.
# recent: a green run younger than RF_PYPI_LAG_S — PyPI's read may lag it, so go straight to polling PyPI, for what
#         is left of RF_PYPI_LAG_S since that run ended (publish-state prints its age).
# none: dispatch it, find its run, wait. A double dispatch would be harmless (one at a time; the same bytes), but is avoided.
rf_dispatch_publish() {
  local out kind run_id url age rc=0
  out="$(rf_py guard publish-state --repo "$GH_REPO" --sha "$RELEASE_SHA" --tag "$TAG" --lag "$RF_PYPI_LAG_S")" \
    || rf_stop "could not read the ${PUBLISH_WORKFLOW} runs of ${TAG} (see above)"
  IFS=$'\t' read -r kind run_id url age <<<"$out"
  case "$kind" in
    live)
      echo "A ${PUBLISH_WORKFLOW} run of ${TAG} is live (${url}) — waiting for it, no second dispatch."
      rf_wait_publish_run "$run_id" "$url"
      ;;
    recent)
      echo "A ${PUBLISH_WORKFLOW} run of ${TAG} succeeded a moment ago (${url}) — no dispatch, PyPI is read until it lists the files."
      # the wait for PyPI counts from the end of that run, not from now
      [[ "$age" =~ ^[0-9]+$ ]] || rf_stop "the age of the ${PUBLISH_WORKFLOW} run ${url} could not be read (got '${age}')"
      RF_GREEN_AT=$(( $(date +%s) - 10#${age} ))
      ;;
    none)
      rf_require_no_branch_of_tag
      echo "Dispatching ${PUBLISH_WORKFLOW} on ${TAG} (it runs in the environment ${PUBLISH_ENVIRONMENT}, whose reviewers must approve)..."
      out="$(rf_py guard publish-dispatch --repo "$GH_REPO" --sha "$RELEASE_SHA" --tag "$TAG")" || rc=$?
      [[ "$rc" == "0" ]] \
        || rf_stop_dispatched "the ${PUBLISH_WORKFLOW} dispatch of ${TAG} failed or its run was not seen in time (see above)"
      IFS=$'\t' read -r run_id url <<<"$out"
      echo "Dispatched: ${url}"
      rf_wait_publish_run "$run_id" "$url"
      ;;
    *) rf_stop "the ${PUBLISH_WORKFLOW} runs of ${TAG} could not be told apart (got '${kind}')" ;;
  esac
}

# Wait for one run of the publish workflow (it may wait for a reviewer of the environment: that counts against the
# deadline too). Success starts the clock of the PyPI lag.
#   $1 run id   $2 run url
rf_wait_publish_run() {
  local rc=0
  rf_set_deadline
  rf_py guard publish-wait --repo "$GH_REPO" --sha "$RELEASE_SHA" --tag "$TAG" --run "$1" \
    --deadline "$RF_DEADLINE" --interval "$RELEASE_POLL_INTERVAL_S" || rc=$?
  case "$rc" in
    0) RF_GREEN_AT="$(date +%s)" ;;
    3) rf_stop_dispatched "the ${PUBLISH_WORKFLOW} run did not finish before the deadline (RELEASE_WAIT_TIMEOUT_S=${RELEASE_WAIT_TIMEOUT_S}): ${2} — approve the deployment of environment ${PUBLISH_ENVIRONMENT} if it waits" ;;
    2) rf_stop_dispatched "GitHub could not be asked for the ${PUBLISH_WORKFLOW} run ${2}" ;;
    *) rf_stop_dispatched "the ${PUBLISH_WORKFLOW} run ${2} did not succeed (see above)" ;;
  esac
}

# Read PyPI until it lists every file of the manifest with its digest. PyPI purges on an upload but a read can lag, so
# absence is not final before RF_PYPI_LAG_S seconds have passed since the green run; that limit is its own clock,
# not the deadline of the waits. A read that fails (a 5xx, a timeout, a page that is not the JSON) is a read that listed
# nothing: it is tried again, and at the limit it ends like absence, with the command that resumes. A file with other
# bytes (state 1) ends it at once: the version is spent.
rf_wait_pypi() {
  local state out limit
  limit=$(( ${RF_GREEN_AT:-$(date +%s)} + RF_PYPI_LAG_S ))
  while :; do
    state=4
    if rf_try_load_pypi; then
      state=0
      out="$(rf_pypi_state "$MANIFEST")" || state=$?
    else
      echo "WARNING: could not read ${PYPI_JSON_URL} (PyPI or the network is not answering)." >&2
    fi
    case "$state" in
      0) echo "PyPI lists every file of ${VERSION} with the manifest's digests."; return 0 ;;
      1) printf '%s\n' "$out" >&2
         err "${PKG} ${VERSION} is on PyPI with other bytes than the manifest's (see above) — that version is spent; release the next one and list this one in release-superseded.json" ;;
      4|5) ;;
      *) echo "WARNING: the answer of PyPI could not be read as the project's JSON." >&2 ;;
    esac
    if (( $(date +%s) >= limit )); then
      rf_stop_dispatched "the publish run succeeded but PyPI does not list the files yet (${RF_PYPI_LAG_S} s since the green run)"
    fi
    echo "PyPI does not list all files of ${VERSION} yet — reading again in ${RELEASE_POLL_INTERVAL_S} s."
    sleep "$RELEASE_POLL_INTERVAL_S"
  done
}

# After the publication: download the version's files from PyPI's file host (https only, the URLs the guard takes from
# the JSON) and compare their sha256 with the manifest. Needs the JSON to be current and in state 0.
rf_verify_pypi() {
  local dir="${RF_TMP}/pypi-files" listed name url
  local safe='^[A-Za-z0-9][A-Za-z0-9._+-]*$'
  rm -rf "$dir"
  mkdir -m 700 "$dir" || err "could not create ${dir}"
  listed="$(rf_py guard pypi-urls --json "$RF_JSON" --package "$PKG" --version "$VERSION")" \
    || err "could not list the files of ${VERSION} on PyPI"
  [[ -n "$listed" ]] || err "PyPI lists no file of ${VERSION}"
  while IFS=$'\t' read -r name url; do
    [[ "$name" =~ $safe ]] || err "PyPI lists a file name that is not a plain file name: '${name}'"
    curl -sSLf --globoff --proto =https --proto-redir =https --connect-timeout 20 --max-time 600 -o "${dir}/${name}" "$url" \
      || rf_stop_dispatched "could not download ${name} from ${PYPI_FILES_HOST} (the network, or the file host, is not answering)"
  done <<<"$listed"
  rf_py digests verify --manifest "$MANIFEST" --dist "$dir" \
    || err "PyPI serves bytes that are not the manifest's (see above) — ${VERSION} is spent; release the next version"
}
