# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

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
from dataclasses import replace
from types import SimpleNamespace

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
from app.exceptions import TensorRTError
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
        ("EMBEDDING_PROVIDER", " tensorrt ", "TensorRT"),
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
        ("EMBEDDING_PROVIDER", "FastEmbed"),  # the CPU FastEmbed provider no longer exists
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
        ("gateway", "GatewayEmbeddingsAdapter"),
        ("GATEWAY", "GatewayEmbeddingsAdapter"),
    ],
)
def test_embedding_factory_selects_provider_ignoring_case(monkeypatch, fake_adapters, value, adapter):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", value)

    assert type(bootstrap.get_embedding_adapter()).__name__ == adapter


@pytest.mark.parametrize("value", ["TensorRT", "tensorrt", "TENSORRT", ""])
def test_tensorrt_embedding_factory_returns_the_shared_engine_ignoring_case(monkeypatch, tensorrt_embedder, value):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", value)  # blank means the default, which is TensorRT

    assert bootstrap.get_embedding_adapter() is tensorrt_embedder
    assert bootstrap.get_embedding_adapter() is bootstrap.get_embedding_adapter()


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
    monkeypatch.setattr(bootstrap, "_TENSORRT_ADAPTERS", {})
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKING_PROVIDER", "TensorRT")
    monkeypatch.setattr(settings, "RERANKING_WORKERS", 2)  # another cache key: a second adapter, same cached engine

    second = bootstrap.get_reranker_adapter()
    assert second is not tensorrt_reranker
    assert len(second.score("query", ["document"])) == 1

    bootstrap._release_tensorrt_adapters()

    assert bootstrap._TENSORRT_ADAPTERS == {}
    with pytest.raises(TensorRTError, match="closed"):
        second.score("query", ["document"])


def test_shared_embedders_are_closed_when_the_process_releases_them(monkeypatch, tensorrt_embedder):
    monkeypatch.setattr(bootstrap, "_TENSORRT_ADAPTERS", {})
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "TensorRT")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL_DIR", settings.EMBEDDING_MODEL_DIR + "/")  # another key, same files

    second = bootstrap.get_embedding_adapter()
    assert second is not tensorrt_embedder
    assert len(second.embed(["document"])) == 1

    bootstrap._release_tensorrt_adapters()

    assert bootstrap._TENSORRT_ADAPTERS == {}
    with pytest.raises(TensorRTError, match="closed"):
        second.embed(["document"])


def test_both_tensorrt_models_are_logged_with_the_memory_and_context_they_share(
    tensorrt_embedder, tensorrt_reranker, caplog,
):
    caplog.set_level(logging.INFO, logger="app.bootstrap")

    bootstrap.log_tensorrt_models(tensorrt_embedder, tensorrt_reranker, None)

    messages = [record.getMessage() for record in caplog.records]
    assert messages[0] == tensorrt_embedder.startup.summary()
    assert messages[1] == tensorrt_reranker.startup.summary()
    combined = messages[2]
    assert "GPU memory with the embedder and reranker loaded: this process holds" in combined
    assert f"one CUDA context ({tensorrt_embedder.startup.cuda_context:#x}) shared by the embedder and reranker" in combined
    assert len(messages) == 3


def test_models_on_different_cuda_contexts_are_reported_as_a_warning(tensorrt_embedder, tensorrt_reranker, caplog):
    other = SimpleNamespace(startup=replace(tensorrt_reranker.startup, cuda_context=1))
    caplog.set_level(logging.INFO, logger="app.bootstrap")

    bootstrap.log_tensorrt_models(tensorrt_embedder, other)

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert [record.getMessage() for record in warnings] == [
        "2 different CUDA contexts for the embedder and reranker, which should not happen",
    ]


def test_providers_without_a_gpu_have_nothing_to_log(caplog):
    caplog.set_level(logging.INFO, logger="app.bootstrap")

    bootstrap.log_tensorrt_models(object(), None)

    assert caplog.records == []


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


def test_logging_while_starting_shows_records_on_stderr_and_leaves_the_root_logger_as_it_was(capsys):
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    root.setLevel(logging.WARNING)  # what an unconfigured process has, so INFO is dropped
    try:
        logging.getLogger("startup.test").info("before: dropped")
        with logging_config.logging_while_starting("INFO", "console"):
            logging.getLogger("startup.test").info("while starting: shown, password=hunter2")
        logging.getLogger("startup.test").info("after: dropped")

        assert root.handlers == handlers
        assert root.level == logging.WARNING
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)

    err = capsys.readouterr().err
    assert "while starting: shown" in err
    assert "hunter2" not in err  # the same masking as the configured handler
    assert "dropped" not in err


def test_logging_while_starting_restores_the_root_logger_when_the_block_fails():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level

    with pytest.raises(RuntimeError, match="boom"), logging_config.logging_while_starting("DEBUG", "json"):
        raise RuntimeError("boom")

    assert root.handlers == handlers
    assert root.level == level


def test_logging_while_starting_rejects_an_unknown_format():
    with pytest.raises(ValueError, match="LOG_FORMAT"), logging_config.logging_while_starting("INFO", "jsno"):
        pytest.fail("the block must not run")


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
