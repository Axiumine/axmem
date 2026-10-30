# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The CI workflows run on hosted runners only, and only the checks that need neither a GPU nor Docker.

The suites that run the real TensorRT models and a real Qdrant (tests, end-to-end, cluster, coverage, memory budgets,
Qodana, mutation) run on the developer machine in the pre-push Git hook; ``tests/integration/test_quality_runner.py``
and ``tests/integration/test_pre_push_hook.py`` prove that side. These tests keep the GPU and Docker work from coming
back into a workflow.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.quality import registry, security

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOWS_DIR.glob("*.yml"))
GATE_COMMAND = re.compile(r"scripts\.quality\s+gate\s+([\w-]+)")
# What a workflow may ask `scripts.quality` for: the gates that run on any Linux machine, none of them on a GPU or Docker.
HOSTED_GATES = {
    "ruff", "imports", "bandit", "semgrep", "shellcheck", "actionlint", "reuse", "vulture", "radon-cc", "radon-mi", "mypy",
    "pylint", "architecture", "trivy", "package", "wheel",
}
# Words of a step that only a GPU or a Docker host can act on.
GPU_OR_DOCKER_WORDS = re.compile(
    r"docker|nvidia-smi|tests/(integration|e2e|cluster)|\bpytest\b.*\s-m\s+(cluster|memory|snapshot)|--memray|mutmut|MUTATION|qodana",
    re.IGNORECASE,
)
JOBS_OF = {
    "ci.yml": {"lint", "smoke-test"},
    "quality.yml": {"static", "trivy"},
    "publish.yml": {"verify", "publish"},
    "publish-testpypi.yml": {"verify", "publish"},
    "release.yml": {"sign-artefacts", "provenance-artefacts"},
    "scorecard.yml": {"analysis"},
    "codeql.yml": {"analyze"},
}


def _load(path):
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["triggers"] = document.get("on", document.get(True))  # YAML reads a bare `on` as the boolean True
    return document


def _jobs():
    return [(path.name, name, job) for path in WORKFLOWS for name, job in _load(path)["jobs"].items()]


def _steps():
    return [(workflow, name, step) for workflow, name, job in _jobs() for step in job.get("steps", [])]


def _runs_on_a_branch_or_a_pull_request(path):
    triggers = _load(path)["triggers"]
    return "pull_request" in triggers or "branches" in (triggers.get("push") or {})


# The workflows that check the code of a branch or of a pull request; release.yml runs on a release tag only.
CHECKED = [path for path in WORKFLOWS if _runs_on_a_branch_or_a_pull_request(path)]
# A job of a reusable workflow (`uses`) has no runner and no steps of its own.
RUNNING = [(workflow, name, job) for workflow, name, job in _jobs() if "uses" not in job]
JOB_IDS = [f"{workflow}:{name}" for workflow, name, _ in _jobs()]
RUNNING_IDS = [f"{workflow}:{name}" for workflow, name, _ in RUNNING]
STEP_IDS = [f"{workflow}:{name}:{number}" for number, (workflow, name, _) in enumerate(_steps())]
INSTALLS = [(workflow, name, step) for workflow, name, step in _steps() if "uv sync" in step.get("run", "")]
INSTALL_IDS = [f"{workflow}:{name}" for workflow, name, _ in INSTALLS]


def _gates_named():
    return {gate for _, _, step in _steps() for gate in GATE_COMMAND.findall(step.get("run", ""))}


def test_the_workflows_and_their_jobs_are_the_hosted_ones():
    assert {path.name for path in WORKFLOWS} == set(JOBS_OF)
    assert {path.name for path in CHECKED} == {"ci.yml", "quality.yml", "scorecard.yml", "codeql.yml"}
    for path in WORKFLOWS:
        assert set(_load(path)["jobs"]) == JOBS_OF[path.name], path.name


def test_the_jobs_of_the_removed_gpu_workflow_and_suites_are_gone():
    names = {name for _, name, _ in _jobs()}

    assert not (WORKFLOWS_DIR / "e2e.yml").exists()
    assert names.isdisjoint({"test", "e2e", "snapshots", "coverage", "memory", "qodana", "mutation"})


@pytest.mark.parametrize(("workflow", "name", "job"), RUNNING, ids=RUNNING_IDS)
def test_every_job_runs_on_a_hosted_runner(workflow, name, job):
    runner = job["runs-on"]

    assert isinstance(runner, str), f"{workflow} job {name} names its runner with a list, a mapping or a matrix"
    assert re.fullmatch(r"ubuntu-[\w.-]+", runner), f"{workflow} job {name} runs on {runner!r}, not a hosted Ubuntu runner"


@pytest.mark.parametrize(("workflow", "name", "job"), _jobs(), ids=JOB_IDS)
def test_no_job_starts_a_container_or_a_service(workflow, name, job):
    assert not {"container", "services"} & set(job), f"{workflow} job {name} starts a container"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_no_workflow_runs_on_pull_request_target(workflow):
    assert "pull_request_target" not in _load(workflow)["triggers"]


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_no_workflow_is_scheduled(workflow):
    assert "schedule" not in _load(workflow)["triggers"]


@pytest.mark.parametrize(("workflow", "name", "step"), _steps(), ids=STEP_IDS)
def test_no_step_needs_a_gpu_or_docker(workflow, name, step):
    text = f"{step.get('name', '')} {step.get('uses', '')} {step.get('run', '')}"

    assert not GPU_OR_DOCKER_WORDS.search(text), f"{workflow} job {name}: {text.strip()!r} needs a GPU or Docker"


def test_the_workflows_ask_only_for_gates_that_need_neither_a_gpu_nor_docker():
    named = _gates_named()

    assert named <= HOSTED_GATES
    assert not named & (set(registry.GATES) - HOSTED_GATES)


def test_every_hosted_gate_runs_in_a_workflow_and_in_the_ci_stage():
    assert _gates_named() == HOSTED_GATES
    assert {gate for group in registry.STAGES["ci"] for gate in group} == HOSTED_GATES


def test_the_hooks_and_hosted_ci_run_the_same_gates():
    """No check lives in a workflow alone: the pre-push hook starts with the `ci` stage, so a developer machine runs every
    gate that hosted CI runs (by name), and a hosted gate that no hook reaches fails here."""
    ci = registry.STAGES["ci"]
    pre_push = registry.STAGES["pre-push"]
    hooked = {gate for stage in ("pre-commit", "pre-push") for group in registry.STAGES[stage] for gate in group}

    assert pre_push[: len(ci)] == ci
    assert hooked >= HOSTED_GATES
    assert set(registry.GATES) - HOSTED_GATES <= hooked  # the rest needs a GPU, Docker or a clone


def test_no_workflow_step_runs_a_check_by_another_name_than_its_gate():
    """A raw `ruff`, `pytest` or `twine` command in a workflow would be a check that the hooks do not run."""
    raw = re.compile(r"\b(?:ruff|pytest|twine|check-wheel-contents|shellcheck|actionlint|lint-imports|mypy|pylint|bandit)\b")
    for workflow, name, step in _steps():
        if workflow in {"ci.yml", "quality.yml"}:
            commands = [line for line in step.get("run", "").splitlines() if not line.lstrip().startswith("#")]
            assert not [line for line in commands if raw.search(line) and "scripts.quality" not in line], (workflow, name)


@pytest.mark.parametrize(("workflow", "name", "step"), INSTALLS, ids=INSTALL_IDS)
def test_the_environment_is_installed_without_the_tensorrt_libraries(workflow, name, step):
    command = step["run"]

    assert "--locked" in command
    assert "--no-install-package tensorrt-cu13-libs" in command  # 3.8 GB that no hosted check loads


def test_the_installs_are_found():
    assert INSTALL_IDS == ["ci.yml:lint", "ci.yml:smoke-test", "quality.yml:static"]


@pytest.mark.parametrize("workflow", CHECKED, ids=lambda path: path.name)
def test_a_newer_run_cancels_the_older_run_of_the_same_workflow_and_ref(workflow):
    concurrency = _load(workflow)["concurrency"]

    assert concurrency["cancel-in-progress"] is True
    assert "github.workflow" in concurrency["group"]
    assert "github.ref" in concurrency["group"]


@pytest.mark.parametrize("workflow", CHECKED, ids=lambda path: path.name)
def test_code_from_a_pull_request_gets_a_read_only_token_and_no_secret(workflow):
    document = _load(workflow)
    checkouts = [
        step for job in document["jobs"].values() for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout")
    ]

    assert document["permissions"] == {"contents": "read"}
    assert "secrets." not in workflow.read_text(encoding="utf-8")
    assert checkouts
    assert all(step["with"]["persist-credentials"] is False for step in checkouts)


@pytest.mark.parametrize("workflow", CHECKED, ids=lambda path: path.name)
def test_no_job_keeps_a_fork_guard_for_a_runner_that_is_gone(workflow):
    for name, job in _load(workflow)["jobs"].items():
        assert "head.repo.full_name" not in str(job.get("if", "")), f"{workflow} job {name} guards a runner it does not use"


def test_the_smoke_test_waits_for_lint_and_builds_nothing_itself():
    job = _load(WORKFLOWS_DIR / "ci.yml")["jobs"]["smoke-test"]
    script = "\n".join(step.get("run", "") for step in job["steps"])

    assert job["needs"] == ["lint"]
    assert "uvx" not in script
    assert "uv build" not in script  # the package and wheel gates build into temporary directories, never dist/
    assert "uv pip" not in script  # the wheel gate installs and resolves in an environment of its own
    assert script.count("scripts.quality gate") == 2


def _step_names(workflow, job):
    return [step.get("name") for step in _load(WORKFLOWS_DIR / workflow)["jobs"][job]["steps"]]


def _step(workflow, job, name):
    (step,) = [step for step in _load(WORKFLOWS_DIR / workflow)["jobs"][job]["steps"] if step.get("name") == name]
    return step


def test_the_smoke_test_runs_the_package_and_wheel_gates_once_the_environment_that_they_run_in_is_installed():
    names = _step_names("ci.yml", "smoke-test")

    assert names.index("Install dependencies") < names.index("Check what the package holds")
    assert names.index("Check what the package holds") < names.index("Check that the wheel installs, starts and resolves")
    assert _step("ci.yml", "smoke-test", "Check what the package holds")["run"] == (
        "uv run --frozen --no-sync python -m scripts.quality gate package"
    )
    assert _step("ci.yml", "smoke-test", "Check that the wheel installs, starts and resolves")["run"] == (
        "uv run --frozen --no-sync python -m scripts.quality gate wheel"
    )


def test_the_lint_job_runs_the_architecture_and_ruff_gates_by_name():
    assert _step("ci.yml", "lint", "Check architecture boundaries, rule proofs and workflow rules")["run"] == (
        "uv run --frozen --no-sync python -m scripts.quality gate architecture"
    )
    assert _step("ci.yml", "lint", "Run linting")["run"] == "uv run --frozen --no-sync python -m scripts.quality gate ruff"


def test_the_static_job_runs_the_shell_and_workflow_linters_from_the_pinned_environment():
    for name in ("shellcheck", "actionlint"):
        assert _step("quality.yml", "static", name)["run"] == f"uv run --frozen python -m scripts.quality gate {name}"


def test_the_static_job_runs_the_reuse_gate_from_the_pinned_environment():
    """The copyright and license criteria of the Best Practices badge: a file without a notice fails a hosted run too."""
    names = _step_names("quality.yml", "static")
    (step,) = [step for step in _load(WORKFLOWS_DIR / "quality.yml")["jobs"]["static"]["steps"] if "gate reuse" in step.get("run", "")]

    assert step["run"] == "uv run --frozen python -m scripts.quality gate reuse"
    assert names.index("actionlint") < names.index(step["name"]) < names.index("vulture")


def test_the_publish_workflow_proves_the_files_it_uploads_before_the_job_that_uploads_them_and_uploads_last():
    names = _step_names("publish.yml", "verify")
    order = [
        "Download the signed release files",
        "Verify the digests against the manifest",
        "Verify the signatures against release.yml at the tag",
        "Run the release guard on the tag commit",
        "Check what the package holds",
        "Summarise what will be published",
    ]

    assert [names.index(name) for name in order] == sorted(names.index(name) for name in order)
    assert _step_names("publish.yml", "publish")[-1] == "Publish to PyPI"
    assert _load(WORKFLOWS_DIR / "publish.yml")["jobs"]["publish"]["needs"] == "verify"
    assert _step("publish.yml", "verify", "Check what the package holds")["run"] == (
        "python3 -m scripts.quality dist-check dist"
    )


def test_the_trivy_job_installs_the_version_that_the_gate_pins_and_hands_the_gate_its_binary():
    job = _load(WORKFLOWS_DIR / "quality.yml")["jobs"]["trivy"]
    script = "\n".join(step.get("run", "") for step in job["steps"])

    assert job["env"]["TRIVY_VERSION"] == security.TRIVY_VERSION
    assert re.fullmatch(r"[0-9a-f]{64}", job["env"]["TRIVY_SHA256"])
    assert "sha256sum --check" in script
    assert 'TRIVY_BIN=$RUNNER_TEMP/trivy/trivy" >> "$GITHUB_ENV"' in script  # without it the gate runs a Docker image


def test_trivy_skips_the_mutation_copy_of_the_project_that_git_ignores():
    """mutmut copies the project, uv.lock included, into ``mutants/``; its stale pins must not fail a scan of the real tree."""
    skipped = yaml.safe_load((WORKFLOWS_DIR.parents[1] / "trivy.yaml").read_text(encoding="utf-8"))["scan"]["skip-dirs"]
    ignored = (WORKFLOWS_DIR.parents[1] / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "mutants" in skipped
    assert "mutants/" in ignored  # the directory is generated and untracked, so skipping it hides nothing that is committed


def test_the_trivy_job_can_import_the_gates_because_it_installs_no_dependency():
    """The job runs ``python -m scripts.quality`` on a bare setup-python: a third-party import at module level breaks it."""
    steps = _load(WORKFLOWS_DIR / "quality.yml")["jobs"]["trivy"]["steps"]
    assert not any("uv " in step.get("run", "") or "pip install" in step.get("run", "") for step in steps)

    result = subprocess.run(  # nosec B603 - the interpreter of this run, fixed argv
        [sys.executable, "-S", "-c", "import scripts.quality.cli"],  # -S: no site-packages, as on the runner
        cwd=WORKFLOWS_DIR.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


# What the Scorecard job may use: api.scorecard.dev refuses to publish the result of a job with any other action.
SCORECARD_ACTIONS = {
    "actions/checkout",
    "actions/upload-artifact",
    "github/codeql-action/upload-sarif",
    "ossf/scorecard-action",
}


def _scorecard():
    return _load(WORKFLOWS_DIR / "scorecard.yml")


# https://github.com/ossf/scorecard-action#workflow-restrictions: a workflow that breaks one of them still runs, but the API
# refuses its result, and the badge in README.md reads that result. Three tests hold them: the workflow, its job, its steps.
def test_the_scorecard_workflow_has_no_env_no_defaults_and_no_write_permission_at_the_top():
    document = _scorecard()

    assert not {"env", "defaults"} & set(document)
    assert "write" not in document["permissions"].values()
    assert "id-token" not in document["permissions"]
    assert {name for name, other in document["jobs"].items() if "id-token" in other.get("permissions", {})} == {"analysis"}


def test_the_scorecard_job_signs_its_result_on_a_hosted_runner_without_env_or_defaults():
    job = _scorecard()["jobs"]["analysis"]

    assert not {"env", "defaults"} & set(job)
    assert job["permissions"]["id-token"] == "write"
    assert re.fullmatch(r"ubuntu-[\w.-]+", job["runs-on"])


def test_the_scorecard_job_has_only_the_steps_of_the_actions_that_the_api_accepts_and_publishes_the_result():
    steps = _scorecard()["jobs"]["analysis"]["steps"]
    scorecard = [step for step in steps if step["uses"].startswith("ossf/scorecard-action@")]

    assert all("uses" in step and "run" not in step for step in steps)
    assert {step["uses"].split("@")[0] for step in steps} <= SCORECARD_ACTIONS
    assert len(scorecard) == 1
    assert scorecard[0]["with"]["publish_results"] is True


def test_the_codeql_workflow_analyzes_languages_and_uploads_with_the_write_permission_in_its_job_only():
    """Scorecard's SAST check looks in the workflows for `github/codeql-action/analyze`; init and analyze are the pair."""
    document = _load(WORKFLOWS_DIR / "codeql.yml")
    job = document["jobs"]["analyze"]
    uses = [step["uses"].split("@")[0] for step in job["steps"] if "uses" in step]

    assert {"github/codeql-action/init", "github/codeql-action/analyze"} <= set(uses)
    assert "security-events" not in document["permissions"]
    assert job["permissions"]["security-events"] == "write"
    assert job["strategy"]["matrix"]["language"]


PINNED_ACTION = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")


@pytest.mark.parametrize(("workflow", "name", "step"), [row for row in _steps() if "uses" in row[2]], ids=lambda row: str(row)[:40])
def test_every_action_of_every_workflow_is_a_commit_not_a_tag_that_can_move(workflow, name, step):
    """The green status of ci.yml and quality.yml is a condition of the release guard, and a tag run restores their caches."""
    assert PINNED_ACTION.fullmatch(step["uses"]), f"{workflow}: {step['uses']} is not pinned to a 40-hex commit"
