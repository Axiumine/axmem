# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Releasing the GPU after a failure: a slot or an engine that cannot be made, a release step that fails, a refused start.

The engines are the real ones on the real GPU. The failures are injected at the CUDA wrapper calls of
``tensorrt_runtime`` (``cudaMalloc``, ``cudaStreamCreate``, ``cudaFree``, ``cudaStreamDestroy``, ``cudaStreamSynchronize``,
``cudaPeekAtLastError``), never in a model. ``FaultyCuda`` stands in for the ``cudart`` module of ``cuda.bindings``: it
counts what was really allocated and what the code asked to free, and it makes chosen calls report an error. A call that
reports a sticky error after doing its work (``perform``) lets the memory checks see what the teardown asked CUDA to
free, which a context that is really poisoned would not do until the process exits.

The memory is read through ``gpu_memory()``, the NVML helper of the module: the bytes this process holds when NVML can
say, else the bytes in use on the device.
"""
import gc
import logging
import traceback
from collections import Counter

import pytest
from cuda.bindings import runtime as cudart

from app import bootstrap
from app.config.settings import settings
from app.exceptions import TensorRTError, TensorRTUnavailableError
from app.repositories.embeddings import tensorrt_reranker, tensorrt_runtime
from app.repositories.embeddings.tensorrt_cache import PLAN_NAME
from app.repositories.embeddings.tensorrt_embedder import PROFILE as EMBEDDER_PROFILE
from app.repositories.embeddings.tensorrt_embedder import TensorRTEmbeddingsAdapter
from app.repositories.embeddings.tensorrt_reranker import TensorRTRerankAdapter
from app.repositories.embeddings.tensorrt_runtime import TensorRTEngine, gpu_memory

# The memory the device and the process may differ by between two readings for reasons that are not ours (the desktop,
# another process, allocator rounding). A slot of the embedder holds 50 MiB of output buffer and an engine hundreds, so
# a release that was skipped moves the reading by more than this; the exact bookkeeping of ``FaultyCuda`` catches the
# small allocations that this tolerance hides.
TOLERANCE = 64 * 1024**2
BUFFERS_PER_SLOT = 4  # input_ids, attention_mask, token_type_ids and the output
OUT_OF_MEMORY = cudart.cudaError_t.cudaErrorMemoryAllocation
ILLEGAL_ADDRESS = cudart.cudaError_t.cudaErrorIllegalAddress
UNKNOWN = cudart.cudaError_t.cudaErrorUnknown


class FaultyCuda:
    """The ``cuda.bindings.runtime`` module, with the memory calls recorded and chosen calls made to report an error.

    ``fail`` lists ``(call, number)`` pairs, or ``(call, number, error)``: the ``number``-th invocation of ``call`` (1 is
    the first, ``None`` is every one) reports ``error``, or the error of its own. ``cudaFree(0)``, which binds a
    thread to the CUDA context, counts as the call
    ``"cudaFree(0)"`` and is never taken for a release. A failed ``cudaMalloc`` or ``cudaStreamCreate`` allocates
    nothing. With ``perform`` a failed ``cudaFree``, ``cudaStreamDestroy`` or ``cudaStreamSynchronize`` does its work
    first and reports the error afterwards. With ``poison`` the first failure makes every later call report the error,
    as a sticky error does.
    """

    WRAPPED = frozenset({
        "cudaMalloc", "cudaFree", "cudaStreamCreate", "cudaStreamDestroy", "cudaStreamSynchronize",
        "cudaPeekAtLastError",
    })
    PERFORMED = frozenset({"cudaFree", "cudaStreamDestroy", "cudaStreamSynchronize"})

    def __init__(self, fail=(), *, error, perform=False, poison=False):
        self.fail = {(call, number): own[0] if own else error for call, number, *own in fail}
        self.error, self.perform, self.poison = error, perform, poison
        self.poisoned = False
        self.invocations = Counter()
        self.made_pointers: list[int] = []  # allocated for real
        self.made_streams: list[int] = []
        self.freed: list[int] = []  # what the code asked to free, whether or not the call worked
        self.destroyed: list[int] = []
        self._live_pointers: set[int] = set()  # allocated for real and not yet released for real
        self._live_streams: dict[int, object] = {}

    def __getattr__(self, name):
        real = getattr(cudart, name)
        if name not in self.WRAPPED:
            return real
        return lambda *args: self._call(name, real, args)

    def _call(self, name, real, args):
        key = "cudaFree(0)" if (name, args) == ("cudaFree", (0,)) else name
        self.invocations[key] += 1
        self._asked(key, args)
        fault = self.fail.get((key, self.invocations[key]), self.fail.get((key, None)))
        self.poisoned = self.poisoned or (fault is not None and self.poison)
        if fault is None and not self.poisoned:
            return self._run(key, real, args)
        if self.perform and name in self.PERFORMED:
            self._run(key, real, args)
        return (self.error if fault is None else fault,)

    def _asked(self, key, args):
        """Note what the code asked to free or destroy, whether or not the call then works."""
        if key == "cudaFree":
            self.freed.append(int(args[0]))
        if key == "cudaStreamDestroy":
            self.destroyed.append(int(args[0]))

    def _run(self, key, real, args):
        result = real(*args)
        self._note(key, args, result)
        return result

    def _note(self, key, args, result):
        if key == "cudaMalloc" and result[0] == cudart.cudaError_t.cudaSuccess:
            self.made_pointers.append(int(result[1]))
            self._live_pointers.add(int(result[1]))
        elif key == "cudaStreamCreate" and result[0] == cudart.cudaError_t.cudaSuccess:
            self.made_streams.append(int(result[1]))
            self._live_streams[int(result[1])] = result[1]
        elif key == "cudaFree":
            self._live_pointers.discard(int(args[0]))
        elif key == "cudaStreamDestroy":
            self._live_streams.pop(int(args[0]), None)

    def assert_every_allocation_released_once(self):
        """Each pointer that was allocated was freed once and each stream that was made was destroyed once."""
        assert sorted(self.freed) == sorted(self.made_pointers)
        assert sorted(self.destroyed) == sorted(self.made_streams)

    def free_leaks(self):
        """Really release whatever a failed release step left allocated, so that no test leaves memory behind."""
        for pointer in self._live_pointers:
            cudart.cudaFree(pointer)
        for stream in self._live_streams.values():
            cudart.cudaStreamDestroy(stream)
        self._live_pointers.clear()
        self._live_streams.clear()


class Installer:
    """Puts a ``FaultyCuda`` in ``tensorrt_runtime`` in place of the real ``cudart`` module."""

    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.installed: list[FaultyCuda] = []

    def __call__(self, *args, **kwargs):
        cuda = FaultyCuda(*args, **kwargs)
        self.installed.append(cuda)
        self.monkeypatch.setattr(tensorrt_runtime, "cudart", cuda)
        return cuda

    def restore(self):
        self.monkeypatch.setattr(tensorrt_runtime, "cudart", cudart)


@pytest.fixture
def faulty(monkeypatch):
    """Call it to install a ``FaultyCuda``; ``faulty.restore()`` puts the real module back."""
    installer = Installer(monkeypatch)
    yield installer
    installer.restore()
    for cuda in installer.installed:
        cuda.free_leaks()


def held_bytes():
    """The GPU memory this process holds (NVML), or the memory in use on the device when NVML cannot say."""
    gc.collect()
    memory = gpu_memory()
    return memory.used_bytes if memory.process_bytes is None else memory.process_bytes


def failure_of(call):
    """The exception that ``call()`` raises, with the frames of its traceback cleared.

    A traceback keeps the locals of every frame it passes through, an engine among them, so the memory of an engine
    that failed to start would still be held while the test looks at the error.
    """
    try:
        call()
    except BaseException as exc:  # noqa: BLE001 - the test looks at whatever was raised
        error = exc
    else:
        pytest.fail("the call was expected to raise")
    seen = []
    pending = error
    while pending is not None and pending not in seen:
        seen.append(pending)
        traceback.clear_frames(pending.__traceback__)
        pending = pending.__cause__ or pending.__context__
    return error


def released(cuda):
    cuda.assert_every_allocation_released_once()
    return len(cuda.made_streams), len(cuda.made_pointers)


@pytest.fixture(scope="module")
def embedder_plan(tensorrt_embedder):
    return (tensorrt_embedder.startup.engine_dir / PLAN_NAME).read_bytes()


@pytest.fixture(scope="module", autouse=True)
def lazy_kernels_loaded(tensorrt_embedder, tensorrt_reranker):
    """Run both models once through private adapters, so that CUDA's lazily loaded kernels are not read as a leak."""
    for adapter_class in (TensorRTEmbeddingsAdapter, TensorRTRerankAdapter):
        adapter = adapter_class()
        adapter.close()


def error_records(caplog):
    return [record for record in caplog.records if record.levelno >= logging.ERROR]


# --------------------------------------------------------------------------
# A slot or an engine that cannot be made releases what it made, and the original error surfaces
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("contexts", "call", "number", "streams", "buffers"),
    [
        (1, "cudaStreamCreate", 1, 0, 0),  # the first slot has nothing yet
        (1, "cudaMalloc", 1, 1, 0),  # the stream only
        (1, "cudaMalloc", 3, 1, 2),  # the stream and two of the buffers
        (2, "cudaStreamCreate", 2, 1, BUFFERS_PER_SLOT),  # one slot is complete, the second has nothing
        (2, "cudaMalloc", BUFFERS_PER_SLOT + 2, 2, BUFFERS_PER_SLOT + 1),  # one complete, the second has its stream and a buffer
    ],
)
def test_an_engine_that_cannot_make_a_slot_releases_the_slots_it_made(
    embedder_plan, faulty, caplog, contexts, call, number, streams, buffers,
):
    cuda = faulty(fail=[(call, number)], error=OUT_OF_MEMORY)
    before = held_bytes()

    error = failure_of(lambda: TensorRTEngine(embedder_plan, EMBEDDER_PROFILE, contexts=contexts))

    assert isinstance(error, TensorRTError)
    assert not isinstance(error, TensorRTUnavailableError)
    assert "CUDA call failed: cudaErrorMemoryAllocation" in str(error)  # the failure that stopped it, not a consequence
    assert released(cuda) == (streams, buffers)  # every stream and buffer that was made was released, once
    assert not error_records(caplog)  # and releasing them went well
    assert abs(held_bytes() - before) <= TOLERANCE  # the engine and the contexts it had made are gone as well


def test_a_release_that_fails_while_a_slot_is_made_does_not_replace_the_error_that_stopped_it(
    embedder_plan, faulty, caplog,
):
    baseline = held_bytes()
    cuda = faulty(
        fail=[("cudaMalloc", 3, OUT_OF_MEMORY), ("cudaStreamDestroy", 1), ("cudaFree", 2)],
        error=ILLEGAL_ADDRESS,
        perform=True,
    )

    error = failure_of(lambda: TensorRTEngine(embedder_plan, EMBEDDER_PROFILE))

    assert "cudaErrorMemoryAllocation" in str(error)  # the allocation that failed, not the release that failed after it
    assert "cudaErrorIllegalAddress" not in str(error)
    assert released(cuda) == (1, 2)
    (record,) = error_records(caplog)
    assert "2 release step(s) failed" in record.getMessage()
    assert "cudaErrorIllegalAddress" in record.getMessage()  # the secondary failures are in the log
    assert abs(held_bytes() - baseline) <= TOLERANCE


def test_a_slot_whose_shape_is_refused_releases_nothing_it_never_made(embedder_plan, faulty):
    """The failure comes before any CUDA call of the slot: there is nothing to release and nothing is attempted."""
    cuda = faulty(error=OUT_OF_MEMORY)
    oversized = tensorrt_runtime.OptimizationProfile(min_shape=(1, 2), opt_shape=(1, 2), max_shape=(65, 512))

    error = failure_of(lambda: TensorRTEngine(embedder_plan, oversized, contexts=2))

    assert isinstance(error, tensorrt_runtime.ProfileError)
    assert cuda.invocations["cudaFree"] == cuda.invocations["cudaStreamDestroy"] == 0
    assert released(cuda) == (0, 0)


# --------------------------------------------------------------------------
# A release step that fails does not stop the others, and does not replace the error that led to the close
# --------------------------------------------------------------------------
def test_a_release_step_that_reports_an_error_does_not_stop_the_close(embedder_plan, faulty, caplog):
    baseline = held_bytes()
    # The first stream, the third buffer of the first slot and the last buffer of the second report a sticky error.
    cuda = faulty(
        fail=[("cudaStreamDestroy", 1), ("cudaFree", 3), ("cudaFree", 2 * BUFFERS_PER_SLOT)],
        error=ILLEGAL_ADDRESS,
        perform=True,
    )
    engine = TensorRTEngine(embedder_plan, EMBEDDER_PROFILE, contexts=2)
    with engine._borrowed() as slot:
        pass

    engine.close()  # does not raise: it is the cleanup of whatever went wrong before

    assert released(cuda) == (2, 2 * BUFFERS_PER_SLOT)  # all ten steps were attempted, none twice
    (record,) = error_records(caplog)  # the three that reported an error are one log line
    message = record.getMessage()
    assert "3 release step(s) failed and every other step ran" in message
    assert message.count("cudaErrorIllegalAddress") == 1  # the reason they share is named once
    assert message.count("destroying the stream") == 1
    assert message.count("freeing the buffer of") == 2
    assert (engine._engine, engine._runtime) == (None, None)
    assert abs(held_bytes() - baseline) <= TOLERANCE
    assert slot.release() == []  # a slot that was released is not released again
    attempts = dict(cuda.invocations)
    engine.close()
    assert dict(cuda.invocations) == attempts  # closing twice asks CUDA for nothing more
    with pytest.raises(TensorRTUnavailableError, match="closed"):
        engine.verify()


def test_every_release_step_is_attempted_once_even_when_all_of_them_fail(embedder_plan, faulty, caplog):
    cuda = faulty(fail=[("cudaStreamDestroy", None), ("cudaFree", None)], error=ILLEGAL_ADDRESS)
    engine = TensorRTEngine(embedder_plan, EMBEDDER_PROFILE, contexts=2)

    engine.close()

    cuda.assert_every_allocation_released_once()  # every one was asked for, and none was asked for twice
    assert cuda.invocations["cudaStreamDestroy"] == 2
    assert cuda.invocations["cudaFree"] == 2 * BUFFERS_PER_SLOT
    (record,) = error_records(caplog)
    assert f"{2 * (BUFFERS_PER_SLOT + 1)} release step(s) failed" in record.getMessage()
    assert engine._engine is None
    assert engine._runtime is None
    cuda.free_leaks()


# --------------------------------------------------------------------------
# A start that is refused releases the engine, and the error that refused it is the one that surfaces
# --------------------------------------------------------------------------
@pytest.mark.parametrize("adapter_class", [TensorRTEmbeddingsAdapter, TensorRTRerankAdapter])
def test_a_sticky_cuda_error_in_the_warm_up_is_the_error_that_surfaces(adapter_class, faulty, caplog):
    before = held_bytes()
    cuda = faulty(fail=[("cudaStreamSynchronize", 1)], error=ILLEGAL_ADDRESS, perform=True, poison=True)

    error = failure_of(adapter_class)

    assert isinstance(error, TensorRTUnavailableError)  # what the warm-up met, not what the release met afterwards
    assert "cudaErrorIllegalAddress" in str(error)
    streams, buffers = released(cuda)  # every release was asked for once although every one reported the error
    assert (streams, buffers) == (1, BUFFERS_PER_SLOT)
    (record,) = error_records(caplog)
    assert f"{streams + buffers} release step(s) failed" in record.getMessage()
    assert abs(held_bytes() - before) <= TOLERANCE


@pytest.mark.parametrize("adapter_class", [TensorRTEmbeddingsAdapter, TensorRTRerankAdapter])
def test_a_failure_after_the_warm_up_releases_the_engine_and_surfaces(adapter_class, faulty, caplog):
    """The CUDA context handle is read after the warm-up has run: the engine is loaded and warm when that fails."""
    before = held_bytes()
    cuda = faulty(fail=[("cudaFree(0)", 1)], error=UNKNOWN)

    error = failure_of(adapter_class)

    assert isinstance(error, TensorRTError)
    assert "CUDA call failed: cudaErrorUnknown" in str(error)
    assert released(cuda) == (1, BUFFERS_PER_SLOT)
    assert not error_records(caplog)
    assert abs(held_bytes() - before) <= TOLERANCE


def test_a_reranker_with_no_valid_worker_count_builds_no_engine(monkeypatch):
    """The worker pool is made before the engine, so a count the pool refuses costs no GPU memory."""
    def no_engine(**kwargs):
        raise AssertionError("an engine was opened")

    monkeypatch.setattr(tensorrt_reranker, "open_engine", no_engine)

    with pytest.raises(ValueError, match="max_workers"):
        TensorRTRerankAdapter(workers=-1)


# --------------------------------------------------------------------------
# The shared adapters are all closed when the process lets go of them
# --------------------------------------------------------------------------
def test_an_adapter_that_fails_to_close_keeps_neither_its_engine_nor_the_others_open(
    monkeypatch, tensorrt_embedder, tensorrt_reranker,
):
    monkeypatch.setattr(bootstrap, "_TENSORRT_ADAPTERS", {})
    monkeypatch.setattr(settings, "EMBEDDING_MODEL_DIR", settings.EMBEDDING_MODEL_DIR + "/")  # another key, same files
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKING_PROVIDER", "TensorRT")
    monkeypatch.setattr(settings, "RERANKING_WORKERS", 2)  # another key, same cached engine
    embedder, reranker = bootstrap.get_embedding_adapter(), bootstrap.get_reranker_adapter()
    assert embedder is not tensorrt_embedder
    assert reranker is not tensorrt_reranker

    def wait_fails(wait=True):
        raise RuntimeError("the worker did not stop")

    monkeypatch.setattr(reranker._executor, "shutdown", wait_fails)  # the latest adapter is closed first

    with pytest.raises(RuntimeError, match="the worker did not stop"):
        bootstrap._release_tensorrt_adapters()

    assert bootstrap._TENSORRT_ADAPTERS == {}
    for adapter in (reranker, embedder):  # the one that failed released its engine all the same, and the other closed
        with pytest.raises(TensorRTUnavailableError, match="closed"):
            adapter._engine.verify()


# --------------------------------------------------------------------------
# A real engine through start, failed warm-up and close, over and over
# --------------------------------------------------------------------------
ITERATIONS = 5


@pytest.mark.parametrize("adapter_class", [TensorRTEmbeddingsAdapter, TensorRTRerankAdapter])
def test_start_failed_warm_up_and_close_in_a_loop_do_not_grow_the_gpu_memory(adapter_class, faulty):
    readings = []
    for _ in range(ITERATIONS):
        healthy = adapter_class()
        cuda = faulty(fail=[("cudaStreamSynchronize", 1)], error=ILLEGAL_ADDRESS, perform=True, poison=True)
        error = failure_of(adapter_class)  # the warm-up meets a sticky error and the start is refused
        faulty.restore()
        assert isinstance(error, TensorRTUnavailableError)
        assert released(cuda) == (1, BUFFERS_PER_SLOT)
        healthy.close()
        readings.append(held_bytes())

    assert max(readings) - min(readings) <= TOLERANCE, readings
    assert readings[-1] - readings[0] <= TOLERANCE, readings
