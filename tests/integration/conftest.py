"""Integration test fixtures: the real Qdrant repository, the real TensorRT models, stubs only for HTTP

The memory repository under test is the production ``QdrantMemoryRepository`` on a real Qdrant node (a
Docker container started once per test process, see ``tests/qdrant_support.py``). A hand-written
in-memory repository would repeat Qdrant's filter, ordering and conditional-write semantics and could
pass where the real thing fails, so there is none. The embedder and the reranker are the real TensorRT
engines on the GPU (``tensorrt_embedder``, ``tensorrt_reranker``); the suite refuses to run without that
GPU or without Qdrant. Only HTTP I/O (remote providers, remote servers) is stubbed.
"""

import pytest

from app.services.memory_service import MemoryService
from tests.gpu_preflight import require_gpu
from tests.qdrant_support import require_docker


def pytest_collection_modifyitems(config, items):
    """The models run on the NVIDIA GPU and the data lives in Qdrant: without either this run ends, it never skips."""
    require_gpu()
    require_docker()


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
    "FORGETFUL_SCOPES",
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
    settings.FORGETFUL_SCOPES = "*"
    yield settings
    for name, value in original.items():
        setattr(settings, name, value)
