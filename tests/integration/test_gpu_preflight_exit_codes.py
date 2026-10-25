"""The exit codes of a failed GPU preflight: 2 for everybody, 1 inside a mutmut worker.

A failed preflight ends the pytest run, and mutmut books exit code 2 as "check was interrupted by user", which no test
turns into "killed". A mutant of ``probe_gpu`` that makes the preflight fail would survive for ever, so a mutmut worker
(it sets ``MUTANT_UNDER_TEST``) gets 1, the code of a failed test. ``test_gpu_preflight.py`` proves the documented 2 with
real pytest runs; these tests call ``require_gpu`` in this process and read the code of the exception that ends the run.
"""
import os

import pytest

from app.exceptions import TensorRTStartupError
from app.repositories.embeddings import tensorrt_runtime
from app.repositories.embeddings.tensorrt_runtime import GpuInfo
from tests.gpu_preflight import exit_code, require_gpu

CARD = GpuInfo(name="NVIDIA Test GPU", compute_capability="8.6", driver_version="580.65.06", total_bytes=24 * 1024**3)


def no_gpu():
    raise TensorRTStartupError("no-gpu", "no NVIDIA GPU is visible to CUDA (cudaErrorNoDevice).")


def a_card():
    return CARD


def ended_by(preflight):
    """The exception with which the preflight ended the run."""
    with pytest.raises(pytest.exit.Exception) as ended:
        preflight()
    return ended.value


@pytest.fixture
def outside_mutmut(monkeypatch):
    monkeypatch.delenv("MUTANT_UNDER_TEST", raising=False)


@pytest.fixture
def inside_mutmut(monkeypatch):
    monkeypatch.setenv("MUTANT_UNDER_TEST", os.environ.get("MUTANT_UNDER_TEST", ""))  # the name of the mutant, kept


@pytest.mark.usefixtures("outside_mutmut")
def test_a_run_that_mutmut_does_not_drive_ends_with_the_documented_code_2():
    assert exit_code() == 2


@pytest.mark.usefixtures("inside_mutmut")
def test_a_mutmut_worker_ends_with_the_code_of_a_failed_test():
    assert exit_code() == 1


@pytest.mark.usefixtures("outside_mutmut")
def test_a_missing_gpu_ends_the_run_with_2_and_the_cause(monkeypatch):
    monkeypatch.setattr(tensorrt_runtime, "probe_gpu", no_gpu)

    ended = ended_by(require_gpu)

    assert ended.returncode == 2
    assert ended.msg.startswith("GPU preflight failed (no-gpu): no NVIDIA GPU is visible to CUDA (cudaErrorNoDevice).")


@pytest.mark.usefixtures("inside_mutmut")
def test_a_missing_gpu_ends_a_mutmut_worker_with_1(monkeypatch):
    monkeypatch.setattr(tensorrt_runtime, "probe_gpu", no_gpu)

    assert ended_by(require_gpu).returncode == 1


@pytest.mark.usefixtures("outside_mutmut")
def test_too_many_workers_for_the_card_end_the_run_with_2_and_the_cause(monkeypatch):
    monkeypatch.setattr(tensorrt_runtime, "probe_gpu", a_card)
    monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", "1000")

    ended = ended_by(require_gpu)

    assert ended.returncode == 2
    assert ended.msg.startswith("GPU preflight failed (memory): 1000 pytest-xdist workers")


@pytest.mark.usefixtures("inside_mutmut")
def test_too_many_workers_for_the_card_end_a_mutmut_worker_with_1(monkeypatch):
    monkeypatch.setattr(tensorrt_runtime, "probe_gpu", a_card)
    monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", "1000")

    assert ended_by(require_gpu).returncode == 1


@pytest.mark.parametrize("fixture", ["outside_mutmut", "inside_mutmut"])
def test_a_card_that_fits_the_workers_passes_in_both_modes(request, monkeypatch, fixture):
    request.getfixturevalue(fixture)
    monkeypatch.setattr(tensorrt_runtime, "probe_gpu", a_card)
    monkeypatch.delenv("PYTEST_XDIST_WORKER_COUNT", raising=False)

    require_gpu()  # returns normally
