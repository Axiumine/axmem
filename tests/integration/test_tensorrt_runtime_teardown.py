"""How the release of an engine and of a slot reports the steps that failed (the real GPU, with the failing CUDA calls scripted)."""
import logging

import pytest
from cuda.bindings import runtime as cudart

from app.exceptions import TensorRTError
from app.repositories.embeddings import tensorrt_runtime
from app.repositories.embeddings.tensorrt_reranker import PROFILE
from app.repositories.embeddings.tensorrt_runtime import TensorRTEngine
from tests.tensorrt_runtime_support import LOGGER, SUCCESS, ScriptedCuda


@pytest.fixture(scope="module")
def plan_bytes(tensorrt_reranker):
    return (tensorrt_reranker.startup.engine_dir / "engine.plan").read_bytes()


# ---- the teardown --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failures", "line"),
    [
        (
            [("destroying the stream", "CUDA call failed: A"), ("freeing the buffer of x", "CUDA call failed: B")],
            (
                "the teardown: 2 release step(s) failed and every other step ran (destroying the stream, freeing the buffer "
                "of x); what they name stays allocated until the process exits. Reason: CUDA call failed: A; CUDA call failed: B"
            ),
        ),
        (
            [("step one", "same"), ("step two", "same"), ("step three", "other")],
            (
                "the teardown: 3 release step(s) failed and every other step ran (step one, step two, step three); "
                "what they name stays allocated until the process exits. Reason: same; other"
            ),
        ),
    ],
)
def test_the_steps_of_a_teardown_that_failed_are_logged_in_one_line_with_each_reason_once(caplog, failures, line):
    tensorrt_runtime._log_release_failures("the teardown", failures)

    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == LOGGER] == [(logging.ERROR, line)]


def test_a_teardown_in_which_every_step_worked_logs_nothing(caplog):
    tensorrt_runtime._log_release_failures("the teardown", [])

    assert [r for r in caplog.records if r.name == LOGGER] == []


def test_a_release_step_records_its_failure_and_never_raises():
    failures = []
    freed = []

    def free(handle):
        freed.append(handle)
        return (SUCCESS,) if handle == 1 else (cudart.cudaError_t.cudaErrorInvalidValue,)

    tensorrt_runtime._release_step(failures, "first", free, 1)
    tensorrt_runtime._release_step(failures, "second", free, 2)
    tensorrt_runtime._release_step(failures, "third", lambda handle: 1 / 0, 3)

    assert freed == [1, 2]
    assert failures == [
        ("second", "CUDA call failed: cudaErrorInvalidValue (invalid argument)"),
        ("third", "division by zero"),
    ]


ILLEGAL_ADDRESS = cudart.cudaError_t.cudaErrorIllegalAddress
ILLEGAL_ADDRESS_TEXT = "CUDA call failed: cudaErrorIllegalAddress (an illegal memory access was encountered)"
STEPS_RAN = "release step(s) failed and every other step ran"
STAYS_ALLOCATED = "what they name stays allocated until the process exits"


def performed_then_failed(name):
    """A scripted answer for the CUDA call ``name``: the real call does its work, then it reports a sticky error."""
    real = getattr(cudart, name)

    def call(*args):
        real(*args)
        return (ILLEGAL_ADDRESS,)

    return call


def test_closing_an_engine_whose_release_steps_all_fail_logs_one_line_naming_each_step_in_order(monkeypatch, caplog, plan_bytes):
    engine = TensorRTEngine(plan_bytes, PROFILE)
    with engine._borrowed() as slot:
        buffers = [*engine.input_names, slot.output]
    cuda = ScriptedCuda(cudaStreamDestroy=performed_then_failed("cudaStreamDestroy"), cudaFree=performed_then_failed("cudaFree"))
    monkeypatch.setattr(tensorrt_runtime, "cudart", cuda)

    engine.close()

    steps = ", ".join(["destroying the stream", *(f"freeing the buffer of {name}" for name in buffers)])
    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == LOGGER] == [
        (
            logging.ERROR,
            f"The TensorRT engine was closed: 5 {STEPS_RAN} ({steps}); {STAYS_ALLOCATED}. Reason: {ILLEGAL_ADDRESS_TEXT}",
        ),
    ]
    assert len(cuda.requests) == 5  # the stream and the four buffers were each asked for once
    assert engine._engine is None
    assert engine._runtime is None


def test_a_slot_that_cannot_be_completed_logs_the_steps_of_its_own_release_that_failed(monkeypatch, caplog, plan_bytes):
    mallocs = []

    def third_one_fails(size):
        mallocs.append(size)
        return (cudart.cudaError_t.cudaErrorMemoryAllocation,) if len(mallocs) == 3 else cudart.cudaMalloc(size)

    cuda = ScriptedCuda(cudaMalloc=third_one_fails, cudaStreamDestroy=performed_then_failed("cudaStreamDestroy"))
    monkeypatch.setattr(tensorrt_runtime, "cudart", cuda)

    with pytest.raises(TensorRTError) as raised:
        TensorRTEngine(plan_bytes, PROFILE)

    assert str(raised.value) == "CUDA call failed: cudaErrorMemoryAllocation (out of memory)"
    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == LOGGER] == [
        (
            logging.ERROR,
            (
                f"A TensorRT execution slot failed half-way and was released: 1 {STEPS_RAN} (destroying the stream); "
                f"{STAYS_ALLOCATED}. Reason: {ILLEGAL_ADDRESS_TEXT}"
            ),
        ),
    ]
