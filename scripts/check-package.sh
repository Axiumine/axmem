#!/usr/bin/env bash
set -euo pipefail

# Pre-publish validation for the Axmem package.
#
# Steps: build -> what the package holds -> twine check -> check-wheel-contents -> clean-environment smoke test.
# Validation is local-only (no TestPyPI round-trip: the package goes to PyPI from the publish workflow, and
# release-pypi.sh drives the release).
#
# Dev tools (twine, check-wheel-contents) run on the fly with `uvx`, so no global install is required and the
# project environment (with the 3.8 GB of TensorRT libraries) is never synced for them.
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

# Step 3 — twine check (metadata / README rendering)
# twine >=7 is required: hatchling emits `Metadata-Version: 2.5`, which twine 6.x rejects outright as
# `InvalidDistribution: '2.5' is not a valid metadata version` — a false failure on a perfectly valid wheel.
step "Step 3: twine check dist/*"
uvx --from 'twine>=7' twine check dist/*

# Step 4 — check-wheel-contents
step "Step 4: check-wheel-contents dist/*.whl"
uvx check-wheel-contents ${WHEEL_IGNORE:+--ignore "$WHEEL_IGNORE"} dist/*.whl

# Step 5 — clean environment install + smoke test. The wheel names the TensorRT packages without an index, and on
# PyPI `tensorrt-cu13` and `tensorrt-cu13-libs` are placeholders that fetch the 3.8 GB of libraries when installed:
# the dependencies come from uv.lock instead (NVIDIA's index for the TensorRT packages), without the libraries,
# which `axmem --help` never loads. The wheel goes on top of them alone, as in the smoke-test job of ci.yml.
step "Step 5: clean environment install + smoke test"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export UV_PROJECT_ENVIRONMENT="$WORK/venv"
uv sync --locked --no-dev --no-install-project --no-install-package tensorrt-cu13-libs
unset UV_PROJECT_ENVIRONMENT
WHEEL="$(ls "$PWD"/dist/*.whl)"
uv pip install --python "$WORK/venv/bin/python" --no-deps "$WHEEL"
# run away from the checkout, so that the installed wheel is what gets imported, not app/ and main.py
cd "$WORK"
"$WORK/venv/bin/$CLI" --help >/dev/null
for mod in $IMPORT_MODS; do
    "$WORK/venv/bin/python" -c "import $mod"
done

echo ""
echo "All checks passed for ${PKG}. Safe to publish."
