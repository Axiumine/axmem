"""The CI workflows run on hosted runners only, and only the checks that need neither a GPU nor Docker.

The suites that run the real TensorRT models and a real Qdrant (tests, end-to-end, cluster, coverage, memory budgets,
Qodana, mutation) run on the developer machine in the pre-push Git hook; ``tests/integration/test_quality_runner.py``
and ``tests/integration/test_pre_push_hook.py`` prove that side. These tests keep the GPU and Docker work from coming
back into a workflow.
"""

import re
import tomllib
from pathlib import Path

import pytest
import yaml

from scripts.quality import registry, security

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOWS_DIR.glob("*.yml"))
GATE_COMMAND = re.compile(r"scripts\.quality\s+gate\s+([\w-]+)")
# What a workflow may ask `scripts.quality` for: the gates that run on any Linux machine, none of them on a GPU or Docker.
HOSTED_GATES = {
    "ruff", "imports", "bandit", "semgrep", "vulture", "radon-cc", "radon-mi", "mypy", "pylint", "trivy",
}
# Words of a step that only a GPU or a Docker host can act on.
GPU_OR_DOCKER_WORDS = re.compile(
    r"docker|nvidia-smi|tests/(integration|e2e|cluster)|\bpytest\b.*\s-m\s+(cluster|memory|snapshot)|--memray|mutmut|MUTATION|qodana",
    re.IGNORECASE,
)
JOBS_OF = {"ci.yml": {"lint", "smoke-test"}, "quality.yml": {"static", "trivy"}, "publish.yml": {"publish"}}


def _load(path):
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["triggers"] = document.get("on", document.get(True))  # YAML reads a bare `on` as the boolean True
    return document


def _jobs():
    return [(path.name, name, job) for path in WORKFLOWS for name, job in _load(path)["jobs"].items()]


def _steps():
    return [(workflow, name, step) for workflow, name, job in _jobs() for step in job["steps"]]


CHECKED = [path for path in WORKFLOWS if {"push", "pull_request"} & set(_load(path)["triggers"])]
JOB_IDS = [f"{workflow}:{name}" for workflow, name, _ in _jobs()]
STEP_IDS = [f"{workflow}:{name}:{number}" for number, (workflow, name, _) in enumerate(_steps())]
INSTALLS = [(workflow, name, step) for workflow, name, step in _steps() if "uv sync" in step.get("run", "")]
INSTALL_IDS = [f"{workflow}:{name}" for workflow, name, _ in INSTALLS]


def _gates_named():
    return {gate for _, _, step in _steps() for gate in GATE_COMMAND.findall(step.get("run", ""))}


def test_the_workflows_and_their_jobs_are_the_hosted_ones():
    assert {path.name for path in WORKFLOWS} == set(JOBS_OF)
    assert {path.name for path in CHECKED} == {"ci.yml", "quality.yml"}
    for path in WORKFLOWS:
        assert set(_load(path)["jobs"]) == JOBS_OF[path.name], path.name


def test_the_jobs_of_the_removed_gpu_workflow_and_suites_are_gone():
    names = {name for _, name, _ in _jobs()}

    assert not (WORKFLOWS_DIR / "e2e.yml").exists()
    assert names.isdisjoint({"test", "e2e", "snapshots", "coverage", "memory", "qodana", "mutation"})


@pytest.mark.parametrize(("workflow", "name", "job"), _jobs(), ids=JOB_IDS)
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


def test_the_wheel_smoke_test_waits_for_lint_and_takes_the_tensorrt_pins_from_the_lock_not_from_pypi():
    job = _load(WORKFLOWS_DIR / "ci.yml")["jobs"]["smoke-test"]
    script = "\n".join(step.get("run", "") for step in job["steps"])

    assert job["needs"] == ["lint"]
    assert "uvx" not in script  # resolves the wheel's own metadata, where the TensorRT packages have no index
    assert "uv sync --locked --no-dev --no-install-project --no-install-package tensorrt-cu13-libs" in script
    assert "uv pip install --no-deps" in script


def _smoke_test_step_and_pyproject():
    pyproject = tomllib.loads((WORKFLOWS_DIR.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    job = _load(WORKFLOWS_DIR / "ci.yml")["jobs"]["smoke-test"]
    (step,) = [step for step in job["steps"] if step.get("name") == "Resolve the wheel with the documented index"]
    return pyproject["tool"]["uv"], job["steps"], step


def test_the_smoke_test_resolves_the_wheel_with_the_index_that_pyproject_declares():
    uv, _, step = _smoke_test_step_and_pyproject()
    (index,) = [entry for entry in uv["index"] if entry["name"] == "nvidia"]

    assert f"--extra-index-url {index['url'].rstrip('/')}" in step["run"]
    assert "uv pip compile" in step["run"]


def test_the_smoke_test_expects_the_real_wheels_of_every_package_that_only_the_extra_index_serves():
    uv, _, step = _smoke_test_step_and_pyproject()

    assert all(f'"{name}"' in step["run"] for name in uv["sources"])
    assert "the libraries must resolve to a wheel" in step["run"]


def test_the_smoke_test_resolves_the_wheel_after_it_is_built():
    _, steps, step = _smoke_test_step_and_pyproject()
    names = [candidate.get("name") for candidate in steps]

    assert names.index(step["name"]) > names.index("Build wheel")


def test_the_trivy_job_installs_the_version_that_the_gate_pins_and_hands_the_gate_its_binary():
    job = _load(WORKFLOWS_DIR / "quality.yml")["jobs"]["trivy"]
    script = "\n".join(step.get("run", "") for step in job["steps"])

    assert job["env"]["TRIVY_VERSION"] == security.TRIVY_VERSION
    assert re.fullmatch(r"[0-9a-f]{64}", job["env"]["TRIVY_SHA256"])
    assert "sha256sum --check" in script
    assert 'TRIVY_BIN=$RUNNER_TEMP/trivy/trivy" >> "$GITHUB_ENV"' in script  # without it the gate runs a Docker image
