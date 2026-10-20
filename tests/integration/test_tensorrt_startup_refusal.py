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


def run_program(code=None, argv=None, **env):
    environment = {
        **os.environ,
        "AUTH_MODE": "none",
        "EMBEDDING_PROVIDER": "TensorRT",
        "RERANKING_ENABLED": "true",
        "RERANKING_PROVIDER": "TensorRT",
        **env,
    }
    command = [sys.executable, "-c", code] if code else [sys.executable, "main.py", *argv]
    return subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120, check=False)  # noqa: S603


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


def test_without_the_local_models_no_gpu_is_needed(qdrant_server):
    """Another embeddings provider and no reranking: the program starts on a machine with no GPU at all."""
    server, prefix = qdrant_server
    result = run_program(
        code=HIDE_AND_RUN.format(hidden={*TENSORRT_HIDDEN, "cuda"}, argv=["tools", "list"]),
        EMBEDDING_PROVIDER="Ollama",
        RERANKING_ENABLED="false",
        CUDA_VISIBLE_DEVICES="-1",
        QDRANT_URL=server.url,
        QDRANT_API_KEY=server.api_key,
        QDRANT_COLLECTION_PREFIX=prefix,
    )

    assert result.returncode == 0
    assert "total_count" in result.stdout


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
