# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The engine and its slots on the real GPU: what a call copies and runs, and the exact words of each refusal."""
import weakref

import numpy as np
import pytest
from cuda.bindings import runtime as cudart

from app.exceptions import TensorRTError, TensorRTStartupError, TensorRTUnavailableError
from app.repositories.embeddings import tensorrt_runtime
from app.repositories.embeddings.tensorrt_reranker import PROFILE
from app.repositories.embeddings.tensorrt_runtime import (
    TRT_LOGGER,
    PlanRejectedError,
    ProfileError,
    TensorRTEngine,
)
from tests.tensorrt_runtime_support import ScriptedCuda

# ---- the engine and its slots -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def plan_bytes(tensorrt_reranker):
    return (tensorrt_reranker.startup.engine_dir / "engine.plan").read_bytes()


def ids(batch, sequence, *, seed=1):
    generator = np.random.default_rng(seed)
    tokens = generator.integers(1000, 5000, (batch, sequence)).astype(np.int64)
    return {"input_ids": tokens, "attention_mask": np.ones_like(tokens), "token_type_ids": np.zeros_like(tokens)}


def test_the_slot_makes_one_stream_and_a_buffer_for_each_input_at_its_largest_and_one_for_the_output(monkeypatch, plan_bytes):
    cuda = ScriptedCuda(watched=["cudaMalloc", "cudaStreamCreate"])
    monkeypatch.setattr(tensorrt_runtime, "cudart", cuda)

    engine = TensorRTEngine(plan_bytes, PROFILE)
    engine.close()

    batch, sequence = PROFILE.max_shape
    assert [args for args, _ in cuda.made("cudaMalloc")] == [(batch * sequence * 8,)] * 3 + [(batch * 4,)]
    assert len(cuda.made("cudaStreamCreate")) == 1


def test_a_call_copies_each_input_in_full_runs_once_and_copies_the_output_back_on_the_same_stream(monkeypatch, plan_bytes):
    cuda = ScriptedCuda(watched=["cudaMalloc", "cudaStreamCreate", "cudaMemcpyAsync", "cudaStreamSynchronize"])
    monkeypatch.setattr(tensorrt_runtime, "cudart", cuda)
    engine = TensorRTEngine(plan_bytes, PROFILE)
    try:
        before = len(cuda.requests)
        engine.infer(ids(3, 10))
    finally:
        engine.close()

    calls = cuda.requests[before:]
    kind = cudart.cudaMemcpyKind
    pointers = [result[1] for _args, result in cuda.made("cudaMalloc")]
    stream = cuda.made("cudaStreamCreate")[0][1][1]
    assert [name for name, _args, _result in calls] == ["cudaMemcpyAsync"] * 4 + ["cudaStreamSynchronize"]
    copies = [args for name, args, _result in calls if name == "cudaMemcpyAsync"]
    assert [(args[2], args[3]) for args in copies] == [(3 * 10 * 8, kind.cudaMemcpyHostToDevice)] * 3 + [
        (3 * 4, kind.cudaMemcpyDeviceToHost),
    ]
    assert [args[0] for args in copies[:3]] == pointers[:3]  # each input goes to its own buffer
    assert copies[3][1] == pointers[3]  # the output comes from its own
    assert {args[4] for args in copies} == {stream}
    assert calls[-1][1] == (stream,)


def test_inputs_that_are_not_laid_out_in_memory_row_by_row_give_the_same_result(plan_bytes):
    engine = TensorRTEngine(plan_bytes, PROFILE)
    try:
        inputs = ids(5, 12)
        expected = engine.infer(inputs)

        reordered = engine.infer({name: np.asfortranarray(array) for name, array in reversed(inputs.items())})
    finally:
        engine.close()

    assert np.array_equal(reordered, expected)
    assert expected.std() > 0  # different texts, different scores: a wrong layout would show


class WatchedNumpy:
    """The ``numpy`` module of the runner; ``ascontiguousarray`` notes (weakly) each array it makes, the rest is numpy."""

    def __init__(self):
        self.made = []

    def __getattr__(self, name):
        return getattr(np, name)

    def ascontiguousarray(self, array, dtype=None):
        converted = np.ascontiguousarray(array, dtype=dtype)
        self.made.append(weakref.ref(converted))
        return converted


def test_the_host_copies_of_the_inputs_are_kept_until_the_stream_has_been_synchronised(monkeypatch, plan_bytes):
    """The copies to the device are asynchronous: the memory they read from must not be freed before the stream is done."""
    watched, alive = WatchedNumpy(), []

    def synchronise(stream):
        alive.extend(reference() is not None for reference in watched.made)
        return cudart.cudaStreamSynchronize(stream)

    engine = TensorRTEngine(plan_bytes, PROFILE)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(tensorrt_runtime, "np", watched)
            patch.setattr(tensorrt_runtime, "cudart", ScriptedCuda(cudaStreamSynchronize=synchronise))
            engine.infer({name: array.astype(np.float64) for name, array in ids(2, 6).items()})  # converted to int64
    finally:
        engine.close()

    assert alive == [True, True, True]


def test_the_inputs_of_the_model_are_known_in_the_order_the_model_has_them(plan_bytes):
    engine = TensorRTEngine(plan_bytes, PROFILE, contexts=3)
    try:
        assert engine.input_names == ["input_ids", "attention_mask", "token_type_ids"]
        assert engine._size == 3
        assert engine.failure == ""
    finally:
        engine.close()


def test_a_slot_that_was_released_holds_nothing_and_is_not_released_again(plan_bytes):
    engine = TensorRTEngine(plan_bytes, PROFILE)
    with engine._borrowed() as slot:
        assert slot.context is not None
        assert slot.stream is not None
        assert len(slot.buffers) == 4

    engine.close()

    assert (slot.context, slot.stream, slot.buffers) == (None, None, {})
    assert slot.release() == []


def test_an_engine_has_one_slot_unless_it_is_asked_for_more(plan_bytes):
    engine = TensorRTEngine(plan_bytes, PROFILE)
    try:
        assert engine._size == 1
        assert engine._slots.qsize() == 1
    finally:
        engine.close()


def test_a_plan_that_tensorrt_cannot_deserialize_is_refused_with_its_message():
    with pytest.raises(PlanRejectedError) as raised:
        TensorRTEngine(b"not a plan", PROFILE)

    assert str(raised.value) == f"TensorRT cannot deserialize the plan: {TRT_LOGGER.last_error}"
    assert TRT_LOGGER.last_error  # and it is TensorRT's own


def test_inputs_with_other_names_than_the_models_are_refused_with_both_lists(plan_bytes):
    engine = TensorRTEngine(plan_bytes, PROFILE)
    try:
        with pytest.raises(TensorRTError) as raised:
            engine.infer({"token_type_ids": np.zeros((1, 4), dtype=np.int64), "input_ids": np.ones((1, 4), dtype=np.int64)})
    finally:
        engine.close()

    assert str(raised.value) == (
        "the model takes the inputs ['attention_mask', 'input_ids', 'token_type_ids'], got ['input_ids', 'token_type_ids']"
    )


def test_a_shape_that_the_profile_does_not_hold_is_named_with_the_shape_the_input_and_tensorrts_reason(plan_bytes):
    engine = TensorRTEngine(plan_bytes, PROFILE)
    try:
        with pytest.raises(ProfileError) as raised:
            engine.infer(ids(65, 16))
    finally:
        engine.close()

    assert str(raised.value) == f"TensorRT rejected the shape (65, 16) of input 'input_ids': {TRT_LOGGER.last_error}"
    assert "does not satisfy any optimization profiles" in TRT_LOGGER.last_error


def test_a_slot_that_tensorrt_cannot_launch_or_bind_says_what_tensorrt_logged(plan_bytes):
    engine = TensorRTEngine(plan_bytes, PROFILE)
    try:
        with engine._borrowed() as slot:
            with pytest.raises(TensorRTError) as launch:
                slot._enqueue()
            assert str(launch.value) == f"TensorRT could not run the engine: {TRT_LOGGER.last_error}"
            assert "Address is not set for input tensor" in TRT_LOGGER.last_error
            with pytest.raises(TensorRTError) as bind:
                slot._bind("nope", 0)
            assert str(bind.value) == f"TensorRT rejected the device address of tensor 'nope': {TRT_LOGGER.last_error}"
            assert "Given invalid tensor name: nope" in TRT_LOGGER.last_error
    finally:
        engine.close()


def test_an_engine_that_cannot_make_an_execution_context_says_what_tensorrt_logged():
    class WithoutContext:
        @staticmethod
        def create_execution_context():
            return None

    with pytest.raises(TensorRTStartupError) as raised:
        tensorrt_runtime._Slot(WithoutContext(), ["input_ids"], "logits", PROFILE)

    assert raised.value.cause == "engine-unusable"
    assert str(raised.value) == f"TensorRT cannot create an execution context: {TRT_LOGGER.last_error}"


def test_an_engine_that_is_closed_or_failed_refuses_work_in_these_words(plan_bytes):
    closed = TensorRTEngine(plan_bytes, PROFILE)
    closed.close()
    failed = TensorRTEngine(plan_bytes, PROFILE)
    cudart.cudaMemcpy(0, 0, 4, cudart.cudaMemcpyKind.cudaMemcpyHostToDevice)  # a CUDA call that fails
    try:
        with pytest.raises(TensorRTUnavailableError) as met:
            failed.verify()
        with pytest.raises(TensorRTUnavailableError) as refused:
            failed.verify()
        with pytest.raises(TensorRTUnavailableError) as shut:
            closed.verify()
    finally:
        failed.close()

    reason = "CUDA call failed: cudaErrorInvalidValue (invalid argument)"
    assert str(met.value) == reason
    assert met.value.__cause__.args == (reason,)
    assert failed.failure == reason
    assert str(refused.value) == f"the TensorRT engine is unusable after an earlier failure ({reason}); restart the server"
    assert str(shut.value) == "the TensorRT engine is closed"
