# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The TensorRT reranker, call for call: its start-up report and warm-up, the arguments that win over the settings, the
scores it takes from the model output, the order of a ranking, what a model output without scores says, and the worker
threads that serve every call.

The engine is the real one on the real GPU. Where a test needs an output the model cannot be made to give (NaN, a wider
logit row, a float64 array, scores it knows), it replaces the ``infer`` of the engine of the reranker; everything around
it is the production code.
"""
import asyncio
import json
import logging
import shutil
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from app.config.settings import settings
from app.exceptions import TensorRTError, TensorRTStartupError, TensorRTUnavailableError
from app.repositories.embeddings import tensorrt_model
from app.repositories.embeddings import tensorrt_reranker as reranker_module
from app.repositories.embeddings.tensorrt_reranker import TensorRTRerankAdapter

WAIT_SECONDS = 10
SLUG = "Xenova--ms-marco-MiniLM-L-12-v2"


def no_network(*args, **kwargs):
    raise AssertionError("the model files are in place, so nothing may be downloaded")


def recorded_digest(model_root: Path) -> str:
    manifest = json.loads((model_root / tensorrt_model.MANIFEST_FILE).read_text())
    return manifest["sha256"][tensorrt_model.ONNX_FILE]


def expect_engine(monkeypatch, gpu, *, model_dir, cache_dir, tf32=False, contexts=1):
    """Wrap ``open_engine``: a start-up that hands it other arguments fails before an engine is built."""
    real, model_root = reranker_module.open_engine, Path(model_dir) / SLUG
    expected = {
        "onnx_path": model_root / tensorrt_model.ONNX_FILE,
        "model_sha256": recorded_digest(model_root),
        "group_name": SLUG,
        "cache_dir": Path(cache_dir),
        "gpu": gpu,
        "tf32": tf32,
        "profile": reranker_module.PROFILE,
        "contexts": contexts,
    }

    def checking(**kwargs):
        for name, value in expected.items():
            assert kwargs[name] == value, name
        return real(**kwargs)

    monkeypatch.setattr(reranker_module, "open_engine", checking)


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


def no_gpu_work(arrays):
    raise AssertionError("there is nothing to score, so the engine must not run")


# --------------------------------------------------------------------------
# What the start-up does and reports
# --------------------------------------------------------------------------
def test_a_start_scores_one_warm_up_pair_reports_what_it_did_and_logs_the_same_line(
    tensorrt_reranker, monkeypatch, caplog,
):
    monkeypatch.setattr(reranker_module.logger, "disabled", False)  # the logging set-up of an app that started earlier disables it
    caplog.set_level(logging.INFO, logger=reranker_module.logger.name)
    expect_engine(
        monkeypatch, tensorrt_reranker.startup.gpu,
        model_dir=settings.RERANKING_MODEL_DIR, cache_dir=settings.RERANKING_ENGINE_CACHE_DIR,
        contexts=settings.RERANKING_WORKERS,
    )
    fetched = spied(monkeypatch, reranker_module, "fetch_model")
    slugged = spied(monkeypatch, reranker_module, "model_slug")
    warm_ups, score = [], TensorRTRerankAdapter.score

    def recording(self, query, documents):
        warm_ups.append((query, list(documents)))
        return score(self, query, documents)

    monkeypatch.setattr(TensorRTRerankAdapter, "score", recording)
    before = time.perf_counter()
    adapter = TensorRTRerankAdapter()
    elapsed = time.perf_counter() - before
    try:
        report = adapter.startup

        assert warm_ups == [("warm-up query", ["warm-up document"])]
        assert (report.role, report.cache_hit, report.reason, report.tf32, report.workers) == (
            "reranker", True, "cache hit", False, settings.RERANKING_WORKERS,
        )
        assert 0 < report.seconds <= elapsed
        assert (report.engine_dir, report.gpu) == (tensorrt_reranker.startup.engine_dir, tensorrt_reranker.startup.gpu)
        assert fetched == [((settings.RERANKING_MODEL, settings.RERANKING_MODEL_DIR, "RERANKING_MODEL"), {})]
        assert slugged == [((settings.RERANKING_MODEL, "RERANKING_MODEL"), {})]
        assert (adapter.tf32, adapter.workers) == (False, settings.RERANKING_WORKERS)
        assert [r.getMessage() for r in caplog.records if r.name == reranker_module.logger.name] == [report.summary()]
    finally:
        adapter.close()


def test_the_arguments_win_over_the_settings(tensorrt_reranker, tmp_path, monkeypatch):
    """The settings point at nothing usable, so only an adapter that takes its arguments can start."""
    models, model, report = settings.RERANKING_MODEL_DIR, settings.RERANKING_MODEL, tensorrt_reranker.startup
    seeded = tmp_path / "engines" / report.engine_dir.parent.name / report.engine_dir.name
    shutil.copytree(report.engine_dir, seeded)
    for name, value in {
        "RERANKING_MODEL": "not a repository",
        "RERANKING_WORKERS": 1,
        "RERANKING_TF32": True,
        "RERANKING_MODEL_DIR": str(tmp_path / "models-of-the-settings"),
        "RERANKING_ENGINE_CACHE_DIR": str(tmp_path / "engines-of-the-settings"),
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(tensorrt_model, "hf_hub_download", no_network)
    expect_engine(monkeypatch, report.gpu, model_dir=models, cache_dir=tmp_path / "engines", tf32=False, contexts=2)
    fetched = spied(monkeypatch, reranker_module, "fetch_model")
    slugged = spied(monkeypatch, reranker_module, "model_slug")

    adapter = TensorRTRerankAdapter(
        model=model, workers=2, tf32=False, model_dir=models, cache_dir=str(tmp_path / "engines"),
    )
    try:
        assert (adapter.workers, adapter.tf32, adapter.startup.workers) == (2, False, 2)
        assert fetched == [((model, models, "RERANKING_MODEL"), {})]
        assert slugged == [((model, "RERANKING_MODEL"), {})]
        assert adapter.startup.engine_dir == seeded
        assert adapter.startup.cache_hit
        assert len(adapter.score("query", ["document"])) == 1
    finally:
        adapter.close()


def test_a_model_that_is_no_repository_id_is_refused_naming_the_setting(tmp_path):
    with pytest.raises(TensorRTStartupError) as refused:
        TensorRTRerankAdapter(model="not a repository", model_dir=str(tmp_path), cache_dir=str(tmp_path))

    assert refused.value.cause == "model-unavailable"
    assert str(refused.value) == (
        "RERANKING_MODEL='not a repository' is not a Hugging Face repository id such as 'owner/name', "
        "which the TensorRT models need in order to fetch onnx/model.onnx and tokenizer.json."
    )


# --------------------------------------------------------------------------
# The scores taken from what the model outputs
# --------------------------------------------------------------------------
def test_the_score_of_a_pair_is_the_first_logit_of_its_row_as_a_float32(tensorrt_reranker, monkeypatch):
    reshapes = []

    class Noting(np.ndarray):
        """A model output that notes the shape it is reshaped to."""

        def reshape(self, *shape, **kwargs):
            reshapes.append(shape)
            return super().reshape(*shape, **kwargs)

    def logits(arrays):
        batch = arrays["input_ids"].shape[0]
        return np.array([[0.1 + row, -1.0, -2.0] for row in range(batch)], dtype=np.float64).view(Noting)

    monkeypatch.setattr(tensorrt_reranker._engine, "infer", logits)

    scores = tensorrt_reranker.score("query", ["first", "second", "third"])

    assert scores == [float(np.float32(0.1)), float(np.float32(1.1)), float(np.float32(2.1))]
    assert reshapes == [(3, -1)]


def test_the_real_model_scores_one_pair_per_document_in_input_order(tensorrt_reranker):
    query = "What is the capital of France?"
    documents = ["Bananas are yellow and rich in potassium.", "Paris is the capital and largest city of France."]

    scores = tensorrt_reranker.score(query, documents)

    assert len(scores) == 2
    assert scores[1] > scores[0]
    assert [tensorrt_reranker.score(query, [document])[0] for document in documents] == pytest.approx(scores, abs=1e-3)


def test_no_documents_give_an_empty_float32_array_to_the_finite_check(tensorrt_reranker, monkeypatch):
    seen = []
    ensure_finite = reranker_module.ensure_finite

    def recording(values, role, what):
        seen.append((values.dtype, values.shape, role, what))
        ensure_finite(values, role, what)

    monkeypatch.setattr(reranker_module, "ensure_finite", recording)
    monkeypatch.setattr(tensorrt_reranker._engine, "infer", no_gpu_work)

    assert tensorrt_reranker.score("query", []) == []
    assert seen == [(np.dtype("float32"), (0,), "reranker", "scores")]


def test_more_documents_than_the_engine_takes_at_once_are_scored_in_chunks_of_64_in_order(
    tensorrt_reranker, monkeypatch,
):
    documents = [f"document {index} about subject {index * 7}" for index in range(130)]
    sizes = batch_sizes(tensorrt_reranker, monkeypatch)

    scores = tensorrt_reranker.score("a query about subject 21", documents)

    assert sizes == [64, 64, 2]
    assert len(scores) == len(documents)
    for index in (0, 63, 64, 127, 128, 129):
        assert scores[index] == pytest.approx(tensorrt_reranker.score("a query about subject 21", [documents[index]])[0], abs=1e-3)


def test_a_model_output_without_numbers_is_refused_naming_the_reranker(tensorrt_reranker, monkeypatch):
    def not_numbers(arrays):
        return np.full((arrays["input_ids"].shape[0], 1), np.nan, dtype=np.float32)

    monkeypatch.setattr(tensorrt_reranker._engine, "infer", not_numbers)

    with pytest.raises(TensorRTError) as refused:
        tensorrt_reranker.score("query", ["document"])

    assert str(refused.value) == "the TensorRT reranker produced non-finite scores"


def test_an_engine_that_can_serve_no_more_says_why_in_one_line(tensorrt_reranker, monkeypatch):
    assert tensorrt_reranker.unusable_reason() == ""

    monkeypatch.setattr(tensorrt_reranker._engine, "_failure", "cudaErrorIllegalAddress")

    assert tensorrt_reranker.unusable_reason() == (
        "the reranker engine is unusable after an earlier failure (cudaErrorIllegalAddress); restart the server"
    )


# --------------------------------------------------------------------------
# The ranking
# --------------------------------------------------------------------------
async def test_a_ranking_lists_the_index_and_score_of_every_document_best_first(tensorrt_reranker, monkeypatch):
    monkeypatch.setattr(
        tensorrt_reranker._engine, "infer", lambda arrays: np.array([[0.5], [2.5], [-1.0], [1.5]], dtype=np.float32),
    )

    ranked = await tensorrt_reranker.rerank("query", ["a", "b", "c", "d"])

    assert ranked == [(1, 2.5), (3, 1.5), (0, 0.5), (2, -1.0)]


async def test_the_real_model_ranks_the_document_that_answers_the_query_first(tensorrt_reranker):
    query = "What is the capital of France?"
    documents = [
        "Bananas are yellow and rich in potassium.",
        "Paris is the capital and largest city of France.",
        "The stock market closed lower on Tuesday.",
    ]

    ranked = await tensorrt_reranker.rerank(query, documents)

    assert next(index for index, _ in ranked) == 1
    assert sorted(index for index, _ in ranked) == [0, 1, 2]
    assert [score for _, score in ranked] == sorted((score for _, score in ranked), reverse=True)
    assert dict(ranked) == pytest.approx(dict(enumerate(tensorrt_reranker.score(query, documents))), abs=1e-3)


async def test_no_documents_give_no_ranking_and_no_gpu_work(tensorrt_reranker, monkeypatch):
    monkeypatch.setattr(tensorrt_reranker._engine, "infer", no_gpu_work)

    assert await tensorrt_reranker.rerank("query", []) == []


# --------------------------------------------------------------------------
# The worker threads serve the calls, and a close waits for the job that is running
# --------------------------------------------------------------------------
async def test_every_call_is_served_one_at_a_time_by_the_one_worker_thread(tensorrt_reranker, monkeypatch):
    expect_engine(
        monkeypatch, tensorrt_reranker.startup.gpu,
        model_dir=settings.RERANKING_MODEL_DIR, cache_dir=settings.RERANKING_ENGINE_CACHE_DIR,
    )
    adapter = TensorRTRerankAdapter()
    entered, first_in, release = [], threading.Event(), threading.Event()
    rerank_sync = adapter._rerank_sync

    def gated(query, documents):
        entered.append(threading.current_thread().name)
        first_in.set()
        release.wait(WAIT_SECONDS)
        return rerank_sync(query, documents)

    adapter._rerank_sync = gated
    before = set(threading.enumerate())
    try:
        calls = [asyncio.create_task(adapter.rerank("query", [f"document {index}"])) for index in range(4)]
        await asyncio.sleep(0)  # every call is handed to the pool now, and the first one holds its thread
        started = sorted(t.name for t in set(threading.enumerate()) - before if t.name.startswith("tensorrt-rerank"))
        assert await asyncio.to_thread(first_in.wait, WAIT_SECONDS)
        release.set()
        results = await asyncio.gather(*calls)
    finally:
        release.set()
        adapter.close()

    assert started == ["tensorrt-rerank_0"]
    assert entered == ["tensorrt-rerank_0"] * 4
    assert [[index for index, _ in result] for result in results] == [[0]] * 4


def test_close_waits_for_the_running_job_before_it_releases_the_engine(tensorrt_reranker, monkeypatch):
    expect_engine(
        monkeypatch, tensorrt_reranker.startup.gpu,
        model_dir=settings.RERANKING_MODEL_DIR, cache_dir=settings.RERANKING_ENGINE_CACHE_DIR,
    )
    adapter = TensorRTRerankAdapter()
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
