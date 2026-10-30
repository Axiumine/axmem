# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""A server that cannot run the TensorRT embedder or reranker refuses to start, says why, and never falls back.

Each case starts the real program in a subprocess whose environment really lacks something:
no visible GPU, TensorRT / CUDA libraries that cannot be imported, a model that is not there or
an embedding width the model does not produce. Both models are enabled (the defaults) and both
providers are TensorRT, so the only way to start would be a fallback. A case that is about the
reranker alone selects another embeddings provider (one that needs no GPU and no network to be
built), and the other way round.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.qdrant_support import drop_prefix, unique_prefix

ROOT = Path(__file__).resolve().parents[2]

# Make a package undiscoverable the way a missing library is: importing it raises ImportError.
HIDE_AND_RUN = textwrap.dedent(
    """
    import importlib.abc
    import sys

    class Hidden(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in {hidden!r}:
                raise ModuleNotFoundError(f"No module named {{name!r}} (hidden by the test)", name=name)

    sys.meta_path.insert(0, Hidden())
    sys.argv = ["axmem", *{argv!r}]
    import main
    raise SystemExit(main.cli())
    """,
)


def run_program(code=None, argv=None, stdin=None, **env):
    environment = {
        **os.environ,
        "AUTH_MODE": "none",
        "EMBEDDING_PROVIDER": "TensorRT",
        "RERANKING_ENABLED": "true",
        "RERANKING_PROVIDER": "TensorRT",
        **env,
    }
    command = [sys.executable, "-c", code] if code else [sys.executable, "main.py", *argv]
    return subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
        command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120, check=False, stdin=stdin,
    )


TENSORRT_HIDDEN = ["tensorrt", "tensorrt_bindings", "tensorrt_libs"]


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_serve_without_a_visible_gpu_exits_with_the_cause_of_the_embedder(transport):
    result = run_program(argv=["serve", "--transport", transport, "--host", "127.0.0.1"], CUDA_VISIBLE_DEVICES="-1")

    assert result.returncode == 1
    assert "Error: the embedder cannot start: no NVIDIA GPU is visible to CUDA (cudaErrorNoDevice)" in result.stderr
    assert "no CPU path" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_serve_without_a_visible_gpu_exits_with_the_cause_of_the_reranker(transport):
    result = run_program(
        argv=["serve", "--transport", transport, "--host", "127.0.0.1"],
        CUDA_VISIBLE_DEVICES="-1",
        EMBEDDING_PROVIDER="Ollama",
    )

    assert result.returncode == 1
    assert "Error: the reranker cannot start: no NVIDIA GPU is visible to CUDA (cudaErrorNoDevice)" in result.stderr
    assert "no CPU path" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_the_embedder_needs_the_gpu_even_when_reranking_is_off():
    result = run_program(
        argv=["serve", "--transport", "stdio"], CUDA_VISIBLE_DEVICES="-1", RERANKING_ENABLED="false",
    )

    assert result.returncode == 1
    assert "Error: the embedder cannot start: no NVIDIA GPU is visible to CUDA" in result.stderr


def test_a_cli_command_without_a_visible_gpu_reports_the_same_cause():
    result = run_program(argv=["tools", "list"], CUDA_VISIBLE_DEVICES="-1")

    assert result.returncode == 1
    assert "no NVIDIA GPU is visible to CUDA" in result.stderr + result.stdout


def test_re_embedding_without_a_visible_gpu_reports_the_cause_and_changes_nothing():
    result = run_program(argv=["--re-embed", "--dry-run"], CUDA_VISIBLE_DEVICES="-1")

    assert result.returncode == 1
    assert "Error: the embedder cannot start: no NVIDIA GPU is visible to CUDA" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("hidden", [TENSORRT_HIDDEN, ["cuda"]])
def test_serve_without_importable_tensorrt_libraries_exits_with_the_cause_of_the_embedder(hidden):
    code = HIDE_AND_RUN.format(hidden=set(hidden), argv=["serve", "--transport", "stdio"])

    result = run_program(code=code)

    assert result.returncode == 1
    assert "Error: the embedder cannot start: TensorRT or the CUDA Python bindings cannot be imported" in result.stderr
    assert "hidden by the test" in result.stderr
    assert "EMBEDDING_PROVIDER" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("hidden", [TENSORRT_HIDDEN, ["cuda"]])
def test_serve_without_importable_tensorrt_libraries_exits_with_the_cause_of_the_reranker(hidden):
    code = HIDE_AND_RUN.format(hidden=set(hidden), argv=["serve", "--transport", "stdio"])

    result = run_program(code=code, EMBEDDING_PROVIDER="Ollama")

    assert result.returncode == 1
    assert "Error: the reranker cannot start: TensorRT or the CUDA Python bindings cannot be imported" in result.stderr
    assert "hidden by the test" in result.stderr
    assert "RERANKING_PROVIDER=HTTP" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("reranking", [
    {"RERANKING_ENABLED": "false"},
    {"RERANKING_PROVIDER": "HTTP", "RERANKING_URL": "http://127.0.0.1:9/v1/rerank"},
], ids=["reranking-off", "http-reranker"])
def test_without_the_local_models_no_gpu_is_needed(qdrant_server, reranking):
    """Another embeddings provider and no local reranker: the program starts on a machine with no GPU at all.

    The collections are the test's own: the shared ones hold vectors of the TensorRT embedder, and a start on
    another embedder is refused there (see ``test_a_start_on_another_embedder_than_the_stored_one_is_refused``).
    """
    server, _ = qdrant_server
    prefix = unique_prefix()
    try:
        result = run_program(
            code=HIDE_AND_RUN.format(hidden={*TENSORRT_HIDDEN, "cuda"}, argv=["tools", "list"]),
            EMBEDDING_PROVIDER="Ollama",
            CUDA_VISIBLE_DEVICES="-1",
            **reranking,
            QDRANT_URL=server.url,
            QDRANT_API_KEY=server.api_key,
            QDRANT_COLLECTION_PREFIX=prefix,
        )
    finally:
        drop_prefix(server.url, server.api_key, prefix)

    assert result.returncode == 0
    assert "total_count" in result.stdout


def test_a_start_on_another_embedder_than_the_stored_one_is_refused(qdrant_server):
    """Same width, other model: the stored vectors cannot be compared with new ones, so the program says so and stops."""
    server, _ = qdrant_server
    prefix = unique_prefix()
    settings = {
        "EMBEDDING_PROVIDER": "Ollama",
        "RERANKING_ENABLED": "false",
        "QDRANT_URL": server.url,
        "QDRANT_API_KEY": server.api_key,
        "QDRANT_COLLECTION_PREFIX": prefix,
    }
    try:
        first = run_program(argv=["tools", "list"], EMBEDDING_MODEL="first-model", **settings)
        second = run_program(argv=["tools", "list"], EMBEDDING_MODEL="second-model", **settings)
    finally:
        drop_prefix(server.url, server.api_key, prefix)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 1
    assert "model first-model" in second.stderr + second.stdout
    assert "model second-model" in second.stderr + second.stdout
    assert "axmem re-embed" in second.stderr + second.stdout
    assert "Traceback" not in second.stderr


def test_serve_shows_what_the_engine_cache_did_while_the_models_started(qdrant_server, tensorrt_embedder, tmp_path):
    """Logging is configured only inside the server's lifespan, after the models start: their lines must not be lost."""
    from app.config.settings import settings
    from app.repositories.embeddings.tensorrt_model import model_slug

    server, prefix = qdrant_server
    group = tmp_path / "engines" / model_slug(settings.EMBEDDING_MODEL, "EMBEDDING_MODEL")
    group.mkdir(parents=True)
    engine = tensorrt_embedder.startup.engine_dir
    (group / engine.name).symlink_to(engine, target_is_directory=True)  # a cache that holds the engine already
    stale = group / ("0" * 16)
    stale.mkdir()
    (stale / "fingerprint.json").write_text("{}")  # and an engine of another fingerprint, to be pruned

    result = run_program(
        argv=["serve", "--transport", "stdio"],
        EMBEDDING_ENGINE_CACHE_DIR=str(tmp_path / "engines"),
        RERANKING_ENABLED="false",
        QDRANT_URL=server.url,
        QDRANT_API_KEY=server.api_key,
        QDRANT_COLLECTION_PREFIX=prefix,
        stdin=subprocess.DEVNULL,
    )

    assert f"Pruned stale TensorRT engine directories: {stale.name}" in result.stderr
    assert "embedder ready in" in result.stderr
    assert not stale.exists()
    assert (group / engine.name).resolve() == engine.resolve()


def test_an_embedding_width_the_model_does_not_produce_exits_with_the_cause():
    result = run_program(argv=["serve", "--transport", "stdio"], EMBEDDING_DIMENSIONS="768")

    assert result.returncode == 1
    assert "Error: the embedder cannot start: BAAI/bge-small-en-v1.5 produces 384-dimensional embeddings" in result.stderr
    assert "EMBEDDING_DIMENSIONS is 768" in result.stderr
    assert "Set EMBEDDING_DIMENSIONS=384" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_missing_model_on_an_offline_host_exits_telling_where_to_put_it(tmp_path):
    result = run_program(
        argv=["serve", "--transport", "stdio"], EMBEDDING_MODEL_DIR=str(tmp_path), HF_HUB_OFFLINE="1",
    )

    target = tmp_path / "BAAI--bge-small-en-v1.5" / "onnx" / "model.onnx"
    assert result.returncode == 1
    assert "Error: the embedder cannot start: cannot fetch onnx/model.onnx of BAAI/bge-small-en-v1.5" in result.stderr
    assert f"copy the file to {target}" in result.stderr
    assert "Traceback" not in result.stderr


def test_an_embedding_model_that_is_not_a_repository_id_exits_naming_the_setting():
    result = run_program(argv=["serve", "--transport", "stdio"], EMBEDDING_MODEL="azure/some/deployment")

    assert result.returncode == 1
    assert "Error: the embedder cannot start: EMBEDDING_MODEL='azure/some/deployment' is not a Hugging Face" in result.stderr


@pytest.mark.parametrize("model", ["embedder", "reranker"])
def test_a_tokenizer_that_cannot_be_parsed_exits_with_the_cause(model, tensorrt_embedder, tensorrt_reranker, tmp_path):
    """The model file is the real one (linked, not copied); the tokenizer beside it is not JSON."""
    from app.config.settings import settings
    from app.repositories.embeddings.tensorrt_model import (
        ONNX_FILE,
        TOKENIZER_FILE,
        fetch_model,
        model_slug,
    )

    repository, directory_setting = {
        "embedder": (settings.EMBEDDING_MODEL, "EMBEDDING_MODEL_DIR"),
        "reranker": (settings.RERANKING_MODEL, "RERANKING_MODEL_DIR"),
    }[model]
    real = fetch_model(repository, getattr(settings, directory_setting))
    root = tmp_path / model_slug(repository)
    (root / "onnx").mkdir(parents=True)
    (root / ONNX_FILE).symlink_to(real.onnx)
    (root / TOKENIZER_FILE).write_text("{ this is not a tokenizer")
    other = {"EMBEDDING_PROVIDER": "Ollama"} if model == "reranker" else {"RERANKING_ENABLED": "false"}

    result = run_program(argv=["serve", "--transport", "stdio"], **{directory_setting: str(tmp_path)}, **other)

    assert result.returncode == 1
    assert f"Error: the {model} cannot start: the tokenizer in {root / TOKENIZER_FILE} cannot be parsed" in result.stderr
    assert f"Delete {root} to fetch the model again" in result.stderr
    assert "Traceback" not in result.stderr

