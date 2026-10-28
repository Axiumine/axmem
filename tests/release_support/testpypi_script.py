"""What the tests of ``scripts/release-testpypi.sh`` share: a release that stopped where the rehearsal belongs, and the words it prints.

``make_released`` runs the real ``scripts/release-pypi.sh`` in a scratch world (``tests/release_support/world.py``) until it
waits for the approval of the environment ``pypi``: the release commit is pushed and green, the tag is pushed and the signing run is
green, ``publish.yml`` is dispatched and waits. That is the state in which the rehearsal is run, in a second terminal.
"""

import json
import subprocess
from collections.abc import Callable

from tests.release_support.world import PACKAGE, World

VERSION = "0.2.0"
TAG = f"v{VERSION}"
WHEEL = f"{PACKAGE}-{VERSION}-py3-none-any.whl"
SDIST = f"{PACKAGE}-{VERSION}.tar.gz"
PYPI_DISPATCH = f"gh workflow-run publish.yml tag={TAG}"
TESTPYPI_DISPATCH = f"gh workflow-run publish-testpypi.yml tag={TAG}"
RESUME = f"bash scripts/release-testpypi.sh {VERSION}"
FIRST_LINE = (
    f"TestPyPI rehearsal of {PACKAGE} {VERSION}: the signed files go to test.pypi.org through publish-testpypi.yml. "
    "Advisory, not a gate. PyPI is not touched."
)
LAST_LINE = "TestPyPI rehearsal complete: now approve environment pypi"
PYPI_RUN_URL = "https://github.example/run/publish-1000"
TESTPYPI_RUN_URL = "https://github.example/run/testpypi-2000"
WAIT = {"RELEASE_WAIT_TIMEOUT_S": "4", "RELEASE_POLL_INTERVAL_S": "0.2"}


def run_script(world: World, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    """The real rehearsal script with exactly these arguments."""
    return world.release_testpypi(*args, **env)


def held_by_testpypi(world: World) -> dict[str, str]:
    """The fake TestPyPI: file name to sha256."""
    return world.testpypi()


def out(done: object) -> str:
    """Everything a finished process printed, for the message of a failed assertion."""
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


def recorded(world: World) -> dict[str, str]:
    """The files of the release as its manifest records them: name to sha256."""
    return {entry["name"]: entry["sha256"] for entry in world.manifest()["artifacts"]}


def since(world: World, mark: int) -> list[str]:
    """The events the fakes logged after the first ``mark`` of them."""
    return world.events()[mark:]


def dispatches(world: World, workflow: str) -> list[str]:
    """The dispatches of one workflow that the fake ``gh`` accepted."""
    return [line for line in world.events() if line.startswith(f"gh workflow-run {workflow} ")]


def hosts(lines: list[str]) -> set[str]:
    """The hosts the fake ``curl`` was asked for in ``lines``."""
    return {line.split()[1].split("/")[2] for line in lines if line.startswith("curl ") and len(line.split()) > 1}


def make_released(world: World) -> World:
    """A release that stopped where the rehearsal belongs: signed, and waiting for the approval of the environment pypi."""
    world.configure(publish_runs=["waiting"])
    first = world.release(VERSION, **WAIT)
    assert first.returncode != 0
    assert world.dispatches() == [PYPI_DISPATCH], out(first)
    return world


def make_rehearse(released: World) -> Callable[..., subprocess.CompletedProcess[str]]:
    """``rehearse(*args, **env)``: the rehearsal script for ``VERSION`` unless arguments are given."""

    def call(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return run_script(released, *(args or (VERSION,)), **env)

    return call


def approve_testpypi(world: World) -> None:
    """The reviewer of the environment testpypi approves: the run that waits goes on to succeed (its next look)."""
    path = world.state / "testpypi-runs.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    for run in state["runs"]:
        run["script"] = ["in_progress", "success"]
        run["seen"] = 0
    path.write_text(json.dumps(state), encoding="utf-8")


def assert_pypi_untouched(world: World) -> None:
    """PyPI holds nothing, nothing was uploaded from this machine, and the release's own dispatch is the only one of ``publish.yml``."""
    assert world.pypi() == {}
    assert world.uv_publishes() == []
    assert world.uploads() == []
    assert dispatches(world, "publish.yml") == [PYPI_DISPATCH]
