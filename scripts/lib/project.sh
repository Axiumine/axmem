# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

# scripts/lib/project.sh — the per-repository settings of the release scripts. Sourced, never executed.
# shellcheck shell=bash disable=SC2034 # every variable is read by the scripts that source this file
#
#   source "$(dirname "${BASH_SOURCE[0]}")/project.sh"

# ── Package identity ──────────────────────────────────────────────────────────
PKG="axmem"                       # dist name (PEP 503)

# ── Where a release goes ──────────────────────────────────────────────────────
# The git remote that holds the repository's `main`: releases are pushed, tagged and checked there.
RELEASE_REMOTE="${RELEASE_REMOTE:-axiumine}"
# The remote branch a release is pushed to; the local branch that releases run on is the one that tracks it.
RELEASE_BRANCH="main"
# Where a release is published: PyPI, by trusted publishing. No PyPI credential of any kind exists on this machine or in
# the repository: the upload is made by the publish workflow in CI, which PyPI recognises by its OIDC identity (workflow
# file and environment below), and this side only reads PyPI's public JSON and files. The release does use the
# maintainer's own GitHub login, which no script reads or stores: `gh auth login` (a fine-grained token on the repository,
# for the API, the CI runs, the release downloads and the dispatch) and the same login in git for the push of main and
# of the tag.
PYPI_HOST="pypi.org"                       # the JSON API of the project: https://<host>/pypi/<PKG>/json
PYPI_FILES_HOST="files.pythonhosted.org"   # the only host the files are downloaded from
PUBLISH_WORKFLOW="publish.yml"             # the workflow that uploads; dispatched on the release tag
PUBLISH_ENVIRONMENT="pypi"                 # the GitHub environment it runs in (required reviewers, tag rule v*.*.*)
