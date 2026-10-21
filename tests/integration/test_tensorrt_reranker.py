"""The TensorRT reranker adapter on the real GPU: scores, ordering, batching, concurrency, failures.

The model is the real cross-encoder, run in strict fp32 by TensorRT. The golden scores were
generated once on the development GPU (RTX PRO 4000 Blackwell, TensorRT 11.4, strict fp32); a CPU
fp32 reference agrees with them to about 1e-5, so the tolerance is small but not bit-exact.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from cuda.bindings import runtime as cudart
from pydantic import ValidationError

from app.bootstrap import operator_failure
from app.config.settings import Settings, settings
from app.exceptions import (
    EmbeddingUnavailableError,
    ModelUnavailableError,
    TensorRTError,
    TensorRTUnavailableError,
)
from app.repositories.embeddings import tensorrt_reranker as tensorrt_reranker_module
from app.repositories.embeddings.tensorrt_common import ensure_finite
from app.repositories.embeddings.tensorrt_reranker import (
    MAX_BATCH,
    TensorRTRerankAdapter,
)
from app.repositories.embeddings.tensorrt_runtime import process_gpu_bytes

TOLERANCE = 2e-3

GOLDEN = [
    ("how many people live in berlin",
     "Berlin had a population of 3,520,031 registered inhabitants in an area of 891.82 square kilometres.",
     9.2594),
    ("how many people live in berlin", "Berlin is well known for its museums and its nightlife.", -2.8715),
    ("what is the capital of france", "Paris is the capital and most populous city of France.", 8.3252),
    ("what is the capital of france", "The Rhone flows from Switzerland through France to the Mediterranean.", -9.5446),
    ("python list comprehension",
     "A list comprehension builds a list from an iterable in a single expression.", 4.3268),
    ("python list comprehension", "Pythons are large non-venomous snakes found in Africa and Asia.", -4.5927),
    ("redis", "Redis is an in-memory data store used for caching.", 9.0325),
    ("", "", 8.1541),
]

QUERY = "which database is good for caching"
DOCUMENTS = [
    "Redis is an in-memory data store used for caching and session storage.",
    "PostgreSQL is a relational database with ACID transactions.",
    "Memcached is a distributed memory caching system.",
    "Rust offers memory safety without garbage collection.",
    "A cache stores copies of data so that later requests are served faster.",
    "SQLite is an embedded SQL database engine.",
    "Bananas are rich in potassium.",
    "The query planner chooses between index scans and sequential scans.",
]


@pytest.fixture(scope="module")
def baseline(tensorrt_reranker):
    return np.array(tensorrt_reranker.score(QUERY, DOCUMENTS))


@pytest.mark.parametrize(("query", "document", "expected"), GOLDEN)
def test_scores_match_the_golden_values(tensorrt_reranker, query, document, expected):
    [score] = tensorrt_reranker.score(query, [document])

    assert score == pytest.approx(expected, abs=TOLERANCE)


def test_scores_do_not_depend_on_how_pairs_are_batched(tensorrt_reranker, baseline):
    one_by_one = [tensorrt_reranker.score(QUERY, [document])[0] for document in DOCUMENTS]

    assert one_by_one == pytest.approx(baseline, abs=1e-3)


async def test_rerank_returns_indexes_and_scores_best_first(tensorrt_reranker, baseline):
    ranked = await tensorrt_reranker.rerank(QUERY, DOCUMENTS)

    assert [index for index, _ in ranked] == list(np.argsort(-baseline, kind="stable"))
    assert [score for _, score in ranked] == pytest.approx(sorted(baseline, reverse=True), abs=1e-3)
    assert ranked[0][0] == 0
    assert all(isinstance(index, int) and isinstance(score, float) for index, score in ranked)


async def test_equal_scores_keep_the_input_order(tensorrt_reranker):
    ranked = await tensorrt_reranker.rerank("redis", ["same text"] * 4)

    assert [index for index, _ in ranked] == [0, 1, 2, 3]


async def test_no_documents_return_nothing_without_running_the_model(tensorrt_reranker):
    assert await tensorrt_reranker.rerank("anything", []) == []
    assert tensorrt_reranker.score("anything", []) == []


@pytest.mark.parametrize("count", [MAX_BATCH - 1, MAX_BATCH, MAX_BATCH + 1, 2 * MAX_BATCH + 5])
def test_batches_beyond_the_profile_are_split_into_chunks(tensorrt_reranker, count):
    documents = [f"{DOCUMENTS[index % len(DOCUMENTS)]} number {index}" for index in range(count)]

    together = tensorrt_reranker.score(QUERY, documents)
    separately = [score for start in range(0, count, 20) for score in tensorrt_reranker.score(QUERY, documents[start:start + 20])]

    assert len(together) == count
    assert together == pytest.approx(separately, abs=1e-3)


def test_a_pair_longer_than_512_tokens_is_truncated(tensorrt_reranker):
    long_document = "database caching " * 600

    [truncated] = tensorrt_reranker.score(QUERY, [long_document])
    [longer] = tensorrt_reranker.score(QUERY, [long_document + "bananas and potassium " * 200])

    assert np.isfinite(truncated)
    assert longer == pytest.approx(truncated, abs=1e-4)  # everything past 512 tokens is ignored


def test_the_shortest_possible_pair_is_padded_into_the_profile(tensorrt_reranker):
    [score] = tensorrt_reranker.score("", [""])

    assert np.isfinite(score)


def test_startup_report_says_what_the_warm_up_did(tensorrt_reranker):
    report = tensorrt_reranker.startup

    assert report.engine_dir.is_dir()
    assert report.cache_hit or report.reason
    assert not report.tf32
    assert report.workers == 1
    assert ("cache hit" in report.summary()) == report.cache_hit


def test_startup_summary_names_the_gpu_precision_and_engine(tensorrt_reranker):
    report = tensorrt_reranker.startup
    summary = report.summary()

    assert "strict fp32" in summary
    assert report.gpu.name in summary
    assert "GiB)" in summary
    assert "TensorRT 11." in summary
    assert str(report.engine_dir) in summary


def test_tf32_engines_are_a_separate_build_that_stays_close_to_fp32(tmp_path, baseline):
    adapter = TensorRTRerankAdapter(tf32=True, cache_dir=str(tmp_path / "engines"))
    try:
        scores = np.array(adapter.score(QUERY, DOCUMENTS))
        assert adapter.tf32
        assert not adapter.startup.cache_hit
        assert "TF32" in adapter.startup.summary()
        assert scores == pytest.approx(baseline, abs=0.1)
    finally:
        adapter.close()


@pytest.fixture(scope="module")
def two_workers(tensorrt_reranker):
    adapter = TensorRTRerankAdapter(workers=2)  # same fingerprint as the session engine: a cache hit
    yield adapter
    adapter.close()


def test_two_workers_load_the_engine_built_for_one(two_workers):
    assert two_workers.startup.cache_hit
    assert two_workers.startup.workers == 2


def test_threads_scoring_at_once_get_their_own_correct_results(two_workers):
    queries = [f"{QUERY} {index}" for index in range(8)]
    expected = [two_workers.score(query, DOCUMENTS) for query in queries]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda query: two_workers.score(query, DOCUMENTS), queries * 6))

    for index, result in enumerate(results):
        assert result == pytest.approx(expected[index % len(queries)], abs=1e-5)


async def test_concurrent_rerank_calls_keep_their_results_apart(two_workers):
    queries = ["redis", "postgres", "bananas", "garbage collection", "index scans", "session storage"]
    expected = [await two_workers.rerank(query, DOCUMENTS) for query in queries]

    results = await asyncio.gather(*(two_workers.rerank(query, DOCUMENTS) for query in queries * 5))

    for index, result in enumerate(results):
        assert [i for i, _ in result] == [i for i, _ in expected[index % len(queries)]]


@pytest.mark.parametrize("cancel_index", [0, 3], ids=["running", "queued"])
async def test_a_cancelled_rerank_does_not_block_later_jobs(two_workers, cancel_index):
    documents = [f"{DOCUMENTS[index % len(DOCUMENTS)]} {index}" for index in range(MAX_BATCH)]
    expected = await two_workers.rerank(QUERY, documents)
    tasks = [asyncio.create_task(two_workers.rerank(QUERY, documents)) for _ in range(5)]
    await asyncio.sleep(0)

    tasks[cancel_index].cancel()
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=30)
    later = await asyncio.wait_for(two_workers.rerank(QUERY, documents), timeout=30)

    assert isinstance(results[cancel_index], asyncio.CancelledError)
    assert all(result == expected for index, result in enumerate(results) if index != cancel_index)
    assert later == expected


async def test_a_failing_job_does_not_block_later_reranks(tensorrt_reranker):
    failing = tensorrt_reranker.rerank(QUERY, [None])  # a document that cannot be tokenized

    results = await asyncio.gather(
        failing, tensorrt_reranker.rerank(QUERY, DOCUMENTS), return_exceptions=True,
    )

    assert isinstance(results[0], TypeError)
    assert results[1][0][0] == 0


async def test_rerank_joins_the_running_loop_instead_of_asking_for_a_default(monkeypatch, tensorrt_reranker):
    def no_default_loop():
        raise AssertionError("rerank must use asyncio.get_running_loop()")

    monkeypatch.setattr(asyncio, "get_event_loop", no_default_loop)

    ranked = await tensorrt_reranker.rerank(QUERY, DOCUMENTS)

    assert ranked[0][0] == 0


async def test_rerank_works_on_a_fresh_loop_in_a_plain_thread(tensorrt_reranker):
    ranked = await asyncio.to_thread(asyncio.run, tensorrt_reranker.rerank(QUERY, DOCUMENTS))

    assert ranked[0][0] == 0


def test_non_finite_scores_are_refused():
    ensure_finite(np.array([1.0, -2.5], dtype=np.float32), "reranker", "scores")

    for bad in (np.nan, np.inf, -np.inf):
        with pytest.raises(TensorRTError, match="non-finite"):
            ensure_finite(np.array([1.0, bad], dtype=np.float32), "reranker", "scores")


def test_a_cuda_error_makes_the_adapter_refuse_further_work(tensorrt_reranker):
    adapter = TensorRTRerankAdapter()  # a private adapter: this test ruins its engine on purpose
    try:
        assert adapter.score(QUERY, DOCUMENTS)
        cudart.cudaMemcpy(0, 0, 4, cudart.cudaMemcpyKind.cudaMemcpyHostToDevice)  # a failing CUDA call

        with pytest.raises(TensorRTError, match="cudaErrorInvalidValue"):
            adapter._engine.verify()  # the guard every score() ends with
        with pytest.raises(TensorRTError, match="unusable after an earlier failure") as refused:
            adapter.score(QUERY, DOCUMENTS)
        # the reranker's engine raises the error the embedder's does: neither is an embeddings provider
        assert isinstance(refused.value, TensorRTUnavailableError)
        assert isinstance(refused.value, ModelUnavailableError)
        assert not isinstance(refused.value, EmbeddingUnavailableError)
        line = operator_failure(refused.value)
        assert line is not None
        assert "embeddings provider" not in line
    finally:
        adapter.close()
        adapter.close()  # closing twice is harmless


def test_a_failed_warm_up_refuses_the_start_and_releases_the_engine(tensorrt_reranker):
    """A CUDA error is pending on this thread, which the guard at the end of the warm-up finds."""
    held = process_gpu_bytes()
    cudart.cudaMemcpy(0, 0, 4, cudart.cudaMemcpyKind.cudaMemcpyHostToDevice)  # a failing CUDA call

    with pytest.raises(TensorRTError, match="cudaErrorInvalidValue") as raised:
        TensorRTRerankAdapter()

    assert raised.value is not None  # the traceback keeps the half-built adapter alive, so only close() frees its engine
    assert process_gpu_bytes() == pytest.approx(held, abs=256 * 1024**2)


def test_a_failure_after_the_warm_up_refuses_the_start_and_releases_the_engine(tensorrt_reranker, monkeypatch):
    """The report of the start-up reads the CUDA context and the GPU memory; a failure there leaves no engine behind."""
    held = process_gpu_bytes()

    def nvml_gone():
        raise TensorRTError("CUDA call failed: the device went away")

    monkeypatch.setattr(tensorrt_reranker_module, "gpu_memory", nvml_gone)

    with pytest.raises(TensorRTError, match="the device went away") as raised:
        TensorRTRerankAdapter()

    assert raised.value is not None
    assert process_gpu_bytes() == pytest.approx(held, abs=256 * 1024**2)


def test_reranking_settings_have_strict_fp32_defaults(monkeypatch):
    for name in ("RERANKING_TF32", "RERANKING_WORKERS", "RERANKING_PROVIDER", "RERANKING_MODEL_DIR",
                 "RERANKING_ENGINE_CACHE_DIR"):
        monkeypatch.delenv(name, raising=False)
    defaults = Settings(_env_file=None)

    assert defaults.RERANKING_PROVIDER == "TensorRT"
    assert defaults.RERANKING_TF32 is False
    assert defaults.RERANKING_WORKERS == 1
    assert defaults.RERANKING_MODEL_DIR != defaults.RERANKING_ENGINE_CACHE_DIR
    assert not hasattr(defaults, "RERANKING_ONNX_PROVIDERS")
    assert not hasattr(defaults, "RERANKING_THREADS")


def test_tf32_and_directories_are_read_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("RERANKING_TF32", "true")
    monkeypatch.setenv("RERANKING_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("RERANKING_ENGINE_CACHE_DIR", str(tmp_path / "engines"))
    configured = Settings(_env_file=None)

    assert configured.RERANKING_TF32 is True
    assert configured.RERANKING_MODEL_DIR == str(tmp_path / "models")
    assert configured.RERANKING_ENGINE_CACHE_DIR == str(tmp_path / "engines")


@pytest.mark.parametrize("value", ["0", "-1", "1.5"])
def test_rerank_worker_count_rejects_invalid_limits(monkeypatch, value):
    monkeypatch.setenv("RERANKING_WORKERS", value)

    with pytest.raises(ValidationError, match="RERANKING_WORKERS"):
        Settings(_env_file=None)


def test_adapter_defaults_come_from_the_settings(tensorrt_reranker):
    assert settings.RERANKING_WORKERS == tensorrt_reranker.workers
    assert settings.RERANKING_TF32 == tensorrt_reranker.tf32
