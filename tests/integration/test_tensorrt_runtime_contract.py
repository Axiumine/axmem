# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The CUDA calls of the TensorRT runner: how an error is read, explained and handed on, and how the first GPU is described.

The CUDA runtime module or NVML is replaced by ``ScriptedCuda`` or ``FakeNvml`` where a failure cannot be had from the GPU on demand.
"""
from types import SimpleNamespace

import pytest
from cuda.bindings import runtime as cudart

from app.exceptions import TensorRTError, TensorRTStartupError
from app.repositories.embeddings import tensorrt_runtime
from app.repositories.embeddings.tensorrt_runtime import (
    GpuInfo,
    cuda_check,
    device_failure,
    probe_gpu,
)
from tests.tensorrt_runtime_support import GIB, SUCCESS, FakeNvml, ScriptedCuda

# ---- CUDA calls -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "value"),
    [((SUCCESS,), None), ((SUCCESS, 5), 5), ((SUCCESS, 1, 2), (1, 2)), ((SUCCESS, None), None), ((SUCCESS, (3, 4)), (3, 4))],
)
def test_the_value_of_a_cuda_call_is_nothing_one_value_or_a_tuple(result, value):
    assert tensorrt_runtime._unpacked(result, lambda error: pytest.fail("no error to report")) == value


def test_a_cuda_error_is_handed_to_the_failure_that_was_asked_for_and_cleared():
    asked = []

    def failure(error):
        asked.append(error)
        return KeyError("the failure that was asked for")

    cudart.cudaSetDevice(10_000)  # leaves an error behind that a recoverable failure must not keep
    with pytest.raises(KeyError, match="the failure that was asked for"):
        tensorrt_runtime._unpacked((cudart.cudaError_t.cudaErrorInvalidDevice, 7), failure)

    assert asked == [cudart.cudaError_t.cudaErrorInvalidDevice]
    assert cudart.cudaPeekAtLastError()[0] == SUCCESS


def test_a_failed_cuda_call_is_named_with_the_error_and_its_text():
    with pytest.raises(TensorRTError) as raised:
        cuda_check(cudart.cudaSetDevice(10_000))

    assert str(raised.value) == "CUDA call failed: cudaErrorInvalidDevice (invalid device ordinal)"


def test_the_text_of_a_cuda_error_that_is_not_utf8_is_still_shown(monkeypatch):
    monkeypatch.setattr(tensorrt_runtime, "cudart", ScriptedCuda(cudaGetErrorString=(SUCCESS, b"bad \xff byte")))

    with pytest.raises(TensorRTError) as raised:
        cuda_check((cudart.cudaError_t.cudaErrorUnknown,))

    assert str(raised.value) == "CUDA call failed: cudaErrorUnknown (bad � byte)"


NO_GPU = (
    "no NVIDIA GPU is visible to CUDA ({name}). The local TensorRT embedder and reranker need an NVIDIA GPU and have no CPU path. "
    "Check the driver and CUDA_VISIBLE_DEVICES, or choose another EMBEDDING_PROVIDER and set RERANKING_PROVIDER=HTTP or "
    "RERANKING_ENABLED=false."
)
DRIVER = (
    "the NVIDIA driver does not match the CUDA 13 runtime ({name}). Install a driver that supports CUDA 13 (R580 or newer) "
    "and reboot or reload its kernel modules."
)


@pytest.mark.parametrize(
    ("name", "cause", "detail"),
    [
        ("cudaErrorNoDevice", "no-gpu", None),
        ("cudaErrorInvalidDevice", "no-gpu", None),
        ("cudaErrorInsufficientDriver", "driver", None),
        ("cudaErrorSystemDriverMismatch", "driver", None),
        ("cudaErrorCompatNotSupportedOnDevice", "driver", None),
        ("cudaErrorStubLibrary", "driver", None),
        ("cudaErrorInvalidValue", "gpu-error", "invalid argument"),
        ("cudaErrorUnknown", "gpu-error", "unknown error"),
        ("cudaErrorMemoryAllocation", "gpu-error", "out of memory"),
    ],
)
def test_a_cuda_error_while_looking_for_the_gpu_is_explained_in_the_words_the_operator_reads(name, cause, detail):
    failure = device_failure(getattr(cudart.cudaError_t, name))

    assert type(failure) is TensorRTStartupError
    assert failure.cause == cause
    expected = {"no-gpu": NO_GPU, "driver": DRIVER}.get(cause, f"CUDA could not initialise the GPU: {{name}} ({detail})")
    assert str(failure) == expected.format(name=name)


@pytest.mark.parametrize(("version", "major"), [("580.65.06", 580), ("575", 575), ("1000.1", 1000), ("9.99.99", 9)])
def test_the_major_version_of_the_driver_is_what_comes_before_the_first_dot(version, major):
    assert GpuInfo("GPU", "8.6", version, GIB).driver_major == major


def test_the_first_device_is_described_from_what_cuda_and_nvml_report(monkeypatch):
    attribute = cudart.cudaDeviceAttr
    values = {attribute.cudaDevAttrComputeCapabilityMajor: 9, attribute.cudaDevAttrComputeCapabilityMinor: 3}
    cuda = ScriptedCuda(
        cudaGetDeviceCount=(SUCCESS, 2),
        cudaGetDeviceProperties=(SUCCESS, SimpleNamespace(name=b"Test \xffGPU")),
        cudaDeviceGetAttribute=lambda which, device: (SUCCESS, values[which]),
        cudaMemGetInfo=(SUCCESS, 1 * GIB, 25_000_000_000),
    )
    nvml = FakeNvml(driver="581.2")
    monkeypatch.setattr(tensorrt_runtime, "cudart", cuda)
    monkeypatch.setattr(tensorrt_runtime, "nvml", nvml)

    gpu = probe_gpu()

    assert gpu == GpuInfo(name="Test �GPU", compute_capability="9.3", driver_version="581.2", total_bytes=25_000_000_000)
    assert [args for args, _ in cuda.made("cudaGetDeviceProperties")] == [(0,)]
    assert sorted(args[1] for args, _ in cuda.made("cudaDeviceGetAttribute")) == [0, 0]
    assert nvml.events == ["init", "shutdown"]


@pytest.mark.parametrize(
    ("answer", "cause", "text"),
    [
        ((SUCCESS, 0), "gpu-error", "CUDA could not initialise the GPU: cudaSuccess (no error)"),
        ((cudart.cudaError_t.cudaErrorNoDevice, 0), "no-gpu", None),
        ((cudart.cudaError_t.cudaErrorInsufficientDriver, 3), "driver", None),
        ((cudart.cudaError_t.cudaErrorUnknown, 1), "gpu-error", "CUDA could not initialise the GPU: cudaErrorUnknown (unknown error)"),
    ],
)
def test_a_device_count_that_is_an_error_or_zero_is_a_startup_error(monkeypatch, answer, cause, text):
    monkeypatch.setattr(tensorrt_runtime, "cudart", ScriptedCuda(cudaGetDeviceCount=answer))

    with pytest.raises(TensorRTStartupError) as raised:
        probe_gpu()

    assert raised.value.cause == cause
    assert text is None or str(raised.value) == text


@pytest.mark.parametrize(
    "error",
    [RuntimeError("Unable to load libnvidia-ml.so.1"), OSError("libnvidia-ml.so.1: no such file"), tensorrt_runtime.nvml.NvmlError(1)],
    ids=lambda error: type(error).__name__,
)
@pytest.mark.parametrize("step", ["init", "version"])
def test_an_nvml_that_fails_is_explained_with_the_cause_and_what_the_container_needs(monkeypatch, step, error):
    nvml = FakeNvml(fail_at=step, error=error)
    monkeypatch.setattr(tensorrt_runtime, "nvml", nvml)

    with pytest.raises(TensorRTStartupError) as raised:
        tensorrt_runtime._driver_version()

    assert raised.value.cause == "gpu-error"
    assert str(raised.value) == (
        f"NVML cannot report the NVIDIA driver version ({type(error).__name__}: {error}). Check that the driver's "
        "management library (libnvidia-ml.so.1) is available to the process; a container needs the 'utility' "
        "driver capability next to 'compute'."
    )
    assert raised.value.__cause__ is error
    assert nvml.events == (["init"] if step == "init" else ["init", "shutdown"])


def test_the_driver_version_is_text(monkeypatch):
    monkeypatch.setattr(tensorrt_runtime, "nvml", FakeNvml(driver=580))

    assert tensorrt_runtime._driver_version() == "580"


def test_the_context_of_a_thread_is_a_pointer_that_is_not_null():
    assert tensorrt_runtime.cuda_context_handle() > 0
