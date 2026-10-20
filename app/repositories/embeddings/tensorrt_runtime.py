"""Run an ONNX model directly on TensorRT: probe the GPU, build a plan, load it and execute it.

The embedder and the reranker both run on this module. There is no ONNX Runtime here, no CPU
path and no fallback: when the GPU, the driver or the TensorRT libraries are not usable the
caller gets a ``TensorRTStartupError`` naming the cause.
TensorRT only logs most of its failures and reports them through return values, so every
return value is checked and turned into an error that carries TensorRT's own message.

An ``IExecutionContext`` is not thread-safe. ``TensorRTEngine`` therefore keeps a pool of
slots, each with its own context, CUDA stream and device buffers, and a call borrows one slot
for its whole duration.
"""
from __future__ import annotations

import logging
import os
import queue
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.exceptions import TensorRTError, TensorRTStartupError

try:
    import tensorrt as trt
    from cuda.bindings import driver, nvml
    from cuda.bindings import runtime as cudart
except ImportError as exc:
    raise TensorRTStartupError(
        "tensorrt-missing",
        f"TensorRT or the CUDA Python bindings cannot be imported ({exc}). The local TensorRT embedder "
        "and reranker need the TensorRT 11 libraries and the CUDA 13 runtime (see docs/gpu-models.md). "
        "Install the locked dependencies with `uv sync`, or choose another EMBEDDING_PROVIDER and set "
        "RERANKING_PROVIDER=HTTP or RERANKING_ENABLED=false.",
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

# What NVML raises: its own error, or a failure to find or load the management library.
_NVML_FAILURES = (nvml.NvmlError, OSError, RuntimeError)


class ProfileError(TensorRTError, ValueError):
    """An input shape falls outside the optimisation profile the engine was built for."""


class PlanRejectedError(TensorRTStartupError):
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
        raise TensorRTError(f"CUDA call failed: {error.name} ({_error_text(error)})")
    if not values:
        return None
    return values[0] if len(values) == 1 else tuple(values)


def _error_text(error: Any) -> str:
    return cudart.cudaGetErrorString(error)[1].decode(errors="replace")


def device_failure(error: Any) -> TensorRTStartupError:
    """The startup error that explains why CUDA reported ``error`` while looking for the GPU."""
    if error in _NO_GPU_ERRORS:
        return TensorRTStartupError(
            "no-gpu",
            f"no NVIDIA GPU is visible to CUDA ({error.name}). The local TensorRT embedder and reranker need "
            "an NVIDIA GPU and have no CPU path. Check the driver and CUDA_VISIBLE_DEVICES, or choose another "
            "EMBEDDING_PROVIDER and set RERANKING_PROVIDER=HTTP or RERANKING_ENABLED=false.",
        )
    if error in _DRIVER_ERRORS:
        return TensorRTStartupError(
            "driver",
            f"the NVIDIA driver does not match the CUDA 13 runtime ({error.name}). Install a driver that "
            "supports CUDA 13 (R580 or newer) and reboot or reload its kernel modules.",
        )
    return TensorRTStartupError(
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
    return GpuInfo(
        name=properties.name.decode(errors="replace"),
        compute_capability=f"{major}.{minor}",
        driver_version=_driver_version(),
        total_bytes=int(total),
    )


def _driver_version() -> str:
    """The NVIDIA driver version according to NVML.

    CUDA can work where NVML cannot (a container that mounts only the compute libraries), and the version is part
    of the engine fingerprint, so a missing NVML ends the start with a cause instead of a traceback.
    """
    try:
        nvml.init_v2()
        try:
            return str(nvml.system_get_driver_version())
        finally:
            nvml.shutdown()
    except _NVML_FAILURES as exc:
        raise TensorRTStartupError(
            "gpu-error",
            f"NVML cannot report the NVIDIA driver version ({type(exc).__name__}: {exc}). Check that the driver's "
            "management library (libnvidia-ml.so.1) is available to the process; a container needs the 'utility' "
            "driver capability next to 'compute'.",
        ) from exc


def cuda_context_handle() -> int:
    """Address of the CUDA context the calling thread runs on.

    The CUDA runtime API gives every thread of a process the primary context of the device, so
    each TensorRT engine of the process shares this one context (and its fixed memory cost).
    """
    cuda_check(cudart.cudaFree(0))  # binds the thread to the primary context
    error, context = driver.cuCtxGetCurrent()
    if error != driver.CUresult.CUDA_SUCCESS:
        raise TensorRTError(f"CUDA could not report the current context: {error.name}")
    return int(context)


# NVML answers with this value when it cannot tell how much memory a process holds.
_NVML_NOT_AVAILABLE = 2**63


def process_gpu_bytes() -> int | None:
    """GPU memory this process holds on all devices according to NVML; None when NVML cannot say.

    NVML reports processes by the pid of the host, so inside a container with its own pid
    namespace the process is not found and the answer is None.
    """
    pid = os.getpid()
    held = 0
    try:
        nvml.init_v2()
        try:
            for index in range(nvml.device_get_count_v2()):
                handle = nvml.device_get_handle_by_index_v2(index)
                for process in nvml.device_get_compute_running_processes_v3(handle):
                    if process.pid == pid:
                        if process.used_gpu_memory >= _NVML_NOT_AVAILABLE:
                            return None
                        held += int(process.used_gpu_memory)
        finally:
            nvml.shutdown()
    except _NVML_FAILURES as exc:
        logger.debug(f"NVML cannot report the GPU memory of this process: {exc}")
        return None
    return held or None


@dataclass(frozen=True)
class GpuMemory:
    """GPU memory at one moment: what this process holds and what the device has in use."""

    process_bytes: int | None
    used_bytes: int
    total_bytes: int

    def describe(self) -> str:
        gib = 1024**3
        held = "unknown (NVML cannot report it)" if self.process_bytes is None else f"{self.process_bytes / gib:.2f} GiB"
        return f"this process holds {held}; the device has {self.used_bytes / gib:.2f} of {self.total_bytes / gib:.0f} GiB in use"


def gpu_memory() -> GpuMemory:
    """Read the GPU memory in use now."""
    free, total = cuda_check(cudart.cudaMemGetInfo())
    return GpuMemory(process_bytes=process_gpu_bytes(), used_bytes=int(total - free), total_bytes=int(total))


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


# The element types a strict fp32 BERT model uses: floats for the numbers, integers and booleans for ids and masks.
_STRICT_TYPES = frozenset({trt.DataType.FLOAT, trt.DataType.INT64, trt.DataType.INT32, trt.DataType.BOOL})


def _not_strict_fp32(network: Any) -> list[str]:
    """The inputs, outputs and layer outputs of a parsed network whose type is neither fp32 nor an integer or boolean."""
    tensors = [network.get_input(index) for index in range(network.num_inputs)]
    tensors += [network.get_output(index) for index in range(network.num_outputs)]
    for index in range(network.num_layers):
        layer = network.get_layer(index)
        tensors += [layer.get_output(output) for output in range(layer.num_outputs)]
    return sorted({f"{tensor.name}: {tensor.dtype.name}" for tensor in tensors if tensor.dtype not in _STRICT_TYPES})


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
        raise TensorRTStartupError(
            "build-failed", f"TensorRT cannot parse {onnx_path} as an ONNX model: {problems or 'no network outputs'}",
        )
    reduced = _not_strict_fp32(network)
    if reduced:
        raise TensorRTStartupError(
            "build-failed",
            f"{onnx_path} holds tensors that are not fp32 or integer ({', '.join(reduced)}). The engines are built "
            "in strict fp32 and the network keeps the types of the file, so a half-precision or quantised model is "
            "refused instead of silently running at lower precision.",
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
        raise TensorRTStartupError("build-failed", f"TensorRT rejected the optimisation profile {profile}")
    plan = builder.build_serialized_network(network, config)
    if plan is None:
        raise TensorRTStartupError(
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
            raise TensorRTStartupError(
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
            raise TensorRTError(f"TensorRT rejected the device address of tensor {name!r}: {TRT_LOGGER.last_error}")

    def _enqueue(self) -> None:
        if not self.context.execute_async_v3(int(self.stream)):
            raise TensorRTError(f"TensorRT could not run the engine: {TRT_LOGGER.last_error}")

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
            raise TensorRTError(f"the model takes the inputs {sorted(self.input_names)}, got {sorted(arrays)}")
        with self._borrowed() as slot:
            return slot.run(arrays)

    def verify(self) -> None:
        """Cheap guard: raise, and refuse all later work, when CUDA recorded an error for this thread."""
        self._ensure_usable()
        try:
            cuda_check(cudart.cudaPeekAtLastError())
        except TensorRTError as exc:
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
            raise TensorRTError("the TensorRT engine is closed")
        if self._failure:
            raise TensorRTError(
                f"the TensorRT engine is unusable after an earlier failure ({self._failure}); restart the server",
            )
