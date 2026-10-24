"""Fixtures shared by every test suite."""

import os
import shutil
import uuid

import pytest
from pydantic import SecretStr

from app.config.settings import settings
from tests import CONFIG_HOME
from tests.embedding_doubles import spy_on
from tests.qdrant_support import (
    QdrantServer,
    drop_prefix,
    start_single_node,
    stop,
    unique_prefix,
)


@pytest.fixture(scope="session", autouse=True)
def base_temp_made_first(tmp_path_factory):
    """Make pytest's base temp directory before any test can patch ``atexit.register``.

    pytest registers the clean-up of that directory with ``atexit`` the first time a test asks for a temp directory.
    A test that records or replaces ``atexit.register`` (the commands and the server register the log shutdown there)
    and is the first, or the only one, to ask would make pytest call its patch with the clean-up's five arguments:
    ``list.append`` refused them, so a test file run on its own errored while the same file passed in a full run.

    Under mutmut the directory is removed again when the session ends. A mutmut worker ends with ``os._exit``, so pytest's
    ``atexit`` clean-up never runs, its lock is never released (it then spares the directory for three days) and the
    directory of every mutant that a test killed stays: the Qdrant backups of those tests filled the disk.
    """
    mutating = "MUTANT_UNDER_TEST" in os.environ
    base = tmp_path_factory.getbasetemp()
    yield base
    if mutating:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def config_home_removed():
    """Remove the settings directory that ``tests/__init__.py`` made for this process when its session ends.

    A mutmut worker ends with ``os._exit``, which skips ``atexit``, and a run makes one worker per mutant.
    """
    yield
    shutil.rmtree(CONFIG_HOME, ignore_errors=True)


@pytest.fixture(autouse=True)
def local_tenant_mode(monkeypatch):
    """Run as the single local tenant, as a server bound to loopback with AUTH_MODE=none does.

    The application fails closed without an authentication mode, so tests that exercise a
    request path need one. Tests of the other modes, and of the refusal itself, set
    AUTH_MODE themselves.
    """
    monkeypatch.setattr(settings, "AUTH_MODE", "none")


@pytest.fixture(scope="session")
def tensorrt_reranker():
    """The real TensorRT reranker, built (or loaded from the engine cache) once per test process.

    It is the production adapter with production defaults: strict fp32, one worker, the default
    model and cache directories. ``get_reranker_adapter`` returns this same engine to every runtime
    built under these settings, so suites share one copy of it in GPU memory.
    """
    from app.bootstrap import get_reranker_adapter

    settings_before = pytest.MonkeyPatch()
    settings_before.setattr(settings, "RERANKING_ENABLED", True)
    settings_before.setattr(settings, "RERANKING_PROVIDER", "TensorRT")
    settings_before.setattr(settings, "RERANKING_WORKERS", 1)
    settings_before.setattr(settings, "RERANKING_TF32", False)
    try:
        yield get_reranker_adapter()
    finally:
        settings_before.undo()


@pytest.fixture(scope="session")
def tensorrt_embedder():
    """The real TensorRT embedder, built (or loaded from the engine cache) once per test process.

    It is the production adapter with production defaults: strict fp32, the default model and
    cache directories, 384 dimensions. ``get_embedding_adapter`` returns this same engine to every
    runtime built under these settings, so suites share one copy of it in GPU memory and no test
    needs an embedding stand-in to get real vectors.
    """
    from app.bootstrap import get_embedding_adapter

    settings_before = pytest.MonkeyPatch()
    settings_before.setattr(settings, "EMBEDDING_PROVIDER", "TensorRT")
    settings_before.setattr(settings, "EMBEDDING_TF32", False)
    settings_before.setattr(settings, "EMBEDDING_DIMENSIONS", 384)
    try:
        yield get_embedding_adapter()
    finally:
        settings_before.undo()


@pytest.fixture
def embedding_spy(tensorrt_embedder):
    """The real embedder behind call-recording spies (see ``tests.embedding_doubles.spy_on``).

    For service tests with a mocked repository: the vectors are the model's, the spies show how
    the service called it, and a test injects a provider fault by assigning ``side_effect``.
    """
    return spy_on(tensorrt_embedder)


# ---- Qdrant ----------------------------------------------------------------------------------------
#
# One real Qdrant node per test process (a Docker container, see tests/qdrant_support.py). Tests share its
# collections and stay apart by tenant: ``qdrant_env`` gives every test a tenant namespace of its own, so
# the same tenant name ("alice", DEFAULT_TENANT) is a different tenant in every test. A test that needs
# collections of its own (re-embedding, snapshots, the schema) uses ``isolated_qdrant``.


@pytest.fixture(scope="session")
def qdrant_server():
    """The Qdrant node of this test process, started once and removed at the end."""
    server = start_single_node()
    prefix = unique_prefix()
    yield server, prefix
    if server.container:
        stop(server.container)
    else:
        drop_prefix(server.url, server.api_key, prefix)


def _point_settings_at(monkeypatch, server: QdrantServer, prefix: str) -> None:
    from app.models import tenant_models

    monkeypatch.setattr(settings, "QDRANT_URL", server.url)
    monkeypatch.setattr(settings, "QDRANT_API_KEY", SecretStr(server.api_key))
    monkeypatch.setattr(settings, "QDRANT_COLLECTION_PREFIX", prefix)
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 1)
    monkeypatch.setattr(settings, "QDRANT_REPLICATION_FACTOR", 1)
    monkeypatch.setattr(settings, "QDRANT_WRITE_CONSISTENCY_FACTOR", 1)
    monkeypatch.setattr(tenant_models, "TENANT_NAMESPACE", uuid.uuid4())


@pytest.fixture
def qdrant_env(qdrant_server, monkeypatch):
    """Point the settings at the shared test node, with a tenant namespace no other test shares."""
    server, prefix = qdrant_server
    _point_settings_at(monkeypatch, server, prefix)
    return settings


@pytest.fixture
def isolated_qdrant(qdrant_server, monkeypatch):
    """Point the settings at collections that only this test uses, and delete them afterwards."""
    server, _ = qdrant_server
    prefix = unique_prefix()
    _point_settings_at(monkeypatch, server, prefix)
    yield settings
    drop_prefix(server.url, server.api_key, prefix)


@pytest.fixture
async def qdrant_stack(qdrant_env, tensorrt_embedder):
    """The Qdrant stack on the shared collections, created if they do not exist yet."""
    from app.repositories.qdrant.memory_repository import QdrantStack

    stack = QdrantStack.from_settings()
    await stack.schema.ensure()
    yield stack
    await stack.close()


@pytest.fixture
async def isolated_stack(isolated_qdrant, tensorrt_embedder):
    """A Qdrant stack on collections of its own."""
    from app.repositories.qdrant.memory_repository import QdrantStack

    stack = QdrantStack.from_settings()
    await stack.schema.ensure()
    yield stack
    await stack.close()


@pytest.fixture
async def raw_qdrant(qdrant_env):
    """A plain Qdrant client on the test node, for tests that plant or inspect stored data directly."""
    import warnings

    from qdrant_client import AsyncQdrantClient

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # a key on plain http to the loopback test node
        client = AsyncQdrantClient(
            url=qdrant_env.QDRANT_URL, api_key=qdrant_env.QDRANT_API_KEY.get_secret_value(), check_compatibility=False,
        )
    yield client
    await client.close()
