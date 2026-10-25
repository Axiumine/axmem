"""Integration tests for the factories of the composition root that need no GPU and no Qdrant (app/bootstrap.py)

The factories choose the adapters from the settings, share the TensorRT engines of one process, wire the backup
service to the connections of the stack and refuse a bad scope ceiling before anything is bound. Each test drives the
real function; only the TensorRT adapter classes, which would load a model onto the GPU, are replaced by recorders
where the question is how many are built and under which key.
"""
import logging
from types import SimpleNamespace

import pytest

from app import bootstrap
from app.bootstrap import (
    create_backup_service,
    get_embedding_adapter,
    get_reranker_adapter,
    operator_failure,
)
from app.config.settings import settings
from app.exceptions import ConflictError
from app.repositories.embeddings import embedding_adapter as remote_module
from app.repositories.embeddings import gateway_adapter as gateway_module
from app.repositories.embeddings import reranker_adapter as http_reranker_module
from app.repositories.embeddings import tensorrt_embedder as embedder_module
from app.repositories.embeddings import tensorrt_reranker as reranker_module
from app.repositories.qdrant.archive import QdrantArchiveAdapter
from app.repositories.qdrant.backup import QdrantBackupAdapter
from app.repositories.qdrant.inspector import QdrantClusterInspector
from app.routes.mcp.scope_resolver import parse_scopes
from app.services.backup_service import BackupService


class _Engine:
    """Stands for a TensorRT adapter: it records its construction and its closing, and loads nothing."""

    log: list = []

    def __init__(self):
        self.closed = False
        self.log.append(("built", type(self).__name__, self))

    def close(self):
        self.closed = True
        self.log.append(("closed", type(self).__name__, self))


class _Embedder(_Engine):
    pass


class _Reranker(_Engine):
    pass


@pytest.fixture
def engines(monkeypatch):
    """An empty engine cache whose TensorRT classes build recorders; the record is the list of what happened."""
    log: list = []
    monkeypatch.setattr(_Engine, "log", log)
    monkeypatch.setattr(bootstrap, "_TENSORRT_ADAPTERS", {})
    monkeypatch.setattr(embedder_module, "TensorRTEmbeddingsAdapter", _Embedder)
    monkeypatch.setattr(reranker_module, "TensorRTRerankAdapter", _Reranker)
    return log


def _built(log) -> list:
    return [(kind, engine) for action, kind, engine in log if action == "built"]


# ============================================
# The backup service
# ============================================


def test_the_backup_service_is_wired_to_the_connections_of_the_stack():
    stack = SimpleNamespace(pool=object(), schema=object(), store=object())

    service = create_backup_service(stack)

    assert type(service) is BackupService
    assert type(service.backups) is QdrantBackupAdapter
    assert (service.backups.pool, service.backups.schema) == (stack.pool, stack.schema)
    assert type(service.archive) is QdrantArchiveAdapter
    assert (service.archive.store, service.archive.schema) == (stack.store, stack.schema)
    assert service.archive._embedder is get_embedding_adapter
    assert type(service.inspector) is QdrantClusterInspector
    assert (service.inspector.pool, service.inspector.schema) == (stack.pool, stack.schema)


# ============================================
# The choice of the adapters
# ============================================


def test_an_unknown_embeddings_provider_is_refused_with_the_setting_and_the_choices(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "Opnai")

    with pytest.raises(ValueError) as refused:
        get_embedding_adapter()

    assert str(refused.value) == (
        "Unsupported EMBEDDING_PROVIDER setting. Must be one of: TensorRT, Azure, Google, OpenAI, Ollama, gateway"
    )


def test_an_unknown_reranking_provider_is_refused_with_the_setting_and_the_choices(monkeypatch):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKING_PROVIDER", "Htp")

    with pytest.raises(ValueError) as refused:
        get_reranker_adapter()

    assert str(refused.value) == "Unsupported RERANKING_PROVIDER setting. Must be one of: TensorRT, HTTP"


def test_there_is_no_reranker_when_reranking_is_off_whatever_the_provider_says(monkeypatch, engines):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)
    monkeypatch.setattr(settings, "RERANKING_PROVIDER", "Htp")

    assert get_reranker_adapter() is None
    assert engines == []


@pytest.mark.parametrize("provider", ["TensorRT", "tensorrt", " TENSORRT "])
def test_the_tensorrt_providers_get_the_shared_engines(monkeypatch, engines, provider):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", provider)
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKING_PROVIDER", provider)

    embedder = get_embedding_adapter()
    reranker = get_reranker_adapter()

    assert (type(embedder), type(reranker)) == (_Embedder, _Reranker)
    assert get_embedding_adapter() is embedder
    assert get_reranker_adapter() is reranker
    assert [kind for kind, _ in _built(engines)] == ["_Embedder", "_Reranker"]


@pytest.fixture
def remote_adapters(monkeypatch):
    """The adapters of the remote providers replaced by empty classes of the same names: only the choice is looked at."""

    def named(name):
        return type(name, (), {"__init__": lambda self, *args, **kwargs: None})

    for name in ("AzureOpenAIAdapter", "GoogleEmbeddingsAdapter", "OpenAIEmbeddingsAdapter", "OllamaEmbeddingsAdapter"):
        monkeypatch.setattr(remote_module, name, named(name))
    monkeypatch.setattr(gateway_module, "GatewayEmbeddingsAdapter", named("GatewayEmbeddingsAdapter"))
    monkeypatch.setattr(http_reranker_module, "HttpRerankAdapter", named("HttpRerankAdapter"))


@pytest.mark.parametrize(
    ("value", "adapter"),
    [
        ("openai", "OpenAIEmbeddingsAdapter"),
        ("OPENAI", "OpenAIEmbeddingsAdapter"),
        ("OpenAI", "OpenAIEmbeddingsAdapter"),
        ("ollama", "OllamaEmbeddingsAdapter"),
        ("Ollama", "OllamaEmbeddingsAdapter"),
        ("azure", "AzureOpenAIAdapter"),
        (" Azure ", "AzureOpenAIAdapter"),
        ("google", "GoogleEmbeddingsAdapter"),
        ("GOOGLE", "GoogleEmbeddingsAdapter"),
        ("gateway", "GatewayEmbeddingsAdapter"),
        ("GATEWAY", "GatewayEmbeddingsAdapter"),
    ],
)
def test_each_embeddings_provider_selects_its_own_adapter_whatever_the_case(monkeypatch, remote_adapters, value, adapter):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", value)

    assert type(get_embedding_adapter()).__name__ == adapter


def test_a_blank_embeddings_provider_means_the_default_which_is_tensorrt(monkeypatch, remote_adapters, engines):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "")

    assert type(get_embedding_adapter()) is _Embedder


@pytest.mark.parametrize("value", ["HTTP", "http", "hTTP", " Http "])
def test_the_http_reranking_provider_selects_the_http_adapter_whatever_the_case(monkeypatch, remote_adapters, engines, value):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKING_PROVIDER", value)

    assert type(get_reranker_adapter()).__name__ == "HttpRerankAdapter"
    assert engines == []


# ============================================
# The engines shared by one process
# ============================================


def test_the_embedder_is_cached_under_its_role_and_the_settings_that_shape_it(monkeypatch, engines):
    first = bootstrap._shared_tensorrt_embedder()

    assert bootstrap._shared_tensorrt_embedder() is first
    assert list(bootstrap._TENSORRT_ADAPTERS.items()) == [
        (
            (
                "embedder",
                settings.EMBEDDING_MODEL,
                settings.EMBEDDING_DIMENSIONS,
                settings.EMBEDDING_TF32,
                settings.EMBEDDING_MODEL_DIR,
                settings.EMBEDDING_ENGINE_CACHE_DIR,
            ),
            first,
        ),
    ]
    seen = [first]
    for name, value in [
        ("EMBEDDING_MODEL", "other/model"),
        ("EMBEDDING_DIMENSIONS", 128),
        ("EMBEDDING_TF32", not settings.EMBEDDING_TF32),
        ("EMBEDDING_MODEL_DIR", "/models/other"),
        ("EMBEDDING_ENGINE_CACHE_DIR", "/engines/other"),
    ]:
        monkeypatch.setattr(settings, name, value)
        engine = bootstrap._shared_tensorrt_embedder()
        assert all(engine is not earlier for earlier in seen), name
        assert bootstrap._shared_tensorrt_embedder() is engine
        seen.append(engine)
    assert len(_built(engines)) == 6


def test_the_reranker_is_cached_under_its_role_and_the_settings_that_shape_it(monkeypatch, engines):
    first = bootstrap._shared_tensorrt_reranker()

    assert bootstrap._shared_tensorrt_reranker() is first
    assert list(bootstrap._TENSORRT_ADAPTERS.items()) == [
        (
            (
                "reranker",
                settings.RERANKING_MODEL,
                settings.RERANKING_WORKERS,
                settings.RERANKING_TF32,
                settings.RERANKING_MODEL_DIR,
                settings.RERANKING_ENGINE_CACHE_DIR,
            ),
            first,
        ),
    ]
    seen = [first]
    for name, value in [
        ("RERANKING_MODEL", "other/model"),
        ("RERANKING_WORKERS", settings.RERANKING_WORKERS + 1),
        ("RERANKING_TF32", not settings.RERANKING_TF32),
        ("RERANKING_MODEL_DIR", "/models/other"),
        ("RERANKING_ENGINE_CACHE_DIR", "/engines/other"),
    ]:
        monkeypatch.setattr(settings, name, value)
        engine = bootstrap._shared_tensorrt_reranker()
        assert all(engine is not earlier for earlier in seen), name
        assert bootstrap._shared_tensorrt_reranker() is engine
        seen.append(engine)
    assert len(_built(engines)) == 6


def test_an_embedder_and_a_reranker_of_the_same_settings_are_two_engines(monkeypatch, engines):
    for name in ("MODEL", "MODEL_DIR", "ENGINE_CACHE_DIR"):
        monkeypatch.setattr(settings, f"EMBEDDING_{name}", f"shared-{name}")
        monkeypatch.setattr(settings, f"RERANKING_{name}", f"shared-{name}")
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 2)
    monkeypatch.setattr(settings, "RERANKING_WORKERS", 2)
    monkeypatch.setattr(settings, "EMBEDDING_TF32", False)
    monkeypatch.setattr(settings, "RERANKING_TF32", False)

    embedder = bootstrap._shared_tensorrt_embedder()
    reranker = bootstrap._shared_tensorrt_reranker()

    assert type(embedder) is _Embedder
    assert type(reranker) is _Reranker
    assert len(bootstrap._TENSORRT_ADAPTERS) == 2


def test_releasing_closes_every_engine_latest_first_and_forgets_them(engines):
    first = bootstrap._shared_tensorrt_embedder()
    second = bootstrap._shared_tensorrt_reranker()

    bootstrap._release_tensorrt_adapters()

    assert bootstrap._TENSORRT_ADAPTERS == {}
    assert [(action, engine) for action, _, engine in engines if action == "closed"] == [
        ("closed", second),
        ("closed", first),
    ]


def test_an_engine_that_fails_to_close_does_not_keep_the_others_open(engines):
    first = bootstrap._shared_tensorrt_embedder()
    second = bootstrap._shared_tensorrt_reranker()

    def fail_to_close():
        raise RuntimeError("device lost")

    second.close = fail_to_close

    with pytest.raises(RuntimeError, match="device lost"):
        bootstrap._release_tensorrt_adapters()

    assert first.closed
    assert bootstrap._TENSORRT_ADAPTERS == {}


# ============================================
# The token counter and the scope ceiling
# ============================================


def test_the_token_counter_is_loaded_within_the_timeout_of_the_settings(monkeypatch):
    asked = []
    monkeypatch.setattr("app.utils.token_counter.preload_token_counter", lambda *args, **kwargs: asked.append((args, kwargs)))
    monkeypatch.setattr(settings, "TIKTOKEN_LOAD_TIMEOUT_SECONDS", 7.5)

    bootstrap.load_token_counter()

    assert asked == [((7.5,), {})]


async def test_a_scope_ceiling_that_does_not_parse_is_refused_before_qdrant_is_touched(monkeypatch):
    touched = []
    monkeypatch.setattr(bootstrap, "create_qdrant_stack", lambda: touched.append("stack"))
    monkeypatch.setattr(settings, "AXMEM_SCOPES", "read,bogus:scope")

    with pytest.raises(ValueError) as refused:
        await bootstrap.check_runtime_start()

    with pytest.raises(ValueError) as parsing:
        parse_scopes("read,bogus:scope")
    assert str(refused.value) == f"AXMEM_SCOPES is not valid: {parsing.value}"
    assert refused.value.__cause__ is not None
    assert touched == []


@pytest.mark.parametrize("scopes", ["", "  ", ",,"])
async def test_an_empty_scope_ceiling_is_refused_with_its_reason(monkeypatch, scopes):
    monkeypatch.setattr(bootstrap, "create_qdrant_stack", lambda: pytest.fail("Qdrant must not be touched"))
    monkeypatch.setattr(settings, "AXMEM_SCOPES", scopes)

    with pytest.raises(ValueError) as refused:
        await bootstrap.check_runtime_start()

    assert str(refused.value) == "AXMEM_SCOPES is not valid: Scope string cannot be empty"


async def test_the_connections_of_a_start_check_are_closed_whatever_the_check_says(monkeypatch):
    closed = []

    class Schema:
        async def ensure(self):
            closed.append("ensured")
            raise RuntimeError("layout differs")

    class Stack:
        schema = Schema()

        async def close(self):
            closed.append("closed")

    monkeypatch.setattr(bootstrap, "create_qdrant_stack", Stack)
    monkeypatch.setattr(settings, "AXMEM_SCOPES", "*")

    with pytest.raises(RuntimeError, match="layout differs"):
        await bootstrap.check_runtime_start()

    assert closed == ["ensured", "closed"]


async def test_a_start_check_that_passes_ensures_the_collections_and_closes_the_connections(monkeypatch):
    steps = []

    class Schema:
        async def ensure(self):
            steps.append("ensured")

    class Stack:
        schema = Schema()

        async def close(self):
            steps.append("closed")

    monkeypatch.setattr(bootstrap, "create_qdrant_stack", Stack)
    monkeypatch.setattr(settings, "AXMEM_SCOPES", "read,write:memories")

    assert await bootstrap.check_runtime_start() is None

    assert steps == ["ensured", "closed"]


# ============================================
# The one line of a failure
# ============================================


def test_a_conflict_is_one_line_with_its_own_text():
    assert operator_failure(ConflictError("the memory changed while it was written")) == (
        "the memory changed while it was written"
    )


def test_the_log_of_the_models_stays_silent_for_remote_providers(caplog):
    caplog.set_level(logging.DEBUG, logger="app.bootstrap")

    bootstrap.log_tensorrt_models(SimpleNamespace(), None, object())

    assert caplog.records == []
