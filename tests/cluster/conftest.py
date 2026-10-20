"""Fixtures for the three-node Qdrant cluster suite (marker ``cluster``, run by the ``e2e-qdrant`` gate).

The cluster is real: three ``qdrant/qdrant`` containers on a Docker network with an API key and
internal authentication, started once per pytest process and removed at the end. The collections use the
documented cluster profile (3 shards, replication factor 3, write consistency factor 2). A test that stops
or kills a node gives it back and waits for the cluster, so the next test finds three working nodes.
"""
import uuid

import pytest
from pydantic import SecretStr

from app.config.settings import settings
from tests.gpu_preflight import require_gpu
from tests.qdrant_support import (
    NODES,
    QdrantCluster,
    drop_prefix,
    require_docker,
    start_cluster,
    stop_cluster,
    unique_prefix,
)


def pytest_collection_modifyitems(config, items):
    """The cluster tests embed with the real TensorRT model and need Docker: without either this run ends, it never skips."""
    require_gpu()
    require_docker()


@pytest.fixture(scope="session")
def cluster():
    """Three nodes that formed one cluster, removed at the end of the run."""
    running = start_cluster()
    yield running
    stop_cluster(running)


@pytest.fixture(scope="session")
def cluster_prefix(cluster):
    """The collection prefix of the suite; its collections are deleted at the end."""
    prefix = unique_prefix()
    yield prefix
    drop_prefix(cluster.urls[0], cluster.api_key, prefix)


def point_settings_at_the_cluster(monkeypatch, cluster: QdrantCluster, prefix: str) -> None:
    from app.models import tenant_models

    monkeypatch.setattr(settings, "QDRANT_URL", ",".join(cluster.urls))
    monkeypatch.setattr(settings, "QDRANT_API_KEY", SecretStr(cluster.api_key))
    monkeypatch.setattr(settings, "QDRANT_COLLECTION_PREFIX", prefix)
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", NODES)
    monkeypatch.setattr(settings, "QDRANT_REPLICATION_FACTOR", NODES)
    monkeypatch.setattr(settings, "QDRANT_WRITE_CONSISTENCY_FACTOR", 2)
    monkeypatch.setattr(settings, "QDRANT_TIMEOUT_SECONDS", 120)  # creating a collection with eight indexes on three nodes is slow
    monkeypatch.setattr(settings, "QDRANT_WRITE_ATTEMPTS", 10)  # a busy host answers consensus operations with a timeout
    monkeypatch.setattr(tenant_models, "TENANT_NAMESPACE", uuid.uuid4())


@pytest.fixture
async def cluster_stack(cluster, cluster_prefix, tensorrt_embedder, monkeypatch):
    """The Qdrant stack on the cluster's shared collections, created on first use; a tenant namespace of its own per test."""
    from app.repositories.qdrant.memory_repository import QdrantStack

    point_settings_at_the_cluster(monkeypatch, cluster, cluster_prefix)
    stack = QdrantStack.from_settings()
    await stack.schema.ensure()
    yield stack
    await stack.close()


@pytest.fixture
async def repo(cluster_stack, tensorrt_embedder, monkeypatch):
    """The memory repository over the cluster (reranking off; every test acts as tenants of its own)."""
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)
    return cluster_stack.memory_repository(tensorrt_embedder)
