"""Run an ONNX model directly on TensorRT: probe the GPU, build a plan, load it and execute it.

There is no ONNX Runtime here, no CPU path and no fallback: when the GPU, the driver or the
TensorRT libraries are not usable the caller gets a ``RerankerStartupError`` naming the cause.
TensorRT only logs most of its failures and reports them through return values, so every
return value is checked and turned into an error that carries TensorRT's own message.

An ``IExecutionContext`` is not thread-safe. ``TensorRTEngine`` therefore keeps a pool of
slots, each with its own context, CUDA stream and device buffers, and a call borrows one slot
for its whole duration.
"""
from __future__ import annotations

import logging
import queue
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.exceptions import RerankerError, RerankerStartupError

try:
    import tensorrt as trt
    from cuda.bindings import nvml
    from cuda.bindings import runtime as cudart
except ImportError as exc:
    raise RerankerStartupError(
        "tensorrt-missing",
        f"TensorRT or the CUDA Python bindings cannot be imported ({exc}). The local reranker needs "
        "the TensorRT 11 libraries and the CUDA 13 runtime (see docs/requirements.md). Install the "
        "locked dependencies with `uv sync`, or set RERANKING_PROVIDER=HTTP or RERANKING_ENABLED=false.",
    ) from exc

logger = logging.getLogger(__name__)

_SUCCESS = cudart.cudaError_t.cudaSuccess
_NO_GPU_ERRORS = {cudart.cudaError_t.cudaErrorNoDevice, cudart.cudaError_t.cudaErrorInvalidDevice}
_DRIVER_ERRORS = {
    cudart.cudaError_t.cudaErrorInsufficientDriver,
    cudart.cudaError_t.cudaErrorSystemDriverMismatch,
    cudart.cudaError_t.cudaErrorCompatNotSupportedOnDevice,
    cudart.cudaError_t.cudaErrorStubLibrary,
}
_SEVERITY_LEVELS = {
    trt.ILogger.Severity.INTERNAL_ERROR: logging.ERROR,
    trt.ILogger.Severity.ERROR: logging.ERROR,
    trt.ILogger.Severity.WARNING: logging.INFO,  # the ONNX importer warns about every int64 input
    trt.ILogger.Severity.INFO: logging.DEBUG,
    trt.ILogger.Severity.VERBOSE: logging.DEBUG,
}
_ERROR_SEVERITIES = (trt.ILogger.Severity.INTERNAL_ERROR, trt.ILogger.Severity.ERROR)


class ProfileError(RerankerError, ValueError):
    """An input shape falls outside the optimisation profile the engine was built for."""


class PlanRejectedError(RerankerStartupError):
    """TensorRT refused to deserialize a plan (damaged file, or built by another TensorRT version)."""


class _TrtLogger(trt.ILogger):
    """Send TensorRT's messages to ``logging`` and remember the last error for exception texts."""

    def __init__(self) -> None:
        super().__init__()
        self.last_error = ""

    def log(self, severity: Any, msg: str) -> None:
        logger.log(_SEVERITY_LEVELS[severity], "TensorRT: %s", msg)
        if severity in _ERROR_SEVERITIES:
            self.last_error = msg


# TensorRT objects must not outlive their logger, so one instance serves the whole process.
TRT_LOGGER = _TrtLogger()


def cuda_check(result: tuple[Any, ...]) -> Any:
    """Return the value of a cuda-python call (None, one value or a tuple); raise on a CUDA error."""
    error, *values = result
    if error != _SUCCESS:
        cudart.cudaGetLastError()  # a recoverable error must not make the post-call guard fail later
        raise RerankerError(f"CUDA call failed: {error.name} ({_error_text(error)})")
    if not values:
        return None
    return values[0] if len(values) == 1 else tuple(values)


def _error_text(error: Any) -> str:
    return cudart.cudaGetErrorString(error)[1].decode(errors="replace")


def device_failure(error: Any) -> RerankerStartupError:
    """The startup error that explains why CUDA reported ``error`` while looking for the GPU."""
    if error in _NO_GPU_ERRORS:
        return RerankerStartupError(
            "no-gpu",
            f"no NVIDIA GPU is visible to CUDA ({error.name}). The local TensorRT reranker needs an NVIDIA "
            "GPU and has no CPU path. Check the driver and CUDA_VISIBLE_DEVICES, or set "
            "RERANKING_PROVIDER=HTTP or RERANKING_ENABLED=false.",
        )
    if error in _DRIVER_ERRORS:
        return RerankerStartupError(
            "driver",
            f"the NVIDIA driver does not match the CUDA 13 runtime ({error.name}). Install a driver that "
            "supports CUDA 13 (R580 or newer) and reboot or reload its kernel modules.",
        )
    return RerankerStartupError(
        "gpu-error", f"CUDA could not initialise the GPU: {error.name} ({_error_text(error)})",
    )


@dataclass(frozen=True)
class GpuInfo:
    """The device the engine is built for and runs on (the first visible CUDA device)."""

    name: str
    compute_capability: str
    driver_version: str
    total_bytes: int

    @property
    def driver_major(self) -> int:
        return int(self.driver_version.split(".")[0])


def probe_gpu() -> GpuInfo:
    """Describe the first visible CUDA device, or raise the startup error that explains its absence."""
    error, count = cudart.cudaGetDeviceCount()
    if error != _SUCCESS or not count:
        raise device_failure(error)
    properties = cuda_check(cudart.cudaGetDeviceProperties(0))
    attribute = cudart.cudaDeviceAttr
    major = cuda_check(cudart.cudaDeviceGetAttribute(attribute.cudaDevAttrComputeCapabilityMajor, 0))
    minor = cuda_check(cudart.cudaDeviceGetAttribute(attribute.cudaDevAttrComputeCapabilityMinor, 0))
    _free, total = cuda_check(cudart.cudaMemGetInfo())
    nvml.init_v2()
    try:
        driver = nvml.system_get_driver_version()
    finally:
        nvml.shutdown()
    return GpuInfo(
        name=properties.name.decode(errors="replace"),
        compute_capability=f"{major}.{minor}",
        driver_version=driver,
        total_bytes=int(total),
    )


@dataclass(frozen=True)
class OptimizationProfile:
    """The (batch, sequence) range, minimum, tuned-for and maximum, that every input is built for."""

    min_shape: tuple[int, int]
    opt_shape: tuple[int, int]
    max_shape: tuple[int, int]

    def describe(self) -> dict[str, list[int]]:
        return {"min": list(self.min_shape), "opt": list(self.opt_shape), "max": list(self.max_shape)}


def tensorrt_version() -> str:
    return str(trt.__version__)


def build_plan(onnx_path: Path, profile: OptimizationProfile, *, tf32: bool) -> bytes:
    """Build a serialized TensorRT plan from an ONNX file. The network keeps the types of the file.

    With ``tf32`` false the builder flag is cleared, so matrix multiplications are exact fp32.
    """
    builder = trt.Builder(TRT_LOGGER)
    network = builder.create_network()
    parser = trt.OnnxParser(network, TRT_LOGGER)
    parsed = parser.parse_from_file(str(onnx_path))
    if not parsed or not network.num_outputs:  # arbitrary bytes can "parse" into an empty network
        problems = "; ".join(str(parser.get_error(i)) for i in range(parser.num_errors))
        raise RerankerStartupError(
            "build-failed", f"TensorRT cannot parse {onnx_path} as an ONNX model: {problems or 'no network outputs'}",
        )
    config = builder.create_builder_config()
    if not tf32:
        config.clear_flag(trt.BuilderFlag.TF32)
    optimization = builder.create_optimization_profile()
    for index in range(network.num_inputs):
        optimization.set_shape(
            network.get_input(index).name, profile.min_shape, profile.opt_shape, profile.max_shape,
        )
    if config.add_optimization_profile(optimization) < 0:
        raise RerankerStartupError("build-failed", f"TensorRT rejected the optimisation profile {profile}")
    plan = builder.build_serialized_network(network, config)
    if plan is None:
        raise RerankerStartupError(
            "build-failed", f"TensorRT could not build an engine from {onnx_path}: {TRT_LOGGER.last_error}",
        )
    return bytes(plan)


def _set_shape(context: Any, name: str, shape: tuple[int, ...]) -> None:
    if not context.set_input_shape(name, shape):
        raise ProfileError(
            f"TensorRT rejected the shape {shape} of input {name!r}: {TRT_LOGGER.last_error}",
        )


class _Slot:
    """One execution context with its own stream and device buffers; used by one thread at a time."""

    def __init__(self, engine: Any, inputs: list[str], output: str, profile: OptimizationProfile) -> None:
        context = engine.create_execution_context()
        if context is None:
            raise RerankerStartupError(
                "engine-unusable", f"TensorRT cannot create an execution context: {TRT_LOGGER.last_error}",
            )
        self.context: Any = context
        self.inputs = inputs
        self.output = output
        self.dtypes = {
            name: np.dtype(trt.nptype(engine.get_tensor_dtype(name))) for name in (*inputs, output)
        }
        for name in inputs:
            _set_shape(context, name, profile.max_shape)
        output_shape = tuple(context.get_tensor_shape(output))
        capacity = {name: int(np.prod(profile.max_shape)) * self.dtypes[name].itemsize for name in inputs}
        capacity[output] = int(np.prod(output_shape)) * self.dtypes[output].itemsize
        self.stream = cuda_check(cudart.cudaStreamCreate())
        self.buffers = {name: cuda_check(cudart.cudaMalloc(size)) for name, size in capacity.items()}

    def run(self, arrays: Mapping[str, np.ndarray]) -> np.ndarray:
        """Copy the inputs to the device, execute, and return the output as a host array."""
        staged = {}
        for name in self.inputs:
            array = np.ascontiguousarray(arrays[name], dtype=self.dtypes[name])
            _set_shape(self.context, name, array.shape)
            cuda_check(cudart.cudaMemcpyAsync(
                self.buffers[name], array.ctypes.data, array.nbytes,
                cudart.cudaMemcpyKind.cudaMemcpyHostToDevice, self.stream,
            ))
            self._bind(name, self.buffers[name])
            staged[name] = array  # the host memory must stay valid until the stream is synchronised
        result = np.empty(tuple(self.context.get_tensor_shape(self.output)), dtype=self.dtypes[self.output])
        self._bind(self.output, self.buffers[self.output])
        self._enqueue()
        cuda_check(cudart.cudaMemcpyAsync(
            result.ctypes.data, self.buffers[self.output], result.nbytes,
            cudart.cudaMemcpyKind.cudaMemcpyDeviceToHost, self.stream,
        ))
        cuda_check(cudart.cudaStreamSynchronize(self.stream))
        return result

    def _bind(self, name: str, pointer: int) -> None:
        if not self.context.set_tensor_address(name, int(pointer)):
            raise RerankerError(f"TensorRT rejected the device address of tensor {name!r}: {TRT_LOGGER.last_error}")

    def _enqueue(self) -> None:
        if not self.context.execute_async_v3(int(self.stream)):
            raise RerankerError(f"TensorRT could not run the engine: {TRT_LOGGER.last_error}")

    def release(self) -> None:
        self.context = None
        cuda_check(cudart.cudaStreamDestroy(self.stream))
        for pointer in self.buffers.values():
            cuda_check(cudart.cudaFree(pointer))


class TensorRTEngine:
    """A deserialized plan with ``contexts`` execution slots. ``infer`` is safe to call from any thread.

    The engine has one output; ``infer`` returns it. A call fails with ``ProfileError`` when an input
    shape lies outside the optimisation profile the plan was built for, and nothing is padded or
    split here, so the caller keeps its shapes inside the profile.
    """

    def __init__(self, plan: bytes, profile: OptimizationProfile, *, contexts: int = 1) -> None:
        self._runtime: Any = trt.Runtime(TRT_LOGGER)
        engine = self._runtime.deserialize_cuda_engine(plan)
        if engine is None:
            raise PlanRejectedError(
                "engine-unusable", f"TensorRT cannot deserialize the plan: {TRT_LOGGER.last_error}",
            )
        names = [engine.get_tensor_name(i) for i in range(engine.num_io_tensors)]
        self.input_names = [n for n in names if engine.get_tensor_mode(n) == trt.TensorIOMode.INPUT]
        output = next(n for n in names if engine.get_tensor_mode(n) == trt.TensorIOMode.OUTPUT)
        self._failure = ""
        self._closed = False
        self._engine: Any = engine
        self._slots: queue.SimpleQueue[_Slot] = queue.SimpleQueue()
        self._size = contexts
        for _ in range(contexts):
            self._slots.put(_Slot(engine, self.input_names, output, profile))

    def infer(self, arrays: Mapping[str, np.ndarray]) -> np.ndarray:
        """Run the model on ``arrays`` (one per input name) and return its output."""
        self._ensure_usable()
        if set(arrays) != set(self.input_names):
            raise RerankerError(f"the model takes the inputs {sorted(self.input_names)}, got {sorted(arrays)}")
        with self._borrowed() as slot:
            return slot.run(arrays)

    def verify(self) -> None:
        """Cheap guard: raise, and refuse all later work, when CUDA recorded an error for this thread."""
        self._ensure_usable()
        try:
            cuda_check(cudart.cudaPeekAtLastError())
        except RerankerError as exc:
            self._failure = str(exc)
            raise

    def close(self) -> None:
        """Release the device buffers once every borrowed slot has come back. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        for _ in range(self._size):
            self._slots.get().release()
        self._engine = None
        self._runtime = None

    @contextmanager
    def _borrowed(self) -> Iterator[_Slot]:
        slot = self._slots.get()
        try:
            yield slot
        finally:
            self._slots.put(slot)

    def _ensure_usable(self) -> None:
        if self._closed:
            raise RerankerError("the TensorRT engine is closed")
        if self._failure:
            raise RerankerError(
                f"the TensorRT engine is unusable after an earlier failure ({self._failure}); restart the server",
            )
