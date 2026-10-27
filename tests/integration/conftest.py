"""Integration test fixtures: the real Qdrant repository, the real TensorRT models, stubs only for HTTP

The memory repository under test is the production ``QdrantMemoryRepository`` on a real Qdrant node (a
Docker container started once per test process, see ``tests/qdrant_support.py``). A hand-written
in-memory repository would repeat Qdrant's filter, ordering and conditional-write semantics and could
pass where the real thing fails, so there is none. The embedder and the reranker are the real TensorRT
engines on the GPU (``tensorrt_embedder``, ``tensorrt_reranker``); the suite refuses to run without that
GPU or without Qdrant. Only HTTP I/O (remote providers, remote servers) is stubbed.
"""

import logging

import pytest
from qdrant_client import AsyncQdrantClient

from app.config import logging_config
from app.services.memory_service import MemoryService
from tests.gpu_preflight import require_gpu
from tests.qdrant_support import SCROLLS_PER_TEST, require_docker


def pytest_collection_modifyitems(config, items):
    """The models run on the NVIDIA GPU and the data lives in Qdrant: without either this run ends, it never skips."""
    require_gpu()
    require_docker()


@pytest.fixture(autouse=True)
def paging_loop_ends_the_test(monkeypatch):
    """Fail the test on its ``SCROLLS_PER_TEST + 1``-th read of a page of points instead of letting a paging loop run on.

    Every scan of a collection (``iterate``, the rebuilds, the backup, the inspector) reads page after page from the
    cursor of the page before. A mutant that stops the cursor from advancing makes the scan endless, and whether a test
    ends it depends on the order in which mutmut runs the tests that cover it: one that only waits runs until mutmut
    stops it, which mutmut books as a timeout and the mutation gate counts as a survivor. With this limit the first test
    to reach such a loop fails within seconds. No integration test reads a thousand pages. ``pytest.fail`` raises a
    ``BaseException``, so no ``except Exception`` of the code under test can swallow it and go on looping. The limit
    belongs to this suite alone: mutmut runs these tests, and the cluster suite lets eight writers read beside a scan.
    """
    real = AsyncQdrantClient.scroll
    scrolls = 0

    async def counted(self, *args, **kwargs):
        nonlocal scrolls
        scrolls += 1
        if scrolls > SCROLLS_PER_TEST:
            pytest.fail(f"more than {SCROLLS_PER_TEST} pages were read in one test: a paging loop that does not end")
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(AsyncQdrantClient, "scroll", counted)


@pytest.fixture(autouse=True)
def logging_left_as_found():
    """Stop the queued logging that a test started, so that no test depends on the order of the tests.

    The operator commands and the server call ``configure_logging``, which puts a queue handler on the root logger and
    starts a listener thread that writes to the stderr of that test. The stream is closed when the test ends, and a test
    that runs later and asserts on its own stderr reads ``--- Logging error ---`` from the thread instead. In the order of
    the files that never happens; mutmut runs the tests of a mutant in an order of its own.
    """
    root = logging.getLogger()
    queued = [handler for handler in root.handlers if isinstance(handler, logging_config.RecordPreservingQueueHandler)]
    level = root.level
    yield
    started = [
        handler for handler in root.handlers
        if isinstance(handler, logging_config.RecordPreservingQueueHandler) and handler not in queued
    ]
    if started:
        for handler in started:
            root.removeHandler(handler)
        logging_config.shutdown_logging()
        root.setLevel(level)


@pytest.fixture
def clean_test_data():
    """Fixture that provides a clean slate for each test"""
    # Setup: nothing needed
    return
    # Teardown: nothing needed (every test works in a tenant of its own)


# ============ Memory Testing Fixtures ============


@pytest.fixture
async def memory_repository(qdrant_stack, tensorrt_embedder, monkeypatch):
    """The Qdrant memory repository on the shared test collections (reranking off; tests use tenants of their own)"""
    from app.config.settings import settings

    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)
    return qdrant_stack.memory_repository(tensorrt_embedder)


@pytest.fixture
async def ranked_memory_repository(qdrant_stack, tensorrt_embedder, tensorrt_reranker, monkeypatch):
    """The same repository with the real reranker attached"""
    from app.config.settings import settings

    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    return qdrant_stack.memory_repository(tensorrt_embedder, tensorrt_reranker)


@pytest.fixture
def test_memory_service(memory_repository):
    """Provides a MemoryService over the Qdrant repository"""
    return MemoryService(memory_repository)


# =============================================================================
# Real-runtime settings fixture (bootstrap factory / CLI executor tests)
# =============================================================================

_RUNTIME_FIELDS = [
    "EMBEDDING_PROVIDER",
    "RERANKING_ENABLED",
    "AXMEM_SCOPES",
]


@pytest.fixture
def runtime_settings(qdrant_env, tensorrt_reranker, tensorrt_embedder):
    """Point the composition root at the test Qdrant node with deterministic feature flags.

    Reranking stays enabled: the runtime it builds shares the real TensorRT engine of the session.
    """
    from app.config.settings import settings

    original = {name: getattr(settings, name) for name in _RUNTIME_FIELDS}
    settings.EMBEDDING_PROVIDER = "TensorRT"
    settings.RERANKING_ENABLED = True
    settings.AXMEM_SCOPES = "*"
    yield settings
    for name, value in original.items():
        setattr(settings, name, value)
