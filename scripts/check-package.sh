#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

set -euo pipefail

# Pre-publish validation for the Axmem package.
#
# Steps: build -> what the package holds -> the wheel gate's checks (twine, check-wheel-contents, clean-environment smoke test).
# Validation is local-only (no TestPyPI round-trip: the package goes to PyPI from the publish workflow, and
# release-pypi.sh drives the release).
#
# Env:
#   BUILD=0   skip the build step (validate an already-built dist/, e.g. when
#             called from release-pypi.sh after it has already run `uv build`)

BUILD="${BUILD:-1}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/lib/project.sh
source "$SCRIPT_DIR/lib/project.sh"
cd "$REPO_ROOT"

step() {
    echo ""
    echo "=== $1 ==="
}

# Step 1 — build
if [ "$BUILD" = "1" ]; then
    step "Step 1: uv build"
    rm -rf dist
    uv build
else
    step "Step 1: skipped (BUILD=0) — validating existing dist/"
fi

[ -n "$(ls -A dist 2>/dev/null)" ] || { echo "ERROR: dist/ is empty — nothing to check" >&2; exit 1; }

# Step 2 — what the package holds: the wheel and the sdist are Axmem's source and nothing else (no model weights,
# engines or NVIDIA libraries), and their license metadata is pyproject.toml's (the checker is standard library only)
step "Step 2: what the package holds (scripts.quality dist-check dist)"
uv run --no-project --python 3.14 python -m scripts.quality dist-check dist

# Step 3 — twine check, check-wheel-contents, and a clean environment that installs and uses the wheel: the checks of the
# wheel gate (scripts/quality/wheel.py), run on the files about to be released. It runs in the project environment,
# which holds twine and check-wheel-contents (the dev group); the environment of the check lives in a temp directory.
step "Step 3: wheel gate checks (scripts.quality wheel-check dist)"
uv run --frozen python -m scripts.quality wheel-check dist

echo ""
echo "All checks passed for ${PKG}. Safe to publish."
