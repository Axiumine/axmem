"""Integration test fixtures with in-memory stubs (no real database required)

The stub repositories and test doubles live in ``tests/integration/stubs/`` (one
module per domain); this module imports them and exposes the fixtures the tests use.
Only database, HTTP and embedding I/O is stubbed: the reranker is the real TensorRT engine
on the GPU (``tensorrt_reranker``), and the suite refuses to run without that GPU.
"""

import pytest

from app.services.memory_service import MemoryService
from app.services.tenant_service import TenantService
from tests.gpu_preflight import require_gpu
from tests.integration.stubs import (
    InMemoryMemoryRepository,
    InMemoryUserRepository,
    MockEmbeddingsAdapter,
)


def pytest_collection_modifyitems(config, items):
    """The reranker runs on the NVIDIA GPU with no CPU path: without one this run ends, it never skips."""
    require_gpu()


@pytest.fixture
def clean_test_data():
    """Fixture that provides a clean slate for each test"""
    # Setup: nothing needed
    return
    # Teardown: nothing needed (new instance per test)


@pytest.fixture
def mock_user_repository():
    """Provides an in-memory user repository"""
    return InMemoryUserRepository()


@pytest.fixture
def test_tenant_service(mock_user_repository):
    """Provides a TenantService with in-memory repository"""
    return TenantService(mock_user_repository)


# ============ Memory Testing Fixtures ============


@pytest.fixture
def mock_embeddings_adapter():
    """Provides a mock embeddings adapter"""
    return MockEmbeddingsAdapter(dimensions=384)


@pytest.fixture
def mock_memory_repository():
    """Provides an in-memory memory repository"""
    return InMemoryMemoryRepository()


@pytest.fixture
def test_memory_service(mock_memory_repository):
    """Provides a MemoryService with in-memory repository"""
    return MemoryService(mock_memory_repository)


# =============================================================================
# Real-runtime settings fixture (bootstrap factory / CLI executor tests)
# =============================================================================

_SQLITE_RUNTIME_FIELDS = [
    "DATABASE",
    "SQLITE_MEMORY",
    "EMBEDDING_PROVIDER",
    "RERANKING_ENABLED",
    "FORGETFUL_SCOPES",
]


@pytest.fixture
def sqlite_runtime_settings(tensorrt_reranker):
    """Point the composition root at in-memory SQLite with deterministic feature flags.

    Reranking stays enabled: the runtime it builds shares the real TensorRT engine of the session.
    """
    from app.config.settings import settings

    original = {name: getattr(settings, name) for name in _SQLITE_RUNTIME_FIELDS}
    settings.DATABASE = "SQLite"
    settings.SQLITE_MEMORY = True
    settings.EMBEDDING_PROVIDER = "FastEmbed"
    settings.RERANKING_ENABLED = True
    settings.FORGETFUL_SCOPES = "*"
    yield settings
    for name, value in original.items():
        setattr(settings, name, value)
