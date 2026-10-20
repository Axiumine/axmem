"""Hard-failing GPU preflight for every suite that needs the NVIDIA GPU.

The local reranker has no CPU path, so a suite that needs it cannot be skipped or degraded:
without a usable GPU the whole pytest run ends at once with a message that names the cause.
"""
import os

import pytest

from app.exceptions import RerankerStartupError

# VRAM one test process needs: the engine and one execution context at the profile maximum
# (about 2.2 GiB measured on an RTX PRO 4000 Blackwell), with room for the CUDA context.
PER_PROCESS_BYTES = 2560 * 1024**2
# Share of the card the pytest workers may plan to use; the rest is for the desktop and the driver.
USABLE_FRACTION = 0.9


def worker_budget_problem(total_bytes: int, workers: int) -> str | None:
    """Why ``workers`` pytest processes cannot each hold an engine on a card of ``total_bytes``."""
    usable = int(total_bytes * USABLE_FRACTION)
    if workers * PER_PROCESS_BYTES <= usable:
        return None
    fits = max(usable // PER_PROCESS_BYTES, 1)
    return (
        f"{workers} pytest-xdist workers each load a TensorRT engine of about "
        f"{PER_PROCESS_BYTES / 1024**3:.1f} GiB, but only {usable / 1024**3:.1f} GiB of the "
        f"{total_bytes / 1024**3:.1f} GiB of GPU memory is usable. Run with at most -n {fits}."
    )


def require_gpu() -> None:
    """End the pytest run (never skip) when this process cannot run the TensorRT reranker."""
    try:
        from app.repositories.embeddings.tensorrt_runtime import probe_gpu

        gpu = probe_gpu()
    except RerankerStartupError as exc:
        pytest.exit(
            f"GPU preflight failed ({exc.cause}): {exc}\n"
            "These suites run the real TensorRT reranker on an NVIDIA GPU; there is no CPU path "
            "and nothing is skipped.",
            returncode=2,
        )
    workers = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1"))
    problem = worker_budget_problem(gpu.total_bytes, workers)
    if problem:
        pytest.exit(f"GPU preflight failed (memory): {problem}", returncode=2)
