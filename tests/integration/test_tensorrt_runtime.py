# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The TensorRT runner on the real GPU: CUDA helpers, GPU probe, engine execution and failure reporting.

Nothing here is a stub. The engine is built from the real model on the NVIDIA GPU of the machine;
failures are produced by real inputs (shapes outside the profile, damaged plans, invalid models).
"""
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from cuda.bindings import driver
from cuda.bindings import runtime as cudart

from app.config.settings import settings
from app.exceptions import (
    EmbeddingUnavailableError,
    ModelUnavailableError,
    TensorRTError,
    TensorRTStartupError,
    TensorRTUnavailableError,
)
from app.repositories.embeddings import tensorrt_runtime
from app.repositories.embeddings.tensorrt_model import fetch_model
from app.repositories.embeddings.tensorrt_reranker import PROFILE
from app.repositories.embeddings.tensorrt_runtime import (
    GpuMemory,
    OptimizationProfile,
    PlanRejectedError,
    ProfileError,
    TensorRTEngine,
    _Slot,
    build_plan,
    cuda_check,
    cuda_context_handle,
    device_failure,
    gpu_memory,
    probe_gpu,
)

INPUTS = ("input_ids", "attention_mask", "token_type_ids")


def make_inputs(batch, sequence):
    ids = np.ones((batch, sequence), dtype=np.int64)
    return {"input_ids": ids, "attention_mask": ids.copy(), "token_type_ids": np.zeros_like(ids)}


@pytest.fixture(scope="module")
def plan_bytes(tensorrt_reranker):
    """The serialized plan of the session engine (the engine cache holds it next to its fingerprint)."""
    return (tensorrt_reranker.startup.engine_dir / "engine.plan").read_bytes()


@pytest.fixture(scope="module")
def engine(plan_bytes):
    engine = TensorRTEngine(plan_bytes, PROFILE, contexts=1)
    yield engine
    engine.close()


def test_cuda_check_unpacks_each_kind_of_result():
    assert cuda_check(cudart.cudaSetDevice(0)) is None
    pointer = cuda_check(cudart.cudaMalloc(16))
    assert isinstance(pointer, int)
    assert cuda_check(cudart.cudaFree(pointer)) is None
    free, total = cuda_check(cudart.cudaMemGetInfo())
    assert 0 < free <= total


def test_cuda_check_raises_on_a_cuda_error_and_clears_it():
    with pytest.raises(TensorRTError, match="cudaErrorInvalidDevice"):
        cuda_check(cudart.cudaSetDevice(10_000))

    assert cudart.cudaPeekAtLastError()[0] == cudart.cudaError_t.cudaSuccess


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (cudart.cudaError_t.cudaErrorNoDevice, "no-gpu"),
        (cudart.cudaError_t.cudaErrorInvalidDevice, "no-gpu"),
        (cudart.cudaError_t.cudaErrorInsufficientDriver, "driver"),
        (cudart.cudaError_t.cudaErrorSystemDriverMismatch, "driver"),
        (cudart.cudaError_t.cudaErrorInvalidValue, "gpu-error"),
    ],
)
def test_device_failure_names_the_cause(error, cause):
    failure = device_failure(error)

    assert isinstance(failure, TensorRTStartupError)
    assert failure.cause == cause
    assert error.name in str(failure)


def test_probe_gpu_describes_the_device():
    gpu = probe_gpu()

    assert gpu.name
    assert re.fullmatch(r"\d+\.\d+", gpu.compute_capability)
    assert re.fullmatch(r"\d+(\.\d+)*", gpu.driver_version)
    assert gpu.driver_major == int(gpu.driver_version.split(".")[0]) > 0
    assert gpu.total_bytes > 1024**3


class _BrokenNvml:
    """The slice of ``cuda.bindings.nvml`` that ``probe_gpu`` uses, with one call that fails."""

    def __init__(self, failing_call, error):
        self.failing_call, self.error = failing_call, error
        self.shut_down = False

    def init_v2(self):
        if self.failing_call == "init_v2":
            raise self.error

    def system_get_driver_version(self):
        raise self.error

    def shutdown(self):
        self.shut_down = True


@pytest.mark.parametrize(("failing_call", "error"), [
    ("init_v2", RuntimeError("Unable to load libnvidia-ml.so.1")),
    ("init_v2", OSError("libnvidia-ml.so.1: cannot open shared object file")),
    ("system_get_driver_version", tensorrt_runtime.nvml.NvmlError(1)),
])
def test_probe_gpu_reports_an_nvml_that_cannot_be_used_as_a_startup_error(monkeypatch, failing_call, error):
    nvml = _BrokenNvml(failing_call, error)
    monkeypatch.setattr(tensorrt_runtime, "nvml", nvml)

    with pytest.raises(TensorRTStartupError, match="NVML cannot report the NVIDIA driver version") as raised:
        probe_gpu()

    assert raised.value.cause == "gpu-error"
    assert "libnvidia-ml.so.1" in str(raised.value)
    assert nvml.shut_down is (failing_call != "init_v2")  # an NVML that was opened is closed again


class _FailingCuda:
    """The CUDA runtime module of ``cuda.bindings`` with one call that answers with an error."""

    def __init__(self, call, error):
        self.call, self.error = call, error

    def __getattr__(self, name):
        if name == self.call:
            return lambda *args: (self.error,)
        return getattr(cudart, name)


@pytest.mark.parametrize("call", ["cudaGetDeviceProperties", "cudaDeviceGetAttribute", "cudaMemGetInfo"])
@pytest.mark.parametrize(("error", "cause"), [
    (cudart.cudaError_t.cudaErrorNoDevice, "no-gpu"),
    (cudart.cudaError_t.cudaErrorInsufficientDriver, "driver"),
    (cudart.cudaError_t.cudaErrorUnknown, "gpu-error"),
])
def test_probe_gpu_reports_a_cuda_failure_after_the_device_count_as_a_startup_error(monkeypatch, call, error, cause):
    """The start-up handlers and the test preflight catch ``TensorRTStartupError`` only."""
    monkeypatch.setattr(tensorrt_runtime, "cudart", _FailingCuda(call, error))

    with pytest.raises(TensorRTStartupError) as raised:
        probe_gpu()

    assert raised.value.cause == cause
    assert error.name in str(raised.value)


def test_gpu_memory_still_answers_when_nvml_cannot_say(monkeypatch):
    monkeypatch.setattr(tensorrt_runtime, "nvml", _BrokenNvml("init_v2", RuntimeError("no library")))

    assert gpu_memory().process_bytes is None


def _field(number, wire_type, payload):
    return _varint(number << 3 | wire_type) + payload


def _varint(value):
    out = b""
    while value > 0x7F:
        out += bytes([value & 0x7F | 0x80])
        value >>= 7
    return out + bytes([value])


def _text(number, text):
    return _field(number, 2, _varint(len(text)) + text.encode())


def _message(number, body):
    return _field(number, 2, _varint(len(body)) + body)


def identity_model(element_type):
    """A real ONNX file with one Identity node on a tensor of the given ONNX element type (1 float, 10 float16)."""
    def tensor(name):
        shape = _message(2, _message(1, _text(2, "batch")) + _message(1, _text(2, "sequence")))
        kind = _message(1, _field(1, 0, _varint(element_type)) + shape)
        return _text(1, name) + _message(2, kind)

    node = _text(1, "x") + _text(2, "y") + _text(4, "Identity")
    graph = _message(1, node) + _text(2, "graph") + _message(11, tensor("x")) + _message(12, tensor("y"))
    return _field(1, 0, _varint(8)) + _message(7, graph) + _message(8, _field(2, 0, _varint(17)))


@pytest.mark.parametrize(("element_type", "dtype"), [(10, "HALF"), (16, "BF16"), (3, "INT8")])
def test_build_refuses_a_model_that_is_not_fp32(tmp_path, element_type, dtype):
    """The network keeps the types of the file, so a half-precision or quantised model would build a lower-precision engine."""
    path = tmp_path / "model.onnx"
    path.write_bytes(identity_model(element_type))
    profile = OptimizationProfile(min_shape=(1, 4), opt_shape=(1, 4), max_shape=(1, 4))

    with pytest.raises(TensorRTStartupError, match=rf"not fp32 or integer.*x: {dtype}") as raised:
        build_plan(path, profile, tf32=False)

    assert raised.value.cause == "build-failed"


def test_build_accepts_an_fp32_model_of_the_same_shape(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(identity_model(1))
    profile = OptimizationProfile(min_shape=(1, 4), opt_shape=(1, 4), max_shape=(1, 4))

    assert len(build_plan(path, profile, tf32=False)) > 0


def test_engine_reads_the_input_names_of_the_model(engine):
    assert sorted(engine.input_names) == sorted(INPUTS)


@pytest.mark.parametrize(
    "shape", [PROFILE.min_shape, PROFILE.opt_shape, PROFILE.max_shape, (7, 33), (1, 512), (64, 2)],
)
def test_infer_accepts_every_shape_inside_the_profile(engine, shape):
    logits = engine.infer(make_inputs(*shape))

    assert logits.shape == (shape[0], 1)
    assert logits.dtype == np.float32
    assert np.isfinite(logits).all()


@pytest.mark.parametrize("shape", [(65, 16), (1, 1), (2, 513), (200, 8)])
def test_infer_rejects_shapes_outside_the_profile(engine, shape):
    with pytest.raises(ProfileError, match="does not satisfy any optimization profiles"):
        engine.infer(make_inputs(*shape))

    assert engine.infer(make_inputs(3, 10)).shape == (3, 1)  # the engine is still usable


def test_infer_rejects_wrong_input_names(engine):
    with pytest.raises(TensorRTError, match="takes the inputs"):
        engine.infer({"input_ids": np.ones((1, 4), dtype=np.int64)})


def test_infer_converts_the_dtype_of_the_inputs(engine):
    exact = engine.infer(make_inputs(2, 8))
    as_floats = engine.infer({name: array.astype(np.float64) for name, array in make_inputs(2, 8).items()})

    assert np.array_equal(exact, as_floats)


def test_slot_reports_a_refused_launch(plan_bytes):
    fresh = TensorRTEngine(plan_bytes, PROFILE)
    try:
        with fresh._borrowed() as slot, pytest.raises(TensorRTError, match="could not run the engine"):
            slot._enqueue()  # no device addresses are bound on a slot that has not run yet
    finally:
        fresh.close()


def test_slot_reports_an_engine_that_cannot_create_an_execution_context():
    """TensorRT returns no context when it cannot reserve the memory; an engine double stands in for that."""
    class OutOfMemoryEngine:
        @staticmethod
        def create_execution_context():
            return None

    with pytest.raises(TensorRTStartupError, match="cannot create an execution context") as raised:
        _Slot(OutOfMemoryEngine(), list(INPUTS), "logits", PROFILE)

    assert raised.value.cause == "engine-unusable"


def test_slot_reports_a_refused_binding(engine):
    with engine._borrowed() as slot, pytest.raises(TensorRTError, match="device address of tensor 'nope'"):
        slot._bind("nope", 0)


def test_concurrent_calls_share_one_slot_without_corrupting_results(engine):
    inputs = make_inputs(8, 40)
    expected = engine.infer(inputs)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: engine.infer(inputs), range(24)))

    assert all(np.array_equal(result, expected) for result in results)


def test_plan_rejected_for_bytes_that_are_not_a_plan():
    with pytest.raises(PlanRejectedError) as raised:
        TensorRTEngine(b"not a plan", PROFILE)

    assert raised.value.cause == "engine-unusable"


def test_plan_rejected_for_a_truncated_plan(plan_bytes):
    with pytest.raises(PlanRejectedError):
        TensorRTEngine(plan_bytes[: len(plan_bytes) // 2], PROFILE)


def test_failed_cuda_state_is_reported_then_refuses_all_work(plan_bytes):
    engine = TensorRTEngine(plan_bytes, PROFILE)
    engine.verify()  # healthy engine: nothing recorded

    cudart.cudaMemcpy(0, 0, 4, cudart.cudaMemcpyKind.cudaMemcpyHostToDevice)  # a CUDA call that fails
    with pytest.raises(TensorRTUnavailableError, match="cudaErrorInvalidValue"):
        engine.verify()

    for call in (engine.verify, lambda: engine.infer(make_inputs(1, 4))):
        with pytest.raises(TensorRTUnavailableError, match="unusable after an earlier failure") as raised:
            call()
        assert isinstance(raised.value, ModelUnavailableError)  # what a re-embedding does not ask again about
        assert not isinstance(raised.value, EmbeddingUnavailableError)  # the reranker's engine raises it too
    engine.close()


def test_closed_engine_refuses_work_and_closing_twice_is_harmless(plan_bytes):
    engine = TensorRTEngine(plan_bytes, PROFILE, contexts=2)
    assert engine.infer(make_inputs(1, 4)).shape == (1, 1)

    engine.close()
    engine.close()

    for call in (lambda: engine.infer(make_inputs(1, 4)), engine.verify):
        with pytest.raises(TensorRTUnavailableError, match="closed") as raised:
            call()
        assert isinstance(raised.value, ModelUnavailableError)


def test_build_rejects_a_file_that_is_not_onnx(tmp_path):
    broken = tmp_path / "model.onnx"
    broken.write_text("this is not an ONNX model")

    with pytest.raises(TensorRTStartupError, match=r"cannot parse .* as an ONNX model") as raised:
        build_plan(broken, PROFILE, tf32=False)

    assert raised.value.cause == "build-failed"


@pytest.fixture(scope="module")
def model_onnx(tensorrt_reranker):
    return fetch_model(settings.RERANKING_MODEL, settings.RERANKING_MODEL_DIR).onnx


def test_build_rejects_an_invalid_profile(model_onnx):
    inverted = OptimizationProfile(min_shape=(10, 2), opt_shape=(5, 100), max_shape=(64, 512))

    with pytest.raises(TensorRTStartupError, match="rejected the optimisation profile") as raised:
        build_plan(model_onnx, inverted, tf32=False)

    assert raised.value.cause == "build-failed"


def test_build_reports_the_error_tensorrt_logged_when_no_engine_can_be_made(model_onnx):
    beyond_the_model = OptimizationProfile(min_shape=(1, 2), opt_shape=(1, 4096), max_shape=(1, 4096))

    with pytest.raises(TensorRTStartupError, match=r"could not build an engine.*4096 != 512") as raised:
        build_plan(model_onnx, beyond_the_model, tf32=False)

    assert raised.value.cause == "build-failed"


def test_every_thread_runs_on_the_same_cuda_context():
    with ThreadPoolExecutor(max_workers=3) as pool:
        handles = {pool.submit(cuda_context_handle).result() for _ in range(6)}

    assert handles == {cuda_context_handle()}
    assert 0 not in handles


def test_a_driver_error_while_reading_the_context_is_reported(monkeypatch):
    monkeypatch.setattr(driver, "cuCtxGetCurrent", lambda: (driver.CUresult.CUDA_ERROR_INVALID_CONTEXT, None))

    with pytest.raises(TensorRTError, match="CUDA could not report the current context: CUDA_ERROR_INVALID_CONTEXT"):
        cuda_context_handle()


def test_gpu_memory_reads_what_the_device_has_in_use():
    memory = gpu_memory()

    assert 0 < memory.used_bytes <= memory.total_bytes
    assert memory.total_bytes == probe_gpu().total_bytes
    assert memory.process_bytes is None or 0 < memory.process_bytes <= memory.used_bytes


@pytest.mark.parametrize(
    ("process_bytes", "held"),
    [(3 * 1024**3, "this process holds 3.00 GiB"), (None, "this process holds unknown (NVML cannot report it)")],
)
def test_gpu_memory_describes_itself_in_gibibytes(process_bytes, held):
    memory = GpuMemory(process_bytes=process_bytes, used_bytes=4 * 1024**3, total_bytes=24 * 1024**3)

    assert memory.describe() == f"{held}; the device has 4.00 of 24 GiB in use"


def test_a_failed_call_notes_the_cuda_error_so_that_the_health_route_can_report_it(plan_bytes):
    """A sticky error fails every call at the call itself, where ``verify`` is never reached."""
    engine = TensorRTEngine(plan_bytes, PROFILE)
    assert engine.failure == ""

    cudart.cudaMemcpy(0, 0, 4, cudart.cudaMemcpyKind.cudaMemcpyHostToDevice)  # a CUDA call that fails
    with pytest.raises(TensorRTUnavailableError, match="cudaErrorInvalidValue") as raised:
        engine.infer(make_inputs(65, 16))  # a call that fails, outside the profile

    assert isinstance(raised.value, ModelUnavailableError)  # the call that met the error is already refused for good
    assert isinstance(raised.value.__cause__.__context__, ProfileError)  # and the failure of the call stays in the chain
    assert "cudaErrorInvalidValue" in engine.failure
    with pytest.raises(TensorRTUnavailableError, match="unusable after an earlier failure"):
        engine.infer(make_inputs(1, 4))
    engine.close()


def test_a_failed_call_without_a_cuda_error_leaves_the_engine_usable(engine):
    with pytest.raises(ProfileError):
        engine.infer(make_inputs(65, 16))

    assert engine.failure == ""
    assert engine.infer(make_inputs(1, 4)).shape == (1, 1)
