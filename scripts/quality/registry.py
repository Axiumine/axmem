"""Gate registry and stage pipelines (cheapest checks first, fail fast)."""

from __future__ import annotations

import os

from . import security, static, testing
from .core import GateFn

GATES: dict[str, tuple[GateFn, str]] = {
    "ruff": (static.gate_ruff, "ruff lint"),
    "imports": (static.gate_imports, "import-linter layer contracts"),
    "bandit": (static.gate_bandit, "bandit security lint"),
    "semgrep": (static.gate_semgrep, "semgrep custom (+registry) rules"),
    "vulture": (static.gate_vulture, "dead code"),
    "radon-cc": (static.gate_radon_cc, "cyclomatic complexity, no function below B"),
    "radon-mi": (static.gate_radon_mi, "maintainability index, all files grade A"),
    "mypy": (static.gate_mypy, "static typing"),
    "pylint": (static.gate_pylint, "pylint score >= 8/10"),
    "snapshots": (testing.gate_snapshots, "syrupy snapshot tests"),
    "tests-fast": (testing.gate_tests_fast, "architecture + integration tests"),
    "gitnexus": (security.gate_gitnexus, "GitNexus change/impact analysis"),
    "tests": (testing.gate_tests, "architecture + integration + e2e on a Qdrant node, with coverage"),
    "e2e-qdrant": (testing.gate_e2e_qdrant, "three-node Qdrant cluster e2e (docker) with coverage"),
    "coverage": (testing.gate_coverage, "100% line+branch coverage"),
    "memray": (testing.gate_memray, "memory budgets (pytest-memray)"),
    "trivy": (security.gate_trivy, "trivy vuln/secret/misconfig scan"),
    "qodana": (security.gate_qodana, "Qodana Ultimate Plus (QODANA_TOKEN from .env)"),
    "mutation": (testing.gate_mutation, "mutmut mutation testing (MUTATION=1)"),
}

_STATIC = ["ruff", "imports", "bandit", "semgrep", "vulture", "radon-cc", "radon-mi"]

# What needs neither a GPU nor Docker: the static gates, trivy, and the type checks. Hosted CI runs exactly these (by
# name, split across jobs), and `stage ci` runs them in one go on a developer machine.
_HOSTED = [[*_STATIC, "trivy"], ["mypy", "pylint"]]

# Gates inside a group run in parallel; groups run in order and a failed group stops the stage.
STAGES: dict[str, list[list[str]]] = {
    "pre-commit": [
        _STATIC,
        ["mypy", "pylint"],
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

# Slow gates that only run when asked for (MUTATION=1 or ONLY=mutation).
OPT_IN = {"mutation"}


def selected(name: str) -> bool:
    skip = {s for s in os.environ.get("SKIP", "").replace(" ", "").split(",") if s}
    only = {s for s in os.environ.get("ONLY", "").replace(" ", "").split(",") if s}
    if name in skip:
        return False
    if only:
        return name in only
    if name == "mutation":
        return os.environ.get("MUTATION") == "1"
    return name not in OPT_IN
