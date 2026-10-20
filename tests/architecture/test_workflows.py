"""The CI workflows keep fork code off the self-hosted GPU runner and keep the weekly run alive."""

from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOWS_DIR.glob("*.yml"))
FORK_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"


def _load(path):
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["triggers"] = document.get("on", document.get(True))  # YAML reads a bare `on` as the boolean True
    return document


def _self_hosted_jobs():
    return [
        (path.name, name, job)
        for path in WORKFLOWS
        for name, job in _load(path)["jobs"].items()
        if "self-hosted" in job.get("runs-on", [])
    ]


def test_the_gpu_runner_jobs_are_found():
    names = {(workflow, name) for workflow, name, _ in _self_hosted_jobs()}

    assert {
        ("ci.yml", "test"), ("e2e.yml", "e2e"), ("quality.yml", "snapshots"), ("quality.yml", "coverage"),
        ("quality.yml", "memory"), ("quality.yml", "mutation"),
    } <= names


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_no_workflow_runs_on_pull_request_target(workflow):
    assert "pull_request_target" not in _load(workflow)["triggers"]


@pytest.mark.parametrize(
    ("workflow", "name", "job"), _self_hosted_jobs(), ids=[f"{workflow}:{name}" for workflow, name, _ in _self_hosted_jobs()],
)
def test_a_pull_request_from_a_fork_never_reaches_the_self_hosted_runner(workflow, name, job):
    condition = str(job.get("if", ""))
    only_schedule_and_dispatch = "schedule" in condition and "pull_request" not in condition

    assert FORK_GUARD in condition or only_schedule_and_dispatch, f"{workflow} job {name} runs fork code on the GPU host"


@pytest.mark.parametrize("workflow", [path for path in WORKFLOWS if "schedule" in _load(path)["triggers"]], ids=lambda path: path.name)
def test_the_scheduled_run_does_not_share_a_concurrency_group_with_pushes(workflow):
    concurrency = _load(workflow)["concurrency"]

    assert concurrency["cancel-in-progress"] is True
    assert "github.event_name" in concurrency["group"]


def test_the_wheel_smoke_test_takes_the_tensorrt_pins_from_the_lock_not_from_pypi():
    commands = [step.get("run", "") for step in _load(WORKFLOWS_DIR / "ci.yml")["jobs"]["smoke-test"]["steps"]]
    script = "\n".join(commands)

    assert "uvx" not in script  # resolves the wheel's own metadata, where the TensorRT packages have no index
    assert "uv sync --locked --no-dev --no-install-project --no-install-package tensorrt-cu13-libs" in script
    assert "uv pip install --no-deps" in script
