"""Integration tests for startup configuration wiring

Documented settings must either take effect or be rejected up front; they must never be
silently ignored or fail later with an opaque error:

- EMBEDDING_PROVIDER / RERANKING_PROVIDER / LOG_FORMAT match case-insensitively and an
  unknown value fails fast (like DATABASE) instead of selecting a different provider.
- ENCODING_* / AGENT_* provenance defaults are length-checked against the model caps.
- The root service-info document only advertises endpoints that exist.

Settings are built from environment variables with Settings(_env_file=None), which is what
startup does, so no database or network is needed.
"""
import importlib
import inspect
import logging
import pkgutil

import httpx
import pytest
from pydantic import BaseModel, ValidationError

import app.models as models_package
import main
from app import bootstrap
from app.config import logging_config
from app.config.settings import (
    PROVENANCE_MAX_LENGTHS,
    Settings,
    settings,
)
from app.exceptions import RerankerError
from app.repositories.embeddings import embedding_adapter as embedding_module
from app.repositories.embeddings import gateway_adapter as gateway_module
from app.repositories.embeddings import reranker_adapter as reranker_module
from app.utils.provenance import PROVENANCE_ENV_MAP


def _settings(monkeypatch, **env):
    """Build Settings the way startup does: from the environment, ignoring .env files."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


# ============================================================================
# Provider / format settings: case-insensitive, unknown values rejected
# ============================================================================

@pytest.fixture
def fake_adapters(monkeypatch):
    """Replace provider adapters with cheap fakes so no model is loaded."""
    def fake(name):
        return type(name, (), {"__init__": lambda self, *args, **kwargs: None})

    for name in (
        "AzureOpenAIAdapter",
        "GoogleEmbeddingsAdapter",
        "OpenAIEmbeddingsAdapter",
        "OllamaEmbeddingsAdapter",
        "FastEmbeddingAdapter",
    ):
        monkeypatch.setattr(embedding_module, name, fake(name))
    monkeypatch.setattr(gateway_module, "GatewayEmbeddingsAdapter", fake("GatewayEmbeddingsAdapter"))
    monkeypatch.setattr(reranker_module, "HttpRerankAdapter", fake("HttpRerankAdapter"))


@pytest.mark.parametrize(
    ("setting", "raw", "canonical"),
    [
        ("EMBEDDING_PROVIDER", "openai", "OpenAI"),
        ("EMBEDDING_PROVIDER", "OLLAMA", "Ollama"),
        ("EMBEDDING_PROVIDER", " azure ", "Azure"),
        ("EMBEDDING_PROVIDER", "google", "Google"),
        ("EMBEDDING_PROVIDER", "fastembed", "FastEmbed"),
        ("EMBEDDING_PROVIDER", " Gateway ", "gateway"),
        ("RERANKING_PROVIDER", "http", "HTTP"),
        ("RERANKING_PROVIDER", "tensorrt", "TensorRT"),
        ("RERANKING_PROVIDER", " TENSORRT ", "TensorRT"),
        ("LOG_FORMAT", "JSON", "json"),
        ("LOG_FORMAT", "Console", "console"),
    ],
)
def test_choice_settings_are_normalised_to_canonical_spelling(monkeypatch, setting, raw, canonical):
    loaded = _settings(monkeypatch, **{setting: raw})

    assert getattr(loaded, setting) == canonical


@pytest.mark.parametrize(
    ("setting", "typo"),
    [
        ("EMBEDDING_PROVIDER", "Opnai"),
        ("RERANKING_PROVIDER", "Htp"),
        ("RERANKING_PROVIDER", "FastEmbed"),  # the ONNX Runtime reranker no longer exists
        ("LOG_FORMAT", "jsno"),
    ],
)
def test_unknown_choice_setting_fails_startup(monkeypatch, setting, typo):
    with pytest.raises(ValidationError, match=setting):
        _settings(monkeypatch, **{setting: typo})


@pytest.mark.parametrize("setting", ["EMBEDDING_PROVIDER", "RERANKING_PROVIDER", "LOG_FORMAT"])
def test_blank_choice_setting_keeps_the_default(monkeypatch, setting):
    default = Settings.model_fields[setting].default

    assert getattr(_settings(monkeypatch, **{setting: ""}), setting) == default


@pytest.mark.parametrize(
    ("value", "adapter"),
    [
        ("openai", "OpenAIEmbeddingsAdapter"),
        ("OPENAI", "OpenAIEmbeddingsAdapter"),
        ("ollama", "OllamaEmbeddingsAdapter"),
        ("azure", "AzureOpenAIAdapter"),
        ("google", "GoogleEmbeddingsAdapter"),
        ("fastembed", "FastEmbeddingAdapter"),
        ("FastEmbed", "FastEmbeddingAdapter"),
        ("gateway", "GatewayEmbeddingsAdapter"),
        ("GATEWAY", "GatewayEmbeddingsAdapter"),
    ],
)
def test_embedding_factory_selects_provider_ignoring_case(monkeypatch, fake_adapters, value, adapter):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", value)

    assert type(bootstrap.get_embedding_adapter()).__name__ == adapter


def test_embedding_factory_rejects_unknown_provider(monkeypatch, fake_adapters):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "Opnai")

    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER"):
        bootstrap.get_embedding_adapter()


@pytest.mark.parametrize("value", ["HTTP", "http", "hTTP"])
def test_http_reranker_factory_selects_provider_ignoring_case(monkeypatch, fake_adapters, value):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKING_PROVIDER", value)

    assert type(bootstrap.get_reranker_adapter()).__name__ == "HttpRerankAdapter"


@pytest.mark.parametrize("value", ["TensorRT", "tensorrt", "TENSORRT"])
def test_tensorrt_reranker_factory_returns_the_shared_engine_ignoring_case(
    monkeypatch, tensorrt_reranker, value,
):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKING_PROVIDER", value)

    assert bootstrap.get_reranker_adapter() is tensorrt_reranker
    assert bootstrap.get_reranker_adapter() is bootstrap.get_reranker_adapter()


def test_shared_rerankers_are_closed_when_the_process_releases_them(monkeypatch, tensorrt_reranker):
    monkeypatch.setattr(bootstrap, "_TENSORRT_RERANKERS", {})
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKING_PROVIDER", "TensorRT")
    monkeypatch.setattr(settings, "RERANKING_WORKERS", 2)  # another cache key: a second adapter, same cached engine

    second = bootstrap.get_reranker_adapter()
    assert second is not tensorrt_reranker
    assert len(second.score("query", ["document"])) == 1

    bootstrap._release_tensorrt_rerankers()

    assert bootstrap._TENSORRT_RERANKERS == {}
    with pytest.raises(RerankerError, match="closed"):
        second.score("query", ["document"])


@pytest.mark.parametrize("provider", ["Htp", "FastEmbed"])
def test_reranker_factory_rejects_unknown_provider(monkeypatch, fake_adapters, provider):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKING_PROVIDER", provider)

    with pytest.raises(ValueError, match="RERANKING_PROVIDER"):
        bootstrap.get_reranker_adapter()


def test_reranker_factory_returns_none_when_reranking_disabled(monkeypatch, fake_adapters):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)

    assert bootstrap.get_reranker_adapter() is None


@pytest.fixture
def restored_root_logger():
    """configure_logging() replaces the root handlers and starts a listener thread."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    logging_config.shutdown_logging()
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.mark.parametrize(
    ("log_format", "formatter"),
    [
        ("json", "JSONFormatter"),
        ("JSON", "JSONFormatter"),
        ("Json", "JSONFormatter"),
        ("console", "ConsoleFormatter"),
        ("CONSOLE", "ConsoleFormatter"),
    ],
)
def test_configure_logging_format_is_case_insensitive(restored_root_logger, log_format, formatter):
    listener = logging_config.configure_logging(log_level="INFO", log_format=log_format)

    assert type(listener.handlers[0].formatter).__name__ == formatter


def test_configure_logging_rejects_unknown_format(restored_root_logger):
    with pytest.raises(ValueError, match="LOG_FORMAT"):
        logging_config.configure_logging(log_level="INFO", log_format="jsno")


# ============================================================================
# Provenance defaults: validated against the caps of the models they are copied into
# ============================================================================

def _model_provenance_caps():
    """Collect, per provenance setting, the max_length every model field enforces."""
    caps: dict[str, set[int]] = {}
    for module_info in pkgutil.iter_modules(models_package.__path__):
        module = importlib.import_module(f"app.models.{module_info.name}")
        for _, model in inspect.getmembers(module, inspect.isclass):
            if not issubclass(model, BaseModel) or model.__module__ != module.__name__:
                continue
            for field_name, setting_name in PROVENANCE_ENV_MAP.items():
                field = model.model_fields.get(field_name)
                if field is None:
                    continue
                limits = {m.max_length for m in field.metadata if hasattr(m, "max_length")}
                caps.setdefault(setting_name, set()).update(limits)
    return caps


def test_provenance_limits_match_what_the_models_accept():
    caps = _model_provenance_caps()

    assert set(PROVENANCE_MAX_LENGTHS) == set(PROVENANCE_ENV_MAP.values())
    for setting_name, limit in PROVENANCE_MAX_LENGTHS.items():
        assert caps[setting_name] == {limit}, f"{setting_name} cap disagrees with the models"


@pytest.mark.parametrize("setting", sorted(PROVENANCE_MAX_LENGTHS))
def test_overlong_provenance_default_fails_startup_with_a_clear_error(monkeypatch, setting):
    limit = PROVENANCE_MAX_LENGTHS[setting]

    with pytest.raises(ValidationError, match=f"{setting}.*at most {limit} characters"):
        _settings(monkeypatch, **{setting: "x" * (limit + 1)})


@pytest.mark.parametrize("setting", sorted(PROVENANCE_MAX_LENGTHS))
def test_provenance_default_at_the_cap_is_accepted(monkeypatch, setting):
    limit = PROVENANCE_MAX_LENGTHS[setting]

    assert getattr(_settings(monkeypatch, **{setting: "x" * limit}), setting) == "x" * limit


# ============================================================================
# Root service-info document
# ============================================================================

async def test_root_endpoint_only_advertises_routes_that_exist():
    transport = httpx.ASGITransport(app=main.mcp.http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        info = (await client.get("/")).json()
        advertised = [info["endpoints"][name] for name in info["endpoints"]]
        advertised += [value for value in info.values() if isinstance(value, str) and value.startswith("/")]
        statuses = {path: (await client.get(path)).status_code for path in advertised}

    assert "/docs" not in advertised
    assert "docs" not in info
    assert statuses
    assert all(status != 404 for status in statuses.values()), statuses
