# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Gate registry and stage pipelines (cheapest checks first, fail fast)."""

from __future__ import annotations

import os

from . import package, security, static, testing, wheel
from .core import GateFn

GATES: dict[str, tuple[GateFn, str]] = {
    "ruff": (static.gate_ruff, "ruff lint"),
    "imports": (static.gate_imports, "import-linter layer contracts"),
    "bandit": (static.gate_bandit, "bandit security lint"),
    "semgrep": (static.gate_semgrep, "semgrep custom (+registry) rules"),
    "shellcheck": (static.gate_shellcheck, "shellcheck over the shell scripts and the Git hooks"),
    "actionlint": (static.gate_actionlint, "actionlint over the workflows, with the pinned shellcheck on their run blocks"),
    "reuse": (
        static.gate_reuse,
        "reuse lint-file: a copyright notice and a license in every file (REUSE.toml for the ones that cannot carry one)",
    ),
    "vulture": (static.gate_vulture, "dead code"),
    "radon-cc": (static.gate_radon_cc, "cyclomatic complexity, no function below B"),
    "radon-mi": (static.gate_radon_mi, "maintainability index, all files grade A"),
    "mypy": (static.gate_mypy, "static typing"),
    "pylint": (static.gate_pylint, "pylint score >= 8/10"),
    "architecture": (testing.gate_architecture, "tests/architecture: import contracts, rule proofs, workflow rules"),
    "snapshots": (testing.gate_snapshots, "syrupy snapshot tests"),
    "tests-fast": (testing.gate_tests_fast, "integration tests"),
    "package": (package.gate_package, "wheel and sdist hold the source only: no model weights, engines or NVIDIA libraries"),
    "wheel": (wheel.gate_wheel, "the built wheel installs on the locked dependencies, starts and resolves"),
    "gitnexus": (security.gate_gitnexus, "GitNexus change/impact analysis"),
    "tests": (testing.gate_tests, "architecture + integration + e2e on a Qdrant node, with coverage"),
    "e2e-qdrant": (testing.gate_e2e_qdrant, "three-node Qdrant cluster e2e (docker) with coverage"),
    "coverage": (testing.gate_coverage, "100% line+branch coverage"),
    "memray": (testing.gate_memray, "memory budgets (pytest-memray)"),
    "trivy": (security.gate_trivy, "trivy vuln/secret/misconfig scan"),
    "qodana": (security.gate_qodana, "Qodana Ultimate Plus (QODANA_TOKEN from .env)"),
    "mutation": (testing.gate_mutation, "mutmut mutation testing of the app/ modules the push changes"),
}

_STATIC = ["ruff", "imports", "bandit", "semgrep", "vulture", "radon-cc", "radon-mi", "shellcheck", "actionlint", "reuse"]

# What needs neither a GPU nor Docker: the static gates, trivy, the type checks, the architecture tests and the package.
# Hosted CI runs exactly these (by name, split across jobs), and `stage ci` runs them in one go on a developer machine.
# The wheel gate needs the network (PyPI and NVIDIA's index) for the locked environment, so it is not in `pre-commit`.
_HOSTED = [[*_STATIC, "trivy"], ["mypy", "pylint", "architecture", "package", "wheel"]]

# Gates inside a group run in parallel; groups run in order and a failed group stops the stage.
STAGES: dict[str, list[list[str]]] = {
    "pre-commit": [
        _STATIC,
        ["mypy", "pylint", "architecture", "package"],
        ["snapshots", "tests-fast"],
        ["gitnexus"],
    ],
    # The one place that runs the GPU, Docker and Qdrant gates (tests, e2e-qdrant, coverage, memray, qodana, mutation):
    # hosted CI has no GPU and no Docker. The cheap gates come first, so a failure there saves the long suites.
    "pre-push": [
        *_HOSTED,
        ["tests"],
        ["e2e-qdrant"],
        ["coverage"],
        ["memray"],
        ["qodana"],
        ["mutation"],
        ["gitnexus"],
    ],
    "ci": _HOSTED,
}


def stage_gates(stage: str) -> str:
    """The gates of a stage, in order, for a message."""
    return ", ".join(name for group in STAGES[stage] for name in group)


def named(variable: str) -> set[str]:
    """The gate names in a comma-separated environment variable (SKIP or ONLY)."""
    return {s for s in os.environ.get(variable, "").replace(" ", "").split(",") if s}


def unknown_names() -> list[str]:
    """Names in SKIP or ONLY that are no gate: a typo there would leave a gate out, or select none, without a word."""
    return sorted((named("SKIP") | named("ONLY")) - set(GATES))


def left_out(name: str) -> str:
    """Why a gate is not selected (SKIP or ONLY), or an empty string when it runs."""
    only = named("ONLY")
    if name in named("SKIP"):
        return "left out by SKIP"
    if only:
        return "" if name in only else "left out by ONLY"
    return ""


def selected(name: str) -> bool:
    return not left_out(name)
