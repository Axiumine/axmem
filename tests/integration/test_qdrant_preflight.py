# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The Qdrant preflight of the suites: it pulls the pinned image when it is missing, and it ends the run, never skips.

The preflight is exercised with real pytest runs in a subprocess, against a stand-in ``docker`` command that records
what it is asked and answers like the CLI would (no image yet, a pull that works or fails). The real daemon is not
touched, so the image the other suites run on is never removed.
"""
import os
import re
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.qdrant_support import IMAGE, PULL_TIMEOUT_SECONDS

ROOT = Path(__file__).resolve().parents[2]
FAKE_DOCKER = """\
#!/bin/sh
echo "$@" >> "$DOCKER_LOG"
case "$1" in
    image)
        [ -f "$DOCKER_STATE/pulled" ] && exit 0
        [ "$FAKE_IMAGE" = "local" ] && exit 0
        echo "Error response from daemon: No such image: $3" >&2
        exit 1
        ;;
    pull)
        [ "$FAKE_PULL" = "works" ] || { echo "toomanyrequests: rate limit reached" >&2; exit 1; }
        : > "$DOCKER_STATE/pulled"
        ;;
esac
"""


def _run_preflight(tmp_path, *, image, pull="works", docker=True, environment=None):
    """Run a pytest session whose conftest calls ``require_docker``; return the result and the docker calls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if docker:
        command = bin_dir / "docker"
        command.write_text(FAKE_DOCKER)
        command.chmod(command.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "conftest.py").write_text(textwrap.dedent(
        """
        from tests.qdrant_support import require_docker


        def pytest_collection_modifyitems(config, items):
            require_docker()
        """,
    ))
    (tmp_path / "test_needs_qdrant.py").write_text("def test_it():\n    assert True\n")
    log = tmp_path / "docker.log"
    log.touch()

    result = subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
        [sys.executable, "-m", "pytest", str(tmp_path), "-p", "no:cacheprovider", "-c", os.devnull,
         "--rootdir", str(tmp_path)],
        cwd=ROOT,
        env={
            "PATH": str(bin_dir), "PYTHONPATH": str(ROOT), "DOCKER_LOG": str(log), "DOCKER_STATE": str(tmp_path),
            "FAKE_IMAGE": image, "FAKE_PULL": pull, **(environment or {}),
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return result, log.read_text().splitlines()


def test_an_image_that_is_already_local_is_not_pulled(tmp_path):
    result, calls = _run_preflight(tmp_path, image="local")

    assert result.returncode == 0
    assert calls == [f"image inspect {IMAGE}"]


def test_a_missing_image_is_pulled_and_the_run_goes_on(tmp_path):
    result, calls = _run_preflight(tmp_path, image="absent")

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [f"image inspect {IMAGE}", f"pull {IMAGE}"]
    assert "1 passed" in result.stdout


def test_a_pull_that_fails_ends_the_run_with_the_cause_instead_of_skipping(tmp_path):
    result, calls = _run_preflight(tmp_path, image="absent", pull="fails")

    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert calls == [f"image inspect {IMAGE}", f"pull {IMAGE}"]
    assert "Qdrant preflight failed" in output
    assert "rate limit reached" in output
    assert "no tests ran" in result.stdout
    assert not re.search(r"\d+ (skipped|passed)", result.stdout)


def test_a_machine_without_docker_ends_the_run_with_the_cause_instead_of_skipping(tmp_path):
    result, calls = _run_preflight(tmp_path, image="absent", docker=False)

    assert result.returncode == 2
    assert calls == []
    assert "Qdrant preflight failed" in result.stdout + result.stderr
    assert not re.search(r"\d+ (skipped|passed)", result.stdout)


def test_a_server_that_already_runs_needs_no_docker_at_all(tmp_path):
    result, calls = _run_preflight(
        tmp_path, image="absent", pull="fails", environment={"AXMEM_TEST_QDRANT_URL": "http://127.0.0.1:6333"},
    )

    assert result.returncode == 0
    assert calls == []


def test_the_pull_may_take_as_long_as_a_slow_registry_needs():
    assert PULL_TIMEOUT_SECONDS >= 900


@pytest.mark.parametrize(
    ("path", "marker"),
    [
        ("tests/integration/test_version_resolution.py", []),
        ("tests/e2e/test_health_e2e.py", []),
        ("tests/cluster/test_cluster_failover.py", ["-m", "cluster"]),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_every_suite_of_the_pre_push_gates_ends_the_run_when_docker_is_missing(tmp_path, path, marker):
    empty = tmp_path / "no-commands"
    empty.mkdir()
    environment = {name: value for name, value in os.environ.items() if name != "AXMEM_TEST_QDRANT_URL"}

    result = subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
        [sys.executable, "-m", "pytest", path, "-p", "no:cacheprovider", *marker],
        cwd=ROOT,
        env={**environment, "PATH": str(empty)},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "Qdrant preflight failed" in result.stdout + result.stderr
    assert not re.search(r"\d+ (skipped|passed|failed)", result.stdout)
