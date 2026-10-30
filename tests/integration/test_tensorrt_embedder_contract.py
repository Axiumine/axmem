# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The TensorRT embedder, call for call: its start-up report and line, the arguments that win over the settings, the
vectors it takes from the model output, what a refused text, width or model output says, and the one worker thread that
serves every call.

The engine is the real one on the real GPU. Where a test needs an output the model cannot be made to give (a vector of
NaN, a hidden state of zeros, a hidden state it knows), it replaces the ``infer`` of the engine of the embedder;
everything around it is the production code.
"""
import asyncio
import json
import logging
import shutil
import threading
import time
import warnings
from pathlib import Path

import numpy as np
import pytest

from app.config.settings import settings
from app.exceptions import TensorRTError, TensorRTStartupError, TensorRTUnavailableError
from app.repositories.embeddings import tensorrt_embedder as embedder_module
from app.repositories.embeddings import tensorrt_model
from app.repositories.embeddings.tensorrt_embedder import TensorRTEmbeddingsAdapter

WIDTH = 384
WAIT_SECONDS = 10
SLUG = "BAAI--bge-small-en-v1.5"


def no_network(*args, **kwargs):
    raise AssertionError("the model files are in place, so nothing may be downloaded")


def recorded_digest(model_root: Path) -> str:
    manifest = json.loads((model_root / tensorrt_model.MANIFEST_FILE).read_text())
    return manifest["sha256"][tensorrt_model.ONNX_FILE]


def expect_engine(monkeypatch, gpu, *, model_dir, cache_dir, tf32=False):
    """Wrap ``open_engine``: a start-up that hands it other arguments fails before an engine is built."""
    real, model_root = embedder_module.open_engine, Path(model_dir) / SLUG
    expected = {
        "onnx_path": model_root / tensorrt_model.ONNX_FILE,
        "model_sha256": recorded_digest(model_root),
        "group_name": SLUG,
        "cache_dir": Path(cache_dir),
        "gpu": gpu,
        "tf32": tf32,
        "profile": embedder_module.PROFILE,
        "contexts": 1,
    }

    def checking(**kwargs):
        for name, value in expected.items():
            assert kwargs[name] == value, name
        return real(**kwargs)

    monkeypatch.setattr(embedder_module, "open_engine", checking)


def spied(monkeypatch, module, name):
    """Record the arguments of ``module.name``; the real function still answers."""
    real, calls = getattr(module, name), []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, spy)
    return calls


def batch_sizes(adapter, monkeypatch) -> list[int]:
    """The sizes of the batches that reach the engine from now on."""
    sizes, infer = [], adapter._engine.infer

    def recording(arrays):
        sizes.append(arrays["input_ids"].shape[0])
        return infer(arrays)

    monkeypatch.setattr(adapter._engine, "infer", recording)
    return sizes


# --------------------------------------------------------------------------
# What the start-up reports
# --------------------------------------------------------------------------
def test_a_start_reports_what_it_did_and_logs_the_same_line(tensorrt_embedder, monkeypatch, caplog):
    monkeypatch.setattr(embedder_module.logger, "disabled", False)  # the logging set-up of an app that started earlier disables it
    caplog.set_level(logging.INFO, logger=embedder_module.logger.name)
    expect_engine(
        monkeypatch, tensorrt_embedder.startup.gpu,
        model_dir=settings.EMBEDDING_MODEL_DIR, cache_dir=settings.EMBEDDING_ENGINE_CACHE_DIR,
    )
    fetched = spied(monkeypatch, embedder_module, "fetch_model")
    slugged = spied(monkeypatch, embedder_module, "model_slug")
    before = time.perf_counter()
    adapter = TensorRTEmbeddingsAdapter()
    elapsed = time.perf_counter() - before
    try:
        report = adapter.startup

        assert (report.role, report.cache_hit, report.reason, report.tf32, report.workers) == (
            "embedder", True, "cache hit", False, 1,
        )
        assert 0 < report.seconds <= elapsed
        assert (report.engine_dir, report.gpu) == (tensorrt_embedder.startup.engine_dir, tensorrt_embedder.startup.gpu)
        assert fetched == [((settings.EMBEDDING_MODEL, settings.EMBEDDING_MODEL_DIR, "EMBEDDING_MODEL"), {})]
        assert slugged == [((settings.EMBEDDING_MODEL, "EMBEDDING_MODEL"), {})]
        assert (adapter.model, adapter.dimensions, adapter.tf32, adapter.workers) == (
            settings.EMBEDDING_MODEL, WIDTH, False, 1,
        )
        assert [r.getMessage() for r in caplog.records if r.name == embedder_module.logger.name] == [report.summary()]
    finally:
        adapter.close()


def test_the_arguments_win_over_the_settings(tensorrt_embedder, tmp_path, monkeypatch):
    """The settings point at nothing usable, so only an adapter that takes its arguments can start."""
    models, model, report = settings.EMBEDDING_MODEL_DIR, settings.EMBEDDING_MODEL, tensorrt_embedder.startup
    seeded = tmp_path / "engines" / report.engine_dir.parent.name / report.engine_dir.name
    shutil.copytree(report.engine_dir, seeded)
    for name, value in {
        "EMBEDDING_MODEL": "not a repository",
        "EMBEDDING_DIMENSIONS": 999,
        "EMBEDDING_TF32": True,
        "EMBEDDING_MODEL_DIR": str(tmp_path / "models-of-the-settings"),
        "EMBEDDING_ENGINE_CACHE_DIR": str(tmp_path / "engines-of-the-settings"),
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(tensorrt_model, "hf_hub_download", no_network)
    expect_engine(monkeypatch, report.gpu, model_dir=models, cache_dir=tmp_path / "engines", tf32=False)
    fetched = spied(monkeypatch, embedder_module, "fetch_model")
    slugged = spied(monkeypatch, embedder_module, "model_slug")

    adapter = TensorRTEmbeddingsAdapter(
        model=model, tf32=False, dimensions=WIDTH, model_dir=models, cache_dir=str(tmp_path / "engines"),
    )
    try:
        assert (adapter.model, adapter.dimensions, adapter.tf32) == (model, WIDTH, False)
        assert fetched == [((model, models, "EMBEDDING_MODEL"), {})]
        assert slugged == [((model, "EMBEDDING_MODEL"), {})]
        assert adapter.startup.engine_dir == seeded
        assert adapter.startup.cache_hit
        assert len(adapter.embed(["hello"])[0]) == WIDTH
    finally:
        adapter.close()


# --------------------------------------------------------------------------
# What a refusal says
# --------------------------------------------------------------------------
def test_a_model_that_is_no_repository_id_is_refused_naming_the_setting(tmp_path):
    with pytest.raises(TensorRTStartupError) as refused:
        TensorRTEmbeddingsAdapter(model="not a repository", model_dir=str(tmp_path), cache_dir=str(tmp_path))

    assert refused.value.cause == "model-unavailable"
    assert str(refused.value) == (
        "EMBEDDING_MODEL='not a repository' is not a Hugging Face repository id such as 'owner/name', "
        "which the TensorRT models need in order to fetch onnx/model.onnx and tokenizer.json."
    )


def test_a_width_other_than_the_configured_one_is_refused_with_the_whole_instruction(tensorrt_embedder, monkeypatch):
    monkeypatch.setattr(tensorrt_embedder, "dimensions", 768)

    with pytest.raises(TensorRTStartupError) as refused:
        tensorrt_embedder._check_width([0.0] * WIDTH)

    assert refused.value.cause == "dimensions"
    assert str(refused.value) == (
        "BAAI/bge-small-en-v1.5 produces 384-dimensional embeddings but EMBEDDING_DIMENSIONS is 768. "
        "Set EMBEDDING_DIMENSIONS=384, or choose a model that produces 768; memories already stored need `--re-embed` "
        "after a change (see docs/embedding_migration.md)."
    )


def test_a_vector_of_the_configured_width_is_accepted(tensorrt_embedder):
    assert tensorrt_embedder._check_width([0.0] * WIDTH) is None


@pytest.mark.parametrize(("texts", "error", "message"), [
    (["fine", 5], TypeError, "cannot embed int (text 1 of 2 is not a string)"),
    ([None], TypeError, "cannot embed NoneType (text 0 of 1 is not a string)"),
    (["fine", "also fine", ""], ValueError, "cannot embed empty text (text 2 of 3 is empty or only whitespace)"),
    (["  \n\t"], ValueError, "cannot embed empty text (text 0 of 1 is empty or only whitespace)"),
], ids=["number", "none", "empty", "whitespace"])
def test_a_text_the_model_cannot_embed_is_refused_by_position_before_any_gpu_work(
    tensorrt_embedder, monkeypatch, texts, error, message,
):
    def no_gpu_work(arrays):
        raise AssertionError("the texts are refused before the engine runs")

    monkeypatch.setattr(tensorrt_embedder._engine, "infer", no_gpu_work)

    with pytest.raises(error) as refused:
        tensorrt_embedder.embed(texts)

    assert str(refused.value) == message


@pytest.mark.parametrize("value", [np.nan, 0.0], ids=["not-a-number", "zero-vector"])
def test_an_output_without_a_usable_vector_is_refused_and_numpy_says_nothing(tensorrt_embedder, monkeypatch, value):
    """A hidden state of zeros divides to NaN; the refusal is the only report, not a warning of the division."""
    def hidden_state(arrays):
        batch, length = arrays["input_ids"].shape
        return np.full((batch, length, WIDTH), value, dtype=np.float32)

    monkeypatch.setattr(tensorrt_embedder._engine, "infer", hidden_state)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with pytest.raises(TensorRTError) as refused:
            tensorrt_embedder.embed(["text"])

    assert str(refused.value) == "the TensorRT embedder produced non-finite embeddings"


def test_an_engine_that_can_serve_no_more_says_why_in_one_line(tensorrt_embedder, monkeypatch):
    assert tensorrt_embedder.unusable_reason() == ""

    monkeypatch.setattr(tensorrt_embedder._engine, "_failure", "cudaErrorIllegalAddress")

    assert tensorrt_embedder.unusable_reason() == (
        "the embedder engine is unusable after an earlier failure (cudaErrorIllegalAddress); restart the server"
    )


# --------------------------------------------------------------------------
# The vectors taken from the model output
# --------------------------------------------------------------------------
def test_the_vector_of_a_text_is_the_first_token_of_the_hidden_state_scaled_to_length_one(
    tensorrt_embedder, monkeypatch,
):
    requested = []

    class Noting(np.ndarray):
        """A hidden state that notes the type it is asked to become."""

        def astype(self, dtype, *args, **kwargs):
            requested.append(dtype)
            return super().astype(dtype, *args, **kwargs)

    hidden = np.random.default_rng(7).normal(size=(3, 4, WIDTH)).astype(np.float32)
    monkeypatch.setattr(tensorrt_embedder._engine, "infer", lambda arrays: hidden.view(Noting))

    vectors = tensorrt_embedder.embed(["first", "second", "third"])

    first = hidden[:, 0, :].astype(np.float64)
    assert vectors == (first / np.linalg.norm(first, axis=1, keepdims=True)).tolist()
    assert requested[:1] == [np.float64]


def test_every_text_becomes_one_unit_vector_in_order(tensorrt_embedder):
    texts = [
        "The cat sat on the mat.",
        "Quantum chromodynamics describes the strong interaction.",
        "hello",
        "a much longer sentence about nothing in particular " * 8,
        "ünïcödé text ✓",
    ]

    vectors = np.array(tensorrt_embedder.embed(texts))

    assert vectors.shape == (len(texts), WIDTH)
    assert np.linalg.norm(vectors, axis=1) == pytest.approx(1.0, abs=1e-5)
    assert vectors == pytest.approx(np.array([tensorrt_embedder.embed([text])[0] for text in texts]), abs=1e-4)
    assert not np.allclose(vectors[0], vectors[1], atol=1e-2)


def test_no_texts_give_no_vectors_and_no_gpu_work(tensorrt_embedder, monkeypatch):
    sizes = batch_sizes(tensorrt_embedder, monkeypatch)

    assert tensorrt_embedder.embed([]) == []
    assert sizes == []


def test_more_texts_than_the_engine_takes_at_once_are_embedded_in_chunks_of_64_in_order(tensorrt_embedder, monkeypatch):
    texts = [f"sentence {index} about subject {index * 7}" for index in range(130)]
    sizes = batch_sizes(tensorrt_embedder, monkeypatch)

    vectors = tensorrt_embedder.embed(texts)

    assert sizes == [64, 64, 2]
    assert len(vectors) == len(texts)
    for index in (0, 63, 64, 127, 128, 129):
        assert vectors[index] == pytest.approx(tensorrt_embedder.embed([texts[index]])[0], abs=1e-4)


async def test_the_async_calls_return_what_embed_returns(tensorrt_embedder):
    texts = ["first text", "a second and longer text"]

    many = await tensorrt_embedder.generate_embeddings(texts)
    one = await tensorrt_embedder.generate_embedding(texts[1])

    assert np.array(many).shape == (2, WIDTH)
    assert np.array(many) == pytest.approx(np.array(tensorrt_embedder.embed(texts)), abs=1e-6)
    assert np.array(one) == pytest.approx(np.array(tensorrt_embedder.embed([texts[1]])[0]), abs=1e-6)
    assert await tensorrt_embedder.generate_embeddings([]) == []


# --------------------------------------------------------------------------
# One worker thread serves every call, and a close waits for the job that is running
# --------------------------------------------------------------------------
async def test_every_call_is_served_one_at_a_time_by_the_one_worker_thread(tensorrt_embedder, monkeypatch):
    expect_engine(
        monkeypatch, tensorrt_embedder.startup.gpu,
        model_dir=settings.EMBEDDING_MODEL_DIR, cache_dir=settings.EMBEDDING_ENGINE_CACHE_DIR,
    )
    adapter = TensorRTEmbeddingsAdapter()
    entered, first_in, release = [], threading.Event(), threading.Event()
    embed = adapter.embed

    def gated(texts):
        entered.append(threading.current_thread().name)
        first_in.set()
        release.wait(WAIT_SECONDS)
        return embed(texts)

    adapter.embed = gated
    before = set(threading.enumerate())
    try:
        calls = [asyncio.create_task(adapter.generate_embeddings([f"text {index}"])) for index in range(4)]
        await asyncio.sleep(0)  # every call is handed to the pool now, and the first one holds its thread
        started = sorted(t.name for t in set(threading.enumerate()) - before if t.name.startswith("tensorrt-embed"))
        assert await asyncio.to_thread(first_in.wait, WAIT_SECONDS)
        release.set()
        results = await asyncio.gather(*calls)
    finally:
        release.set()
        adapter.close()

    assert started == ["tensorrt-embed_0"]
    assert entered == ["tensorrt-embed_0"] * 4
    assert [[len(vector) for vector in result] for result in results] == [[WIDTH]] * 4


def test_close_waits_for_the_running_job_before_it_releases_the_engine(tensorrt_embedder, monkeypatch):
    expect_engine(
        monkeypatch, tensorrt_embedder.startup.gpu,
        model_dir=settings.EMBEDDING_MODEL_DIR, cache_dir=settings.EMBEDDING_ENGINE_CACHE_DIR,
    )
    adapter = TensorRTEmbeddingsAdapter()
    running, release, closed = threading.Event(), threading.Event(), threading.Event()

    def job():
        running.set()
        release.wait(WAIT_SECONDS)

    future = adapter._executor.submit(job)
    assert running.wait(WAIT_SECONDS)
    closer = threading.Thread(target=lambda: (adapter.close(), closed.set()))
    closer.start()
    try:
        assert not closed.wait(0.3)  # the engine stays open while the job runs
        adapter._engine.verify()
    finally:
        release.set()
        closer.join(WAIT_SECONDS)

    assert closed.is_set()
    assert future.result() is None
    with pytest.raises(TensorRTUnavailableError, match="closed"):
        adapter._engine.verify()
