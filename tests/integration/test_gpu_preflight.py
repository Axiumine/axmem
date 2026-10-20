"""The GPU preflight of the GPU suites: it ends the pytest run, it never skips.

The refusal is exercised with a real pytest run in a subprocess whose environment has no GPU.
"""
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.repositories.embeddings.tensorrt_runtime import probe_gpu
from tests.gpu_preflight import require_gpu, worker_budget_problem

ROOT = Path(__file__).resolve().parents[2]
GIB = 1024**3


def test_a_machine_with_a_gpu_passes_the_preflight():
    require_gpu()  # returns normally


def test_one_worker_fits_on_this_card():
    assert worker_budget_problem(probe_gpu().total_bytes, 1) is None


def test_workers_that_fit_in_gpu_memory_are_allowed():
    assert worker_budget_problem(24 * GIB, 3) is None


def test_the_server_process_of_the_standalone_test_counts_against_the_budget():
    assert worker_budget_problem(24 * GIB, 4) is not None


def test_too_many_workers_are_refused_with_the_number_that_fits():
    problem = worker_budget_problem(24 * GIB, 12)

    assert "12 pytest-xdist workers" in problem
    assert "at most -n 3" in problem
    assert "standalone test starts 1 server process more" in problem


def test_a_card_too_small_for_one_engine_still_names_one_worker():
    assert "at most -n 1" in worker_budget_problem(1 * GIB, 2)


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"CUDA_VISIBLE_DEVICES": "-1"}, "GPU preflight failed (no-gpu)"),
        ({"PYTEST_XDIST_WORKER_COUNT": "1000"}, "GPU preflight failed (memory)"),
    ],
    ids=["no-gpu", "too-many-workers"],
)
def test_a_failed_preflight_ends_the_pytest_run_without_skipping(tmp_path, environment, expected):
    (tmp_path / "conftest.py").write_text(textwrap.dedent(
        """
        from tests.gpu_preflight import require_gpu


        def pytest_collection_modifyitems(config, items):
            require_gpu()
        """,
    ))
    (tmp_path / "test_needs_gpu.py").write_text("def test_it():\n    assert True\n")

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", str(tmp_path), "-p", "no:cacheprovider", "-c", os.devnull,
         "--rootdir", str(tmp_path)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT), **environment},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 2
    assert expected in result.stdout + result.stderr
    assert "no tests ran" in result.stdout  # the run ended; the test was neither run nor skipped
    assert not re.search(r"\d+ (skipped|passed)", result.stdout)
