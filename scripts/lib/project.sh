# scripts/lib/project.sh — the per-repository settings of the release scripts. Sourced, never executed.
# shellcheck shell=bash disable=SC2034 # every variable is read by the scripts that source this file
#
#   source "$(dirname "${BASH_SOURCE[0]}")/project.sh"

# ── Package identity ──────────────────────────────────────────────────────────
PKG="axmem"                       # dist name (PEP 503)
CLI="axmem"                       # console-script entry point
# Modules the clean-environment smoke test imports. `main` is the console script's own module, which imports the
# application with the runtime dependencies alone.
IMPORT_MODS="main"

# ── Wheel-contents check ──────────────────────────────────────────────────────
# check-wheel-contents codes to ignore, comma separated.
# W005: the top-level package is called `app`, which the tool lists among the common names a wheel should not claim.
# W009: the wheel holds two top-level entries, the package `app/` and the module `main.py` that the console script
#       points at (`[tool.hatch.build.targets.wheel] packages`). Both are the project's layout, by design.
WHEEL_IGNORE="W005,W009"

# ── Where a release goes ──────────────────────────────────────────────────────
# The git remote that holds the repository's `main`: releases are pushed, tagged and checked there.
RELEASE_REMOTE="${RELEASE_REMOTE:-axiumine}"
# The remote branch a release is pushed to; the local branch that releases run on is the one that tracks it.
RELEASE_BRANCH="main"
# Where a release is published: PyPI, by trusted publishing. No credential of any kind exists on this machine or in the
# repository: the upload is made by the publish workflow in CI, which PyPI recognises by its OIDC identity (workflow
# file and environment below), and this side only reads PyPI's public JSON and files.
PYPI_HOST="pypi.org"                       # the JSON API of the project: https://<host>/pypi/<PKG>/json
PYPI_FILES_HOST="files.pythonhosted.org"   # the only host the files are downloaded from
PUBLISH_WORKFLOW="publish.yml"             # the workflow that uploads; dispatched on the release tag
PUBLISH_ENVIRONMENT="pypi"                 # the GitHub environment it runs in (required reviewers, tag rule v*.*.*)
