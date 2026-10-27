"""What the remote embedding adapters say and send, word for word.

Operators read the start-up and failure lines in the log and the error texts of a refused configuration or answer; the
providers receive one request per text. Each adapter runs on its real client library against the in-process server of
``tests/embedding_provider_server.py``, so the request is what the library really puts on the wire.
"""
import logging
from types import SimpleNamespace

import httpx
import pytest

from app.config.settings import settings
from app.exceptions import EmbeddingUnavailableError
from app.repositories.embeddings import embedding_adapter as ea
from tests.embedding_provider_server import (
    PROVIDERS,
    FakeEmbeddingProvider,
    adapter_for,
)

WIDTH = 4
MODEL = "provider/model-x"
VECTOR = [0.5, -0.25, 1.0, 0.0]
AZURE_FIELDS = ("AZURE_ENDPOINT", "AZURE_API_KEY", "AZURE_API_VERSION", "AZURE_DEPLOYMENT")
STANDARD_ATTRIBUTES = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


@pytest.fixture(autouse=True)
def configured(monkeypatch, caplog):
    """A distinctive model and a narrow width, so that a dropped or defaulted setting shows in what is sent."""
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", WIDTH)
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", MODEL)
    monkeypatch.setattr(settings, "EMBEDDING_REQUEST_TIMEOUT_SECONDS", 17.0)
    monkeypatch.setattr(ea.logger, "disabled", False)  # the logging set-up of an app that started earlier disables it
    with caplog.at_level(logging.INFO, logger=ea.logger.name):
        yield


def extras(record: logging.LogRecord) -> dict:
    """The fields a call added to the record with ``extra=``."""
    return {name: value for name, value in record.__dict__.items() if name not in STANDARD_ATTRIBUTES}


def records(caplog, message: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == message]


def set_azure(monkeypatch, endpoint: str = "https://my-resource.openai.azure.com/") -> None:
    for ambient in ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "OPENAI_API_VERSION", "OPENAI_BASE_URL"):
        monkeypatch.delenv(ambient, raising=False)
    for name, value in zip(AZURE_FIELDS, (endpoint, "test-key", "2023-05-15", "my-deployment"), strict=True):
        monkeypatch.setattr(settings, name, value)


# --------------------------------------------------------------------------
# The line an operator reads when an adapter starts
# --------------------------------------------------------------------------
def test_azure_start_up_line_names_the_deployment_setting(monkeypatch, caplog):
    set_azure(monkeypatch)
    monkeypatch.setattr(settings, "AZURE_SUPPORTS_DIMENSIONS", False)

    ea.AzureOpenAIAdapter()

    (record,) = records(caplog, "Initialising Azure Open AI embeddings adapter")
    assert record.levelno == logging.INFO
    assert extras(record) == {"embedding_model": MODEL, "supports_dimensions": False}


def test_google_start_up_line_names_the_model(monkeypatch, caplog):
    monkeypatch.setattr(settings, "GOOGLE_AI_API_KEY", "test-key")

    ea.GoogleEmbeddingsAdapter()

    (record,) = records(caplog, "Initialising Google Generative AI embeddings adapter")
    assert record.levelno == logging.INFO
    assert extras(record) == {"embedding_model": MODEL}


@pytest.mark.parametrize(("base_url", "shown"), [
    ("http://llm.internal:8080/v1", "http://llm.internal:8080/v1"),
    ("", "(default)"),
])
@pytest.mark.parametrize("supports_dimensions", [True, False])
def test_openai_start_up_line_names_the_model_endpoint_and_dimensions_setting(
    monkeypatch, caplog, base_url, shown, supports_dimensions,
):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", base_url)
    monkeypatch.setattr(settings, "OPENAI_SUPPORTS_DIMENSIONS", supports_dimensions)

    ea.OpenAIEmbeddingsAdapter()

    (record,) = records(caplog, "Initialising OpenAI embeddings adapter")
    assert record.levelno == logging.INFO
    assert extras(record) == {"embedding_model": MODEL, "base_url": shown, "supports_dimensions": supports_dimensions}


def test_ollama_start_up_line_names_the_model_and_endpoint(monkeypatch, caplog):
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://ollama.internal:11434")

    ea.OllamaEmbeddingsAdapter()

    (record,) = records(caplog, "Initialising Ollama embeddings adapter")
    assert record.levelno == logging.INFO
    assert extras(record) == {"embedding_model": MODEL, "base_url": "http://ollama.internal:11434"}


# --------------------------------------------------------------------------
# What a refused configuration says
# --------------------------------------------------------------------------
def test_azure_names_every_setting_it_needs_and_every_one_that_is_missing(monkeypatch):
    set_azure(monkeypatch)
    for name in AZURE_FIELDS:
        monkeypatch.setattr(settings, name, "")

    with pytest.raises(ValueError) as refused:
        ea.AzureOpenAIAdapter()

    assert str(refused.value) == (
        "AZURE_ENDPOINT, AZURE_API_KEY, AZURE_API_VERSION and AZURE_DEPLOYMENT must be configured for "
        "AzureOpenAIAdapter; missing: AZURE_ENDPOINT, AZURE_API_KEY, AZURE_API_VERSION, AZURE_DEPLOYMENT"
    )


def test_azure_without_a_key_names_only_the_key(monkeypatch):
    set_azure(monkeypatch)
    monkeypatch.setattr(settings, "AZURE_API_KEY", " ")

    with pytest.raises(ValueError) as refused:
        ea.AzureOpenAIAdapter()

    assert str(refused.value).endswith("must be configured for AzureOpenAIAdapter; missing: AZURE_API_KEY")


def test_openai_without_a_key_or_an_endpoint_says_which_setting_to_set(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "")

    with pytest.raises(ValueError) as refused:
        ea.OpenAIEmbeddingsAdapter()

    assert str(refused.value) == "OPENAI_API_KEY must be configured for OpenAIEmbeddingsAdapter"


def test_openai_without_a_key_but_with_an_endpoint_sends_a_placeholder_key(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "http://llm.internal:8080/v1")

    adapter = ea.OpenAIEmbeddingsAdapter()

    assert adapter.client.api_key == "no-key-required"


def test_google_without_a_key_says_which_setting_to_set(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_AI_API_KEY", "")

    with pytest.raises(ValueError) as refused:
        ea.GoogleEmbeddingsAdapter()

    assert str(refused.value) == "GOOGLE_AI_API_KEY must be configured for GoogleEmbeddingsAdapter"


# --------------------------------------------------------------------------
# How each client library is set up from the settings
# --------------------------------------------------------------------------
def test_azure_client_is_built_from_the_four_settings_and_the_timeout(monkeypatch):
    set_azure(monkeypatch)
    monkeypatch.setattr(settings, "AZURE_SUPPORTS_DIMENSIONS", False)

    adapter = ea.AzureOpenAIAdapter()

    assert adapter.client.api_key == "test-key"
    assert adapter.client._api_version == "2023-05-15"
    assert str(adapter.client._azure_endpoint) == "https://my-resource.openai.azure.com/"
    assert adapter.client.timeout == 17.0
    assert adapter.model == "my-deployment"
    assert adapter.supports_dimensions is False


def test_openai_client_is_built_from_the_settings(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "http://llm.internal:8080/v1")
    monkeypatch.setattr(settings, "OPENAI_SUPPORTS_DIMENSIONS", False)

    adapter = ea.OpenAIEmbeddingsAdapter()

    assert adapter.client.api_key == "sk-test"
    assert str(adapter.client.base_url) == "http://llm.internal:8080/v1/"
    assert adapter.client.timeout == 17.0
    assert adapter.model == MODEL
    assert adapter.supports_dimensions is False


def test_openai_client_keeps_the_library_endpoint_when_no_base_url_is_set(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://ambient.invalid/v1")  # the library would pick this up, but only if none is passed
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "")

    adapter = ea.OpenAIEmbeddingsAdapter()

    assert str(adapter.client.base_url) == "http://ambient.invalid/v1/"


def test_ollama_client_is_built_from_the_settings(monkeypatch):
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://ollama.internal:11434")

    adapter = ea.OllamaEmbeddingsAdapter()

    assert str(adapter.client._client.base_url) == "http://ollama.internal:11434"
    assert adapter.client._client.timeout == httpx.Timeout(17.0)
    assert adapter.model == MODEL


@pytest.mark.parametrize(("seconds", "milliseconds"), [(17.0, 17000), (2.5, 2500), (0.0001, 1)])
def test_google_client_gets_the_timeout_in_milliseconds_and_never_less_than_one(monkeypatch, seconds, milliseconds):
    monkeypatch.setattr(settings, "GOOGLE_AI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "EMBEDDING_REQUEST_TIMEOUT_SECONDS", seconds)

    adapter = ea.GoogleEmbeddingsAdapter()

    assert adapter.client._api_client.api_key == "test-key"
    assert adapter.client._api_client._http_options.timeout == milliseconds
    assert adapter.model == MODEL


# --------------------------------------------------------------------------
# What each provider is sent
# --------------------------------------------------------------------------
@pytest.mark.parametrize(("provider", "path", "body"), [
    ("Azure", "/openai/deployments/text-embedding-ada-002/embeddings?api-version=2023-05-15",
     {"input": ["hello"], "model": "text-embedding-ada-002", "dimensions": WIDTH}),
    ("Google", f"/v1beta/models/{MODEL}:batchEmbedContents",
     {"requests": [{"content": {"parts": [{"text": "hello"}], "role": "user"}, "outputDimensionality": WIDTH,
                    "model": f"models/{MODEL}"}]}),
    ("OpenAI", "/v1/embeddings", {"input": ["hello"], "model": MODEL, "dimensions": WIDTH}),
    ("Ollama", "/api/embed", {"input": "hello", "model": MODEL, "dimensions": WIDTH}),
])
async def test_each_provider_is_asked_for_one_text_with_the_model_and_the_configured_width(
    provider, path, body, monkeypatch,
):
    with FakeEmbeddingProvider(VECTOR) as server:
        adapter = adapter_for(provider, server, monkeypatch)

        assert await adapter.generate_embedding("hello") == VECTOR

    [(sent_path, sent)] = server.requests
    assert sent_path == path
    assert {name: value for name, value in sent.items() if name != "encoding_format"} == body


@pytest.mark.parametrize(("provider", "setting"), [
    ("Azure", "AZURE_SUPPORTS_DIMENSIONS"), ("OpenAI", "OPENAI_SUPPORTS_DIMENSIONS"),
])
async def test_the_width_is_left_out_of_a_request_to_a_deployment_that_does_not_take_it(provider, setting, monkeypatch):
    with FakeEmbeddingProvider(VECTOR) as server:
        adapter = adapter_for(provider, server, monkeypatch)
        monkeypatch.setattr(settings, setting, False)
        adapter.supports_dimensions = False

        await adapter.generate_embedding("hello")

    [(_, sent)] = server.requests
    assert "dimensions" not in sent


# --------------------------------------------------------------------------
# The line an operator reads when a provider refuses a request
# --------------------------------------------------------------------------
@pytest.mark.parametrize(("provider", "message", "fields"), [
    ("Azure", "Error generating embeddings", {"embedding provider": "Azure", "embedding model": "text-embedding-ada-002"}),
    ("Google", "Error generating embeddings", {"embedding_provider": "google", "embedding_model": MODEL}),
    ("OpenAI", "Error generating embeddings", {"embedding_provider": "openai", "embedding_model": MODEL}),
    ("Ollama", "Error generating embeddings", {"embedding_provider": "ollama", "embedding_model": MODEL}),
])
async def test_a_refused_request_is_logged_with_its_traceback_and_raised_unchanged(
    provider, message, fields, monkeypatch, caplog,
):
    with FakeEmbeddingProvider(VECTOR, status=400) as server:
        adapter = adapter_for(provider, server, monkeypatch)

        with pytest.raises(Exception) as raised:  # noqa: PT011 - each library has its own error type
            await adapter.generate_embedding("hello")

    (record,) = records(caplog, message)
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert record.exc_info[1] is raised.value
    assert extras(record) == fields


# --------------------------------------------------------------------------
# What an answer without a vector, or with a vector of the wrong width, says
# --------------------------------------------------------------------------
def test_a_vector_of_another_width_is_refused_with_the_whole_instruction():
    adapter = ea.OllamaEmbeddingsAdapter()

    with pytest.raises(EmbeddingUnavailableError) as refused:
        adapter._checked([0.5] * 8)

    assert str(refused.value) == (
        "Ollama returned 8-dimensional embeddings but EMBEDDING_DIMENSIONS is 4. "
        "Set EMBEDDING_DIMENSIONS=8, or choose a model that produces 4; memories already stored need `--re-embed` "
        "after a change (see docs/embedding_migration.md)."
    )


def test_a_vector_with_a_number_that_is_not_finite_is_refused_naming_the_provider():
    adapter = ea.OllamaEmbeddingsAdapter()

    with pytest.raises(RuntimeError) as refused:
        adapter._checked([0.5, float("nan"), 0.5, 0.5])

    assert str(refused.value) == "Ollama returned a non-finite number in the embedding"


async def test_openai_answer_without_a_response_object_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    adapter = ea.OpenAIEmbeddingsAdapter()
    adapter.client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **_request: None))

    with pytest.raises(RuntimeError) as refused:
        await adapter.generate_embedding("hello")

    assert str(refused.value) == "OpenAI response did not contain embedding vector"


@pytest.mark.parametrize("answer", [None, SimpleNamespace(embeddings=None), SimpleNamespace(embeddings=[])],
                         ids=["no-response", "no-embeddings", "empty-embeddings"])
async def test_ollama_answer_without_a_vector_is_reported(answer):
    adapter = ea.OllamaEmbeddingsAdapter()

    async def embed(**_request):
        return answer

    adapter.client = SimpleNamespace(embed=embed)

    with pytest.raises(RuntimeError) as refused:
        await adapter.generate_embedding("hello")

    assert str(refused.value) == "Ollama did not return embedding vector"


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_a_batch_is_one_request_per_text_in_order(provider, monkeypatch):
    with FakeEmbeddingProvider(VECTOR) as server:
        adapter = adapter_for(provider, server, monkeypatch)

        await adapter.generate_embeddings(["first", "second"])

    texts = [
        body["input"] if "input" in body else body["requests"][0]["content"]["parts"][0]["text"]
        for _, body in server.requests
    ]
    assert [text if isinstance(text, str) else text[0] for text in texts] == ["first", "second"]
