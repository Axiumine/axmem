"""Hard-failing GPU preflight for every suite that needs the NVIDIA GPU.

The local embedder and reranker have no CPU path, so a suite that needs them cannot be skipped or
degraded: without a usable GPU the whole pytest run ends at once with a message that names the cause.
"""
import os

import pytest

from app.exceptions import TensorRTStartupError

# VRAM one process needs: the embedder and the reranker, each with one execution context at the profile
# maximum, in one CUDA context (about 4.1 GiB measured on an RTX PRO 4000 Blackwell, 2.2 GiB for the
# embedder and 1.9 GiB more for the reranker), with some room.
PER_PROCESS_BYTES = 4608 * 1024**2
# The standalone end-to-end test starts a server process of its own beside the pytest workers.
SERVER_PROCESSES = 1
# Share of the card the pytest workers may plan to use; the rest is for the desktop and the driver.
USABLE_FRACTION = 0.9


def worker_budget_problem(total_bytes: int, workers: int) -> str | None:
    """Why ``workers`` pytest processes cannot each hold an engine on a card of ``total_bytes``."""
    usable = int(total_bytes * USABLE_FRACTION)
    if (workers + SERVER_PROCESSES) * PER_PROCESS_BYTES <= usable:
        return None
    fits = max(usable // PER_PROCESS_BYTES - SERVER_PROCESSES, 1)
    return (
        f"{workers} pytest-xdist workers each load the TensorRT embedder and reranker, about "
        f"{PER_PROCESS_BYTES / 1024**3:.1f} GiB per process, and the standalone test starts {SERVER_PROCESSES} "
        f"server process more, but only {usable / 1024**3:.1f} GiB of the "
        f"{total_bytes / 1024**3:.1f} GiB of GPU memory is usable. Run with at most -n {fits}."
    )


def exit_code() -> int:
    """The exit code of a failed preflight: 2 (the documented one), or 1 under mutmut.

    mutmut sets ``MUTANT_UNDER_TEST`` in every process that runs tests, and books exit code 2 as "check was interrupted
    by user", a status that no test turns into "killed". A mutant of ``probe_gpu`` that makes the preflight fail must be
    killed, so under mutmut the run ends with 1, the code of a failed test. This hides no real failure: mutmut runs the
    suite unmutated first and stops when that run fails, so a machine whose GPU is unusable never reaches a mutant.
    """
    return 1 if "MUTANT_UNDER_TEST" in os.environ else 2


def require_gpu() -> None:
    """End the pytest run (never skip) when this process cannot run the TensorRT embedder and reranker."""
    try:
        from app.repositories.embeddings.tensorrt_runtime import probe_gpu

        gpu = probe_gpu()
    except TensorRTStartupError as exc:
        pytest.exit(
            f"GPU preflight failed ({exc.cause}): {exc}\n"
            "These suites run the real TensorRT embedder and reranker on an NVIDIA GPU; there is no CPU "
            "path and nothing is skipped.",
            returncode=exit_code(),
        )
    workers = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1"))
    problem = worker_budget_problem(gpu.total_bytes, workers)
    if problem:
        pytest.exit(f"GPU preflight failed (memory): {problem}", returncode=exit_code())
