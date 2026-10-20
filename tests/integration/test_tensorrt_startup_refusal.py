"""A server that cannot run the TensorRT reranker refuses to start, says why, and never falls back.

Each case starts the real program in a subprocess whose environment really lacks something:
no visible GPU, or TensorRT / CUDA libraries that cannot be imported. The reranker is enabled
(the default) and the provider is TensorRT, so the only way to start would be a fallback.
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
    sys.argv = ["forgetful", *{argv!r}]
    import main
    raise SystemExit(main.cli())
    """,
)


def run_program(code=None, argv=None, **env):
    environment = {
        **os.environ,
        "AUTH_MODE": "none",
        "DATABASE": "SQLite",
        "SQLITE_MEMORY": "true",
        "RERANKING_ENABLED": "true",
        "RERANKING_PROVIDER": "TensorRT",
        **env,
    }
    command = [sys.executable, "-c", code] if code else [sys.executable, "main.py", *argv]
    return subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120, check=False)  # noqa: S603


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_serve_without_a_visible_gpu_exits_with_the_cause(transport):
    result = run_program(argv=["serve", "--transport", transport, "--host", "127.0.0.1"], CUDA_VISIBLE_DEVICES="-1")

    assert result.returncode == 1
    assert "Error: the reranker cannot start: no NVIDIA GPU is visible to CUDA (cudaErrorNoDevice)" in result.stderr
    assert "no CPU path" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_a_cli_command_without_a_visible_gpu_reports_the_same_cause():
    result = run_program(argv=["tools", "list"], CUDA_VISIBLE_DEVICES="-1")

    assert result.returncode == 1
    assert "no NVIDIA GPU is visible to CUDA" in result.stderr + result.stdout


@pytest.mark.parametrize("hidden", [["tensorrt", "tensorrt_bindings", "tensorrt_libs"], ["cuda"]])
def test_serve_without_importable_tensorrt_libraries_exits_with_the_cause(hidden):
    code = HIDE_AND_RUN.format(hidden=set(hidden), argv=["serve", "--transport", "stdio"])

    result = run_program(code=code)

    assert result.returncode == 1
    assert "Error: the reranker cannot start: TensorRT or the CUDA Python bindings cannot be imported" in result.stderr
    assert "hidden by the test" in result.stderr
    assert "RERANKING_PROVIDER=HTTP" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_disabled_reranker_does_not_need_a_gpu():
    result = run_program(
        code=HIDE_AND_RUN.format(hidden={"tensorrt", "tensorrt_bindings", "tensorrt_libs", "cuda"}, argv=["tools", "list"]),
        RERANKING_ENABLED="false",
        CUDA_VISIBLE_DEVICES="-1",
    )

    assert result.returncode == 0
    assert "total_count" in result.stdout
