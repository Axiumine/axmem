"""The TensorRT embeddings adapter on the real GPU: vectors, batching, truncation, refusals, concurrency.

The model is the real bge-small-en-v1.5, run in strict fp32 by TensorRT. The golden vectors and
cosine values were generated once with a CPU fp32 reference of the same ONNX file (ONNX Runtime,
CLS pooling, L2 normalisation); the TensorRT vectors differ from them by about 2e-7 per component,
so the tolerances are small but not bit-exact.
"""
import asyncio
import math
import os
import threading

import numpy as np
import pytest
from cuda.bindings import nvml
from pydantic import ValidationError

from app.config.settings import Settings, settings
from app.exceptions import TensorRTError, TensorRTStartupError
from app.repositories.embeddings import tensorrt_embedder as tensorrt_embedder_module
from app.repositories.embeddings import tensorrt_model
from app.repositories.embeddings.tensorrt_common import ensure_finite
from app.repositories.embeddings.tensorrt_embedder import (
    MAX_BATCH,
    PROFILE,
    TensorRTEmbeddingsAdapter,
)
from app.repositories.embeddings.tensorrt_runtime import (
    cuda_context_handle,
    gpu_memory,
    process_gpu_bytes,
)

WIDTH = 384
TOLERANCE = 1e-5

# The first six components of the vector of each text.
GOLDEN = [
    ("The cat sat on the mat.", [0.031346, -0.010937, 0.003095, 0.066052, -0.036654, -0.004978]),
    ("A kitten rests on a rug.", [-0.006338, 0.011768, 0.077712, 0.119779, -0.005906, -0.00193]),
    ("Quarterly revenue grew 12 percent.", [0.031634, -0.011837, 0.010964, -0.040815, 0.013641, -0.001455]),
    ("Use uv tool install for pinned global CLI tooling",
     [-0.105918, -0.030197, 0.03746, -0.006437, 0.060776, -0.008705]),
    ("x", [-0.028889, 0.008081, 0.121579, -0.022053, -0.019789, 0.031553]),
]

# (text, similar text, unrelated text, cosine to the similar one, cosine to the unrelated one)
SIMILARITY = [
    ("The cat sat on the mat.", "A kitten rests on a rug.", "Quarterly revenue grew 12 percent.", 0.760621, 0.360623),
    ("How do I cache database queries?", "Redis is an in-memory data store used for caching.",
     "Bananas are rich in potassium.", 0.700514, 0.422955),
    ("Paris is the capital of France.", "The capital city of France is Paris.",
     "The Rhone flows into the Mediterranean sea.", 0.982406, 0.66537),
]

TEXTS = [text for text, _ in GOLDEN]


def cosine(first, second):
    return float(np.dot(first, second))


@pytest.fixture(scope="module")
def baseline(tensorrt_embedder):
    return np.array(tensorrt_embedder.embed(TEXTS))


async def test_every_text_gets_one_vector_of_the_configured_width(tensorrt_embedder):
    vectors = await tensorrt_embedder.generate_embeddings(TEXTS)

    assert len(vectors) == len(TEXTS)
    assert all(len(vector) == WIDTH == settings.EMBEDDING_DIMENSIONS for vector in vectors)
    assert all(type(value) is float for vector in vectors for value in vector)


async def test_a_single_text_is_embedded_like_a_batch_of_one(tensorrt_embedder, baseline):
    vector = await tensorrt_embedder.generate_embedding(TEXTS[0])

    assert len(vector) == WIDTH
    assert vector == pytest.approx(baseline[0], abs=TOLERANCE)


def test_vectors_have_unit_length(baseline):
    assert np.linalg.norm(baseline, axis=1) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize(("text", "expected"), GOLDEN)
def test_vectors_match_the_golden_values(baseline, text, expected):
    assert baseline[TEXTS.index(text)][:6] == pytest.approx(expected, abs=TOLERANCE)


@pytest.mark.parametrize(
    ("text", "similar", "unrelated", "similar_cosine", "unrelated_cosine"), SIMILARITY,
    ids=["cat", "caching", "paris"],
)
def test_similar_texts_score_above_unrelated_ones(
    tensorrt_embedder, text, similar, unrelated, similar_cosine, unrelated_cosine,
):
    first, near, far = np.array(tensorrt_embedder.embed([text, similar, unrelated]))

    assert cosine(first, near) == pytest.approx(similar_cosine, abs=1e-4)  # the recorded values
    assert cosine(first, far) == pytest.approx(unrelated_cosine, abs=1e-4)
    assert cosine(first, near) > cosine(first, far) + 0.1


def test_identical_text_gives_identical_vectors_wherever_it_sits_in_a_batch(tensorrt_embedder, baseline):
    padding = ["a much longer sentence about databases and caches and indexes " * 8, "short"]
    first, _, last = (np.array(tensorrt_embedder.embed(batch))[position] for batch, position in (
        ([TEXTS[0], *padding], 0),
        ([*padding, TEXTS[0]], 2),
        ([TEXTS[0]], 0),
    ))

    assert first == pytest.approx(baseline[0], abs=TOLERANCE)
    assert last == pytest.approx(baseline[0], abs=TOLERANCE)
    assert np.array(tensorrt_embedder.embed([TEXTS[0]] * 3)) == pytest.approx(
        np.tile(baseline[0], (3, 1)), abs=TOLERANCE,
    )


def test_embeddings_do_not_depend_on_how_texts_are_batched(tensorrt_embedder, baseline):
    one_by_one = np.array([tensorrt_embedder.embed([text])[0] for text in TEXTS])

    assert one_by_one == pytest.approx(baseline, abs=TOLERANCE)


async def test_no_texts_return_nothing_without_running_the_model(tensorrt_embedder):
    assert await tensorrt_embedder.generate_embeddings([]) == []
    assert tensorrt_embedder.embed([]) == []


@pytest.mark.parametrize("count", [MAX_BATCH - 1, MAX_BATCH, MAX_BATCH + 1, 2 * MAX_BATCH + 5])
async def test_batches_beyond_the_profile_are_split_into_chunks_in_order(tensorrt_embedder, count):
    texts = [f"memory number {index} about the topic {index % 7} of the system" for index in range(count)]

    together = np.array(await tensorrt_embedder.generate_embeddings(texts))
    separately = np.array([
        row for start in range(0, count, 10) for row in tensorrt_embedder.embed(texts[start:start + 10])
    ])

    assert together.shape == (count, WIDTH)
    assert together == pytest.approx(separately, abs=TOLERANCE)  # same texts, same order
    assert len({tuple(row[:8].round(5)) for row in together}) > 1  # the vectors are not copies of each other


def test_a_text_longer_than_512_tokens_is_truncated(tensorrt_embedder):
    long_text = "database caching " * 600
    longer = long_text + "bananas and potassium " * 200

    [truncated], [also_truncated] = tensorrt_embedder.embed([long_text]), tensorrt_embedder.embed([longer])

    assert np.isfinite(truncated).all()
    assert also_truncated == pytest.approx(truncated, abs=1e-6)  # everything past 512 tokens is ignored


def test_the_shortest_possible_text_is_padded_into_the_profile(tensorrt_embedder):
    [vector] = tensorrt_embedder.embed(["."])

    assert len(vector) == WIDTH
    assert math.isclose(np.linalg.norm(vector), 1.0, abs_tol=1e-9)


@pytest.mark.parametrize("blank", ["", " ", "\n\t  "])
def test_empty_text_is_refused_with_its_position(tensorrt_embedder, blank):
    with pytest.raises(ValueError, match=r"cannot embed empty text \(text 1 of 3 is empty or only whitespace\)"):
        tensorrt_embedder.embed(["fine", blank, "fine"])

    assert len(tensorrt_embedder.embed(["fine"])[0]) == WIDTH  # nothing was left half done


async def test_empty_text_is_refused_by_the_async_entry_points(tensorrt_embedder):
    with pytest.raises(ValueError, match="cannot embed empty text"):
        await tensorrt_embedder.generate_embedding("")
    with pytest.raises(ValueError, match="cannot embed empty text"):
        await tensorrt_embedder.generate_embeddings(["fine", "   "])


async def test_a_text_that_is_not_a_string_is_refused(tensorrt_embedder):
    with pytest.raises(TypeError, match=r"cannot embed NoneType \(text 1 of 2 is not a string\)"):
        await tensorrt_embedder.generate_embeddings(["fine", None])  # type: ignore[list-item]

    assert len(await tensorrt_embedder.generate_embedding("fine")) == WIDTH


async def test_embedding_runs_on_a_worker_thread_not_the_event_loop(monkeypatch, tensorrt_embedder):
    seen = []
    infer = tensorrt_embedder._engine.infer

    def spy(arrays):
        seen.append(threading.current_thread())
        return infer(arrays)

    monkeypatch.setattr(tensorrt_embedder._engine, "infer", spy)

    await tensorrt_embedder.generate_embedding("which thread runs the engine")

    assert [thread.name for thread in seen] == ["tensorrt-embed_0"]
    assert seen[0] is not threading.main_thread()


async def test_concurrent_calls_keep_their_results_apart(tensorrt_embedder):
    queries = [f"query {index} about {topic}" for index, topic in enumerate(
        ["redis", "postgres", "bananas", "garbage collection", "index scans", "session storage"])]
    expected = [await tensorrt_embedder.generate_embedding(query) for query in queries]

    results = await asyncio.gather(*(tensorrt_embedder.generate_embedding(query) for query in queries * 5))

    for index, result in enumerate(results):
        assert result == pytest.approx(expected[index % len(queries)], abs=TOLERANCE)


@pytest.mark.parametrize("cancel_index", [0, 3], ids=["running", "queued"])
async def test_a_cancelled_call_does_not_block_later_jobs(tensorrt_embedder, cancel_index):
    texts = [f"memory {index} about the topic {index % 5}" for index in range(MAX_BATCH)]
    expected = await tensorrt_embedder.generate_embeddings(texts)
    tasks = [asyncio.create_task(tensorrt_embedder.generate_embeddings(texts)) for _ in range(5)]
    await asyncio.sleep(0)

    tasks[cancel_index].cancel()
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=30)
    later = await asyncio.wait_for(tensorrt_embedder.generate_embeddings(texts), timeout=30)

    assert isinstance(results[cancel_index], asyncio.CancelledError)
    assert all(result == expected for index, result in enumerate(results) if index != cancel_index)
    assert later == expected


async def test_a_failing_job_does_not_block_later_calls(tensorrt_embedder):
    results = await asyncio.gather(
        tensorrt_embedder.generate_embeddings(["fine", ""]),
        tensorrt_embedder.generate_embedding("fine"),
        return_exceptions=True,
    )

    assert isinstance(results[0], ValueError)
    assert len(results[1]) == WIDTH


async def test_embedding_joins_the_running_loop_instead_of_asking_for_a_default(monkeypatch, tensorrt_embedder):
    def no_default_loop():
        raise AssertionError("embedding must use asyncio.get_running_loop()")

    monkeypatch.setattr(asyncio, "get_event_loop", no_default_loop)

    assert len(await tensorrt_embedder.generate_embedding("text")) == WIDTH


async def test_embedding_works_on_a_fresh_loop_in_a_plain_thread(tensorrt_embedder):
    vector = await asyncio.to_thread(asyncio.run, tensorrt_embedder.generate_embedding("text"))

    assert len(vector) == WIDTH


def test_the_engine_is_built_for_batches_of_64_and_sequences_to_512():
    assert PROFILE.min_shape == (1, 2)
    assert PROFILE.opt_shape[0] == 16
    assert PROFILE.max_shape == (64, 512)
    assert MAX_BATCH == 64


def test_non_finite_embeddings_are_refused():
    ensure_finite(np.array([1.0, -2.5]), "embedder", "embeddings")

    for bad in (np.nan, np.inf, -np.inf):
        with pytest.raises(TensorRTError, match="the TensorRT embedder produced non-finite embeddings"):
            ensure_finite(np.array([1.0, bad]), "embedder", "embeddings")


# --------------------------------------------------------------------------
# Start-up: the warm-up report, the width check, the model files, one CUDA context
# --------------------------------------------------------------------------
def test_startup_report_says_what_the_warm_up_did(tensorrt_embedder):
    report = tensorrt_embedder.startup

    assert report.role == "embedder"
    assert report.engine_dir.is_dir()
    assert report.cache_hit or report.reason
    assert not report.tf32
    assert report.workers == 1
    assert report.cuda_context == cuda_context_handle()
    assert ("cache hit" in report.summary()) == report.cache_hit


def test_startup_summary_names_the_gpu_precision_engine_context_and_memory(tensorrt_embedder):
    report = tensorrt_embedder.startup
    summary = report.summary()

    assert "embedder ready in" in summary
    assert "strict fp32" in summary
    assert report.gpu.name in summary
    assert "TensorRT 11." in summary
    assert str(report.engine_dir) in summary
    assert f"CUDA context {report.cuda_context:#x}" in summary
    assert "this process holds" in summary


def test_the_embedder_and_the_reranker_share_one_cuda_context(tensorrt_embedder, tensorrt_reranker):
    on_the_embedder = tensorrt_embedder._executor.submit(cuda_context_handle).result()
    on_the_reranker = tensorrt_reranker._executor.submit(cuda_context_handle).result()

    assert on_the_embedder == on_the_reranker == cuda_context_handle()
    assert tensorrt_embedder.startup.cuda_context == tensorrt_reranker.startup.cuda_context == on_the_embedder


def test_both_engines_are_counted_in_the_gpu_memory_of_the_process(tensorrt_embedder, tensorrt_reranker):
    memory = gpu_memory()

    assert memory.process_bytes is not None
    assert memory.process_bytes > 3 * 1024**3  # two engines with a full execution context each
    assert memory.process_bytes <= memory.used_bytes <= memory.total_bytes
    assert f"this process holds {memory.process_bytes / 1024**3:.2f} GiB" in memory.describe()


def test_a_start_with_the_wrong_width_is_refused_and_releases_the_engine(tensorrt_embedder):
    held = process_gpu_bytes()

    with pytest.raises(TensorRTStartupError) as raised:
        TensorRTEmbeddingsAdapter(dimensions=768)

    assert raised.value.cause == "dimensions"
    message = str(raised.value)
    assert "BAAI/bge-small-en-v1.5 produces 384-dimensional embeddings but EMBEDDING_DIMENSIONS is 768" in message
    assert "Set EMBEDDING_DIMENSIONS=384" in message
    assert "--re-embed" in message
    assert process_gpu_bytes() == pytest.approx(held, abs=256 * 1024**2)  # the refused engine is not kept


def test_a_failure_after_the_warm_up_refuses_the_start_and_releases_the_engine(tensorrt_embedder, monkeypatch):
    """The report of the start-up reads the CUDA context and the GPU memory; a failure there leaves no engine behind."""
    held = process_gpu_bytes()

    def nvml_gone():
        raise TensorRTError("CUDA call failed: the device went away")

    monkeypatch.setattr(tensorrt_embedder_module, "gpu_memory", nvml_gone)

    with pytest.raises(TensorRTError, match="the device went away") as raised:
        TensorRTEmbeddingsAdapter()

    assert raised.value is not None  # the traceback keeps the half-built adapter alive, so only close() frees its engine
    assert process_gpu_bytes() == pytest.approx(held, abs=256 * 1024**2)


def test_the_configured_width_comes_from_the_settings(tensorrt_embedder):
    assert settings.EMBEDDING_DIMENSIONS == tensorrt_embedder.dimensions == WIDTH


@pytest.mark.parametrize("model", ["", "../etc", "a/b/c", "owner/", "not a repository"])
def test_a_model_that_is_not_a_repository_id_is_refused_naming_the_setting(model, monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", model)

    with pytest.raises(TensorRTStartupError, match="EMBEDDING_MODEL=.* is not a Hugging Face repository id") as raised:
        TensorRTEmbeddingsAdapter()

    assert raised.value.cause == "model-unavailable"


def test_a_populated_model_directory_starts_without_any_download(tensorrt_embedder, tmp_path, monkeypatch):
    """An offline host: the model files were copied over by hand and nothing may be fetched."""
    files = tensorrt_model.fetch_model(settings.EMBEDDING_MODEL, settings.EMBEDDING_MODEL_DIR, "EMBEDDING_MODEL")
    seeded = tmp_path / tensorrt_model.model_slug(settings.EMBEDDING_MODEL)
    (seeded / "onnx").mkdir(parents=True)
    for name, source in ((tensorrt_model.ONNX_FILE, files.onnx), (tensorrt_model.TOKENIZER_FILE, files.tokenizer)):
        (seeded / name).write_bytes(source.read_bytes())

    def no_network(*args, **kwargs):
        raise AssertionError("a populated model directory must not download anything")

    monkeypatch.setattr(tensorrt_model, "hf_hub_download", no_network)
    adapter = TensorRTEmbeddingsAdapter(model_dir=str(tmp_path))
    try:
        assert adapter.startup.cache_hit  # same model bytes, same engine fingerprint
        assert adapter.embed([TEXTS[0]])[0][:6] == pytest.approx(GOLDEN[0][1], abs=TOLERANCE)
    finally:
        adapter.close()


def test_tf32_engines_are_a_separate_build_that_stays_close_to_fp32(tmp_path, baseline):
    adapter = TensorRTEmbeddingsAdapter(tf32=True, cache_dir=str(tmp_path / "engines"))
    try:
        vectors = np.array(adapter.embed(TEXTS))
        assert adapter.tf32
        assert not adapter.startup.cache_hit
        assert "TF32" in adapter.startup.summary()
        assert vectors == pytest.approx(baseline, abs=2e-3)
    finally:
        adapter.close()


def test_a_cuda_error_makes_the_adapter_refuse_further_work(tensorrt_embedder):
    from cuda.bindings import runtime as cudart

    adapter = TensorRTEmbeddingsAdapter()  # a private adapter: this test ruins its engine on purpose
    try:
        assert adapter.embed(TEXTS)
        cudart.cudaMemcpy(0, 0, 4, cudart.cudaMemcpyKind.cudaMemcpyHostToDevice)  # a failing CUDA call

        with pytest.raises(TensorRTError, match="cudaErrorInvalidValue"):
            adapter._engine.verify()  # the guard every embed() ends with
        with pytest.raises(TensorRTError, match="unusable after an earlier failure"):
            adapter.embed(TEXTS)
    finally:
        adapter.close()
        adapter.close()  # closing twice is harmless


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def test_embedding_settings_have_strict_fp32_local_defaults(monkeypatch):
    for name in ("EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS", "EMBEDDING_TF32",
                 "EMBEDDING_MODEL_DIR", "EMBEDDING_ENGINE_CACHE_DIR"):
        monkeypatch.delenv(name, raising=False)
    defaults = Settings(_env_file=None)

    assert defaults.EMBEDDING_PROVIDER == "TensorRT"
    assert defaults.EMBEDDING_MODEL == "BAAI/bge-small-en-v1.5"
    assert defaults.EMBEDDING_DIMENSIONS == 384
    assert defaults.EMBEDDING_TF32 is False
    assert len({defaults.EMBEDDING_MODEL_DIR, defaults.RERANKING_MODEL_DIR}) == 2
    assert defaults.EMBEDDING_MODEL_DIR != defaults.EMBEDDING_ENGINE_CACHE_DIR


def test_tf32_and_directories_are_read_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_TF32", "true")
    monkeypatch.setenv("EMBEDDING_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("EMBEDDING_ENGINE_CACHE_DIR", str(tmp_path / "engines"))
    monkeypatch.setenv("EMBEDDING_MODEL", "Xenova/bge-small-en-v1.5")
    configured = Settings(_env_file=None)

    assert configured.EMBEDDING_TF32 is True
    assert configured.EMBEDDING_MODEL_DIR == str(tmp_path / "models")
    assert configured.EMBEDDING_ENGINE_CACHE_DIR == str(tmp_path / "engines")
    assert configured.EMBEDDING_MODEL == "Xenova/bge-small-en-v1.5"


def test_an_unknown_provider_still_fails_startup(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "TensorRTT")

    with pytest.raises(ValidationError, match="EMBEDDING_PROVIDER"):
        Settings(_env_file=None)


def test_the_embedding_model_is_pinned_to_one_revision_in_both_repositories_that_hold_it():
    assert tensorrt_model.PINNED_REVISIONS["BAAI/bge-small-en-v1.5"] == "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
    assert tensorrt_model.PINNED_REVISIONS["Xenova/bge-small-en-v1.5"] == "ea104dacec62c0de699686887e3f920caeb4f3e3"


def test_the_fetched_embedding_model_is_the_pinned_original_export(tensorrt_embedder):
    files = tensorrt_model.fetch_model(settings.EMBEDDING_MODEL, settings.EMBEDDING_MODEL_DIR, "EMBEDDING_MODEL")

    assert files.onnx_sha256 == "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35"
    assert tensorrt_model.sha256_of(files.tokenizer) == "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"


class FakeProcess:
    def __init__(self, pid, used):
        self.pid, self.used_gpu_memory = pid, used


def device_count():
    nvml.init_v2()
    try:
        return nvml.device_get_count_v2()
    finally:
        nvml.shutdown()


def test_the_process_memory_is_what_nvml_lists_for_this_pid_on_every_device(monkeypatch):
    monkeypatch.setattr(
        nvml, "device_get_compute_running_processes_v3",
        lambda handle: [FakeProcess(os.getpid() + 1, 7), FakeProcess(os.getpid(), 5 * 1024**3)],
    )

    assert process_gpu_bytes() == 5 * 1024**3 * device_count()


def test_nvml_that_cannot_report_a_process_gives_no_number(monkeypatch):
    monkeypatch.setattr(
        nvml, "device_get_compute_running_processes_v3", lambda handle: [FakeProcess(os.getpid(), 2**64 - 1)],
    )
    assert process_gpu_bytes() is None  # NVML's "not available" marker

    monkeypatch.setattr(
        nvml, "device_get_compute_running_processes_v3", lambda handle: [FakeProcess(os.getpid() + 1, 1)],
    )
    assert process_gpu_bytes() is None  # this pid is not in the list (another pid namespace)

    def refuse(handle):
        raise nvml.NvmlError(3)

    monkeypatch.setattr(nvml, "device_get_compute_running_processes_v3", refuse)
    assert process_gpu_bytes() is None  # the query itself failed

    def uninitialised():
        raise nvml.NvmlError(1)

    monkeypatch.setattr(nvml, "init_v2", uninitialised)
    assert process_gpu_bytes() is None
